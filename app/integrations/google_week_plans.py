import hashlib
import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from app.core.constants import GOOGLE_WEEK, TIME, Marketplace, WeekIssue
from app.core.stores import PROJECT_STORE_ALIASES
from app.dto.google_week import (
    CellWrite,
    ExportPlan,
    FormulaBackup,
    FreezePlan,
    FreezeSheetResult,
    GoogleDocument,
    GoogleSheet,
    GridCell,
    GridRange,
    HeaderCell,
    HeaderMatch,
    HeaderSource,
    HeaderStop,
    MatchedProduct,
    Product,
    ProductIssue,
    SnapshotTime,
    StoreFacts,
    WeekError,
    WeekPeriod,
    WeekProfile,
    WeekReport,
)


def position(cell: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", cell)
    if match is None:
        raise WeekError(WeekIssue.TARGET, cell=cell)
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - ord("A") + 1
    return int(match[2]) - 1, column - 1


def address(row: int, column: int) -> str:
    letters = ""
    column += 1
    while column:
        column, rest = divmod(column - 1, 26)
        letters = chr(65 + rest) + letters
    return f"{letters}{row + 1}"


def week_start(value: str) -> date | None:
    match = re.fullmatch(r"W([0-9]{1,2}) ([0-9]{4})", value)
    if match:
        try:
            return date.fromisocalendar(int(match[2]), int(match[1]), 1)
        except ValueError:
            pass
    return None


def current_week(now: datetime) -> str:
    today = now.astimezone(TIME.moscow_timezone).date()
    year, number, _ = (today - timedelta(days=today.weekday() + 1)).isocalendar()
    return f"W{number:02d} {year}"


def cells(sheet: GoogleSheet) -> dict[tuple[int, int], GridCell]:
    return {
        (r, c): cell
        for block in sheet.data
        for r, row in enumerate(block.rowData, block.startRow)
        for c, cell in enumerate(row.values, block.startColumn)
    }


def display(cell: GridCell) -> str:
    if cell.formattedValue:
        return cell.formattedValue
    value = cell.effectiveValue
    if value.stringValue is not None:
        return value.stringValue
    if value.numberValue is not None:
        return str(int(value.numberValue)) if value.numberValue.is_integer() else str(value.numberValue)
    return ""


def normalized(value: str) -> str:
    return " ".join(value.split())


def source_values(profile: WeekProfile, sheet: GoogleSheet) -> list[HeaderCell]:
    grid = cells(sheet)
    values = [
        HeaderCell(cell=cell, value=display(grid.get(position(cell), GridCell())))
        for cell in profile.settings.cells
    ]
    if any(week_start(normalized(item.value)) is None for item in values):
        raise WeekError(WeekIssue.WEEK_MISSING)
    return values


def headers(
    profile: WeekProfile, sheet: GoogleSheet, origins: list[HeaderCell], *, stock: bool = False
) -> WeekReport:
    grid = cells(sheet)
    sources = []
    row_headers: dict[int, dict[str, list[HeaderCell]]] = {}
    for (r, c), entry in grid.items():
        value = normalized(display(entry))
        for name in GOOGLE_WEEK.identity_headers:
            if value.casefold() == name.casefold():
                row_headers.setdefault(r, {}).setdefault(name, []).append(
                    HeaderCell(cell=address(r, c), value=value)
                )
    for origin in origins:
        source = HeaderSource(cell=origin.cell, value=origin.value)
        if week_start(normalized(origin.value)) is None:
            source.issue = WeekIssue.WEEK_MISSING
            sources.append(source)
            continue
        for (r, c), entry in grid.items():
            if normalized(display(entry)) != normalized(origin.value):
                continue
            identity = row_headers.get(r, {})
            if (
                not stock
                and address(r, c) == origin.cell
                and not all(name in identity for name in GOOGLE_WEEK.identity_headers)
            ):
                continue
            found = []
            stop = None
            for col in range(c, c - 1 if stock else -1, -1):
                value = display(grid.get((r, col), GridCell()))
                if week_start(normalized(value)) is None:
                    stop = HeaderStop(cell=address(r, col), value=value)
                    break
                found.append(HeaderCell(cell=address(r, col), value=value))
            if found:
                source.matches.append(
                    HeaderMatch(
                        cell=address(r, c),
                        range=f"{found[-1].cell}:{found[0].cell}",
                        row=r + 1,
                        headers=found,
                        columns=identity,
                        stop=stop,
                    )
                )
        sources.append(source)
    return WeekReport(
        marketplace=profile.marketplace,
        spreadsheet_url=profile.settings.spreadsheet_url,
        sheet_name=sheet.properties.title,
        sheet_id=sheet.properties.sheetId,
        source_sheet_name=profile.settings.sheet_name,
        source_cells=list(profile.settings.cells),
        sources=sources,
        total_matches=sum(len(source.matches) for source in sources),
        total_cells=len({h.cell for source in sources for match in source.matches for h in match.headers}),
    )


def layout(report: WeekReport) -> HeaderMatch:
    found: dict[str, HeaderCell] = {}
    matches = []
    for source in report.sources:
        valid = [
            match
            for match in source.matches
            if all(len(match.columns.get(name, [])) == 1 for name in GOOGLE_WEEK.identity_headers)
        ]
        if source.issue or not valid:
            raise WeekError(WeekIssue.HEADERS, cell=source.cell)
        matches.extend(valid)
        for match in valid:
            for header in match.headers:
                found[header.cell] = header
    if not matches or len({match.row for match in matches}) != 1:
        raise WeekError(WeekIssue.AMBIGUOUS)
    return matches[0].model_copy(update={"headers": list(found.values())})


def match_products(
    sheet: GoogleSheet, header: HeaderMatch, products: list[Product], report: WeekReport
) -> list[MatchedProduct]:
    index: dict[tuple[str, str], set[str]] = defaultdict(set)
    for product in products:
        for alias in (product.article, *product.aliases):
            index[product.store_slug, alias.strip()].add(product.article)
    grid = cells(sheet)
    article_col = position(header.columns[GOOGLE_WEEK.identity_headers[0]][0].cell)[1]
    project_col = position(header.columns[GOOGLE_WEEK.identity_headers[1]][0].cell)[1]
    found = []
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in range(header.row, max((r for r, _ in grid), default=header.row - 1) + 1):
        article = display(grid.get((row, article_col), GridCell())).strip()
        project = normalized(display(grid.get((row, project_col), GridCell())))
        if not article and not project:
            continue
        store = PROJECT_STORE_ALIASES.get(project.casefold())
        candidates = index.get((store or "", article), set())
        if not article or not project or store is None or len(candidates) != 1:
            report.issues.append(
                ProductIssue(
                    row=row + 1,
                    article=article,
                    project=project,
                    reason=WeekIssue.PROJECT if store is None else WeekIssue.PRODUCT,
                )
            )
            continue
        canonical = next(iter(candidates))
        found.append(MatchedProduct(store_slug=store, article=canonical, row=row + 1, project=project))
        counts[store, canonical] += 1
    result = []
    for product in found:
        if counts[product.store_slug, product.article] > 1:
            report.issues.append(
                ProductIssue(
                    row=product.row,
                    article=product.article,
                    project=product.project,
                    reason=WeekIssue.DUPLICATE,
                )
            )
        else:
            result.append(product)
    report.product_rows = len(result) + len(report.issues)
    report.matched_products = len(result)
    return result


def periods(header: HeaderMatch) -> list[WeekPeriod]:
    result = []
    for cell in header.headers:
        start = week_start(normalized(cell.value))
        if start is None:
            raise WeekError(WeekIssue.WEEK_MISSING, cell=cell.cell)
        result.append(
            WeekPeriod(
                cell=cell.cell,
                value=cell.value,
                date_from=start,
                date_to=start + timedelta(days=GOOGLE_WEEK.days_per_week - 1),
            )
        )
    return result


def export_plan(
    profile: WeekProfile,
    report: WeekReport,
    matched: list[MatchedProduct],
    facts: dict[str, StoreFacts],
    *,
    now: datetime,
    stock: bool,
) -> ExportPlan:
    plan = ExportPlan(report=report, writes=[])
    today = now.astimezone(TIME.moscow_timezone).date()
    captured: dict[tuple[str, date, datetime], SnapshotTime] = {}
    indexes = {
        store: {(fact.article, fact.day): fact for fact in source.facts} for store, source in facts.items()
    }
    for product in matched:
        source = facts[product.store_slug]
        if source.error:
            report.issues.append(
                ProductIssue(
                    row=product.row, article=product.article, project=product.project, reason=WeekIssue.SOURCE
                )
            )
            continue
        for period in report.periods:
            cell = address(product.row - 1, position(period.cell)[1])
            issue = ProductIssue(
                row=product.row,
                article=product.article,
                project=product.project,
                cell=cell,
                week=period.value,
            )
            if period.date_to >= today:
                issue.reason = WeekIssue.FUTURE
                issue.missing_days = [period.date_to]
                report.missing_data.append(issue)
                continue
            days = (
                [period.date_to]
                if stock
                else [period.date_from + timedelta(days=i) for i in range(GOOGLE_WEEK.days_per_week)]
            )
            quantity = 0
            for day in days:
                fact = indexes[product.store_slug].get((product.article, day))
                if fact and fact.quantity is not None:
                    quantity += fact.quantity
                    if stock and fact.captured_at:
                        captured[product.store_slug, day, fact.captured_at] = SnapshotTime(
                            store_slug=product.store_slug, day=day, captured_at=fact.captured_at
                        )
                    if not stock and profile.marketplace != Marketplace.WB and day not in source.confirmed:
                        issue.missing_days.append(day)
                elif stock or fact is not None or day not in source.confirmed:
                    issue.missing_days.append(day)
            if issue.missing_days:
                issue.reason = WeekIssue.MISSING_DATA
                report.zero_filled.append(issue)
            plan.writes.append(CellWrite(cell=cell, value=quantity))
    report.complete = (
        bool(matched) and not report.issues and not report.missing_data and not report.zero_filled
    )
    report.snapshot_times = list(captured.values())
    report.processed_at = now
    return plan


def contains(region: GridRange, row: int, col: int, sheet: GoogleSheet) -> bool:
    bounds = sheet.properties.gridProperties
    return region.startRowIndex <= row < (
        region.endRowIndex if region.endRowIndex is not None else bounds.rowCount
    ) and region.startColumnIndex <= col < (
        region.endColumnIndex if region.endColumnIndex is not None else bounds.columnCount
    )


def check_targets(
    profile: WeekProfile, sheet: GoogleSheet, writes: list[CellWrite], *, number: bool = False
) -> None:
    bounds = sheet.properties.gridProperties
    for write in writes:
        row, col = position(write.cell)
        if (
            row >= bounds.rowCount
            or col >= bounds.columnCount
            or (
                not number
                and sheet.properties.title == profile.settings.sheet_name
                and write.cell in profile.settings.cells
            )
        ):
            raise WeekError(WeekIssue.TARGET, cell=write.cell)
        for merge in sheet.merges:
            if contains(merge, row, col, sheet) and not (
                number and (row, col) == (merge.startRowIndex, merge.startColumnIndex)
            ):
                raise WeekError(WeekIssue.MERGED, cell=write.cell)


def freeze_plan(
    profile: WeekProfile, sheet: GoogleSheet, document: GoogleDocument, wanted: str
) -> FreezePlan:
    plan = FreezePlan(
        report=FreezeSheetResult(sheet_id=sheet.properties.sheetId, sheet_name=sheet.properties.title)
    )
    try:
        grid = cells(sheet)
        labels = {pos: display(cell) for pos, cell in grid.items()}
        bounds = sheet.properties.gridProperties
        if sheet.properties.sheetType != "GRID" or not bounds.rowCount or not bounds.columnCount:
            raise WeekError(WeekIssue.TARGET)
        identities: dict[int, set[str]] = defaultdict(set)
        for (row, _), label in labels.items():
            if label.strip().upper() in GOOGLE_WEEK.freeze_headers:
                identities[row].add(label.strip().upper())
        rows = {row for row, names in identities.items() if names == GOOGLE_WEEK.freeze_headers}
        if not rows:
            raise WeekError(WeekIssue.HEADERS)
        anchors = sorted(
            pos
            for pos, label in labels.items()
            if pos[0] in rows and pos[1] >= GOOGLE_WEEK.freeze_anchor_column and label == wanted
        )
        if not anchors:
            raise WeekError(WeekIssue.WEEK_MISSING)
        target = week_start(wanted)
        assert target is not None
        spans: dict[tuple[int, int], list[int]] = defaultdict(list)
        signatures: dict[tuple[int, int], list[str]] = {}
        for row, anchor in anchors:
            start, previous = anchor, target
            for col in range(anchor - 1, -1, -1):
                period = week_start(labels.get((row, col), ""))
                if period is None:
                    break
                if period >= previous:
                    raise WeekError(WeekIssue.AMBIGUOUS)
                start, previous = col, period
            if start == anchor:
                continue
            span = start, anchor
            if any(start < right and left < anchor and span != (left, right) for left, right in spans):
                raise WeekError(WeekIssue.AMBIGUOUS)
            signature = [labels[row, col] for col in range(start, anchor + 1)]
            if span in signatures and signatures[span] != signature:
                raise WeekError(WeekIssue.AMBIGUOUS)
            signatures[span] = signature
            spans[span].append(row)
        if not spans:
            raise WeekError(WeekIssue.WEEK_MISSING)
        for (left, right), header_rows in spans.items():
            ordered = sorted(header_rows)
            for index, row in enumerate(ordered):
                bottom = ordered[index + 1] if index + 1 < len(ordered) else bounds.rowCount
                if row + 1 < bottom:
                    plan.ranges.append(
                        GridRange(
                            sheetId=sheet.properties.sheetId,
                            startRowIndex=row + 1,
                            endRowIndex=bottom,
                            startColumnIndex=left,
                            endColumnIndex=right,
                        )
                    )
        plan.report.ranges = [
            f"{address(r.startRowIndex, r.startColumnIndex)}:{address(edges(r, sheet)[1] - 1, edges(r, sheet)[3] - 1)}"
            for r in plan.ranges
        ]

        def inside(row: int, col: int) -> bool:
            return any(contains(region, row, col, sheet) for region in plan.ranges)

        for pos, label in labels.items():
            period = week_start(label)
            if inside(*pos) and period and period >= target:
                raise WeekError(WeekIssue.TARGET)
        if sheet.properties.title == profile.settings.sheet_name and any(
            inside(*position(cell)) for cell in profile.settings.cells
        ):
            raise WeekError(WeekIssue.TARGET)
        for merge in sheet.merges:
            for region in plan.ranges:
                if intersects(region, merge, sheet) and not encloses(region, merge, sheet):
                    raise WeekError(WeekIssue.MERGED)
        named = {item.namedRangeId: item.range for item in document.namedRanges}
        for protection in sheet.protectedRanges:
            if protection.warningOnly or protection.requestingUserCanEdit:
                continue
            protected = protection.range or named.get(protection.namedRangeId)
            if protected is None:
                raise WeekError(WeekIssue.PROTECTED)
            for region in plan.ranges:
                if protection_blocks(region, protected, protection.unprotectedRanges, sheet):
                    raise WeekError(WeekIssue.PROTECTED)
        leftmost_formula = bounds.columnCount
        for (row, col), cell in sorted(grid.items()):
            if inside(row, col):
                if cell.effectiveValue.errorValue or any(
                    value is not None
                    for value in (cell.pivotTable, cell.dataSourceTable, cell.dataSourceFormula)
                ):
                    raise WeekError(WeekIssue.FORMULA)
                if cell.userEnteredValue.formulaValue is not None:
                    if all(
                        value is None
                        for value in (
                            cell.effectiveValue.numberValue,
                            cell.effectiveValue.stringValue,
                            cell.effectiveValue.boolValue,
                        )
                    ):
                        raise WeekError(WeekIssue.FORMULA)
                    leftmost_formula = min(leftmost_formula, col)
                    plan.formulas.append(
                        FormulaBackup(
                            cell=address(row, col),
                            previous=cell.userEnteredValue,
                            effective_value=cell.effectiveValue,
                        )
                    )
            if (
                cell.effectiveValue.model_dump(exclude_none=True)
                and not cell.userEnteredValue.model_dump(exclude_none=True)
                and (inside(row, col) or col >= leftmost_formula)
            ):
                raise WeekError(WeekIssue.ARRAY)
        plan.report.formula_count = len(plan.formulas)
        payload = {
            "properties": sheet.properties.model_dump(),
            "merges": [r.model_dump() for r in sheet.merges],
            "protections": [r.model_dump() for r in sheet.protectedRanges],
            "named": [r.model_dump() for r in document.namedRanges],
            "headers": [(key, value, signatures[key]) for key, value in spans.items()],
            "values": [
                (row, col, cell.userEnteredValue.model_dump())
                for (row, col), cell in sorted(grid.items())
                if inside(row, col)
            ],
        }
        plan.signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if not plan.formulas:
            plan.ranges = []
    except WeekError as error:
        plan.report.issue = error.problem.code
        plan.report.formula_count = 0
        plan.ranges, plan.formulas = [], []
    return plan


def edges(region: GridRange, sheet: GoogleSheet) -> tuple[int, int, int, int]:
    bounds = sheet.properties.gridProperties
    return (
        region.startRowIndex,
        region.endRowIndex if region.endRowIndex is not None else bounds.rowCount,
        region.startColumnIndex,
        region.endColumnIndex if region.endColumnIndex is not None else bounds.columnCount,
    )


def intersects(first: GridRange, second: GridRange, sheet: GoogleSheet) -> bool:
    top, bottom, left, right = edges(first, sheet)
    other_top, other_bottom, other_left, other_right = edges(second, sheet)
    return top < other_bottom and other_top < bottom and left < other_right and other_left < right


def encloses(first: GridRange, second: GridRange, sheet: GoogleSheet) -> bool:
    top, bottom, left, right = edges(first, sheet)
    other_top, other_bottom, other_left, other_right = edges(second, sheet)
    return top <= other_top and other_bottom <= bottom and left <= other_left and other_right <= right


def protection_blocks(
    region: GridRange, protected: GridRange, exceptions: list[GridRange], sheet: GoogleSheet
) -> bool:
    if not intersects(region, protected, sheet):
        return False
    a, b, c, d = edges(region, sheet)
    e, f, g, h = edges(protected, sheet)
    top, bottom, left, right = max(a, e), min(b, f), max(c, g), min(d, h)
    stripes = sorted(
        {top, bottom}
        | {max(top, min(bottom, edge)) for item in exceptions for edge in edges(item, sheet)[:2]}
    )
    for row_start, row_end in zip(stripes, stripes[1:], strict=False):
        covered = left
        intervals = sorted(
            (edges(item, sheet)[2], edges(item, sheet)[3])
            for item in exceptions
            if item.sheetId == protected.sheetId
            and item.startRowIndex <= row_start
            and edges(item, sheet)[1] >= row_end
        )
        for start, end in intervals:
            if start <= covered:
                covered = max(covered, end)
        if covered < right:
            return True
    return False
