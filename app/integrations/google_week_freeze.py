"""Freeze historical week columns with guarded, server-side value-only copies."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from app.config import settings as app_settings
from app.integrations import google_week_search as search
from app.integrations import google_week_update as week
from app.repositories import google_week_freeze as repository
from app.repositories import google_week_update as week_repository
from app.repositories.google_week_freeze import WeekFreezeState
from app.repositories.google_week_update import WeekUpdateSettings

_GRID_FIELDS = (
    "namedRanges(namedRangeId,range),"
    "sheets(properties(sheetId,title,sheetType,gridProperties),merges,"
    "protectedRanges(range,namedRangeId,warningOnly,requestingUserCanEdit,unprotectedRanges),"
    "data(startRow,startColumn,rowData(values(userEnteredValue,effectiveValue,formattedValue,"
    "pivotTable,dataSourceTable,dataSourceFormula))))"
)


@dataclass
class _Plan:
    report: dict
    requests: list[dict]
    formulas: list[dict]
    signature: dict


def is_due(settings: WeekUpdateSettings, *, now: datetime) -> bool:
    return week.update_is_due(search.schedule_settings(settings, repository.get_state()), now)


def result_matches_settings(result: dict, settings: WeekUpdateSettings, state: WeekFreezeState) -> bool:
    return (
        result.get("spreadsheet_url") == settings.spreadsheet_url
        and result.get("spreadsheet_id") == state.spreadsheet_id
        and result.get("sheet_ids") == list(state.sheet_ids)
        and result.get("source_sheet_name") == settings.sheet_name
        and result.get("source_cells") == list(settings.cells)
    )


def _week_start(value: object) -> date | None:
    match = re.fullmatch(r"W([0-9]{1,2}) ([0-9]{4})", str(value))
    if match:
        try:
            return date.fromisocalendar(int(match[2]), int(match[1]), 1)
        except ValueError:
            pass
    return None


def _cells(sheet: dict) -> dict[tuple[int, int], dict]:
    cells = {}
    for block in sheet.get("data", []):
        for r, row in enumerate(block.get("rowData", []), block.get("startRow", 0)):
            for c, cell in enumerate(row.get("values", []), block.get("startColumn", 0)):
                if cell:
                    cells[r, c] = cell
    return cells


def _display(cell: dict) -> str:
    # Do not normalize whitespace or week padding: the Actions label is matched verbatim.
    if "formattedValue" in cell:
        return cell["formattedValue"]
    return str(cell.get("effectiveValue", {}).get("stringValue", ""))


def _read_sheets(service, doc_id: str, sheet_ids: tuple[int, ...]) -> tuple[dict[int, dict], dict]:
    metadata = (
        service.spreadsheets()
        .get(spreadsheetId=doc_id, fields="sheets(properties(sheetId,title))")
        .execute(num_retries=2)
    )
    existing = {item["properties"]["sheetId"]: item for item in metadata.get("sheets", [])}
    titles = [existing[sid]["properties"]["title"] for sid in sheet_ids if sid in existing]
    if not titles:
        return {}, {}
    response = (
        service.spreadsheets()
        .get(
            spreadsheetId=doc_id,
            ranges=["'" + title.replace("'", "''") + "'" for title in titles],
            includeGridData=True,
            fields=_GRID_FIELDS,
        )
        .execute(num_retries=2)
    )
    sheets = {item["properties"]["sheetId"]: item for item in response.get("sheets", [])}
    named_ranges = {item["namedRangeId"]: item["range"] for item in response.get("namedRanges", [])}
    return sheets, named_ranges


def _inside(row: int, column: int, ranges: list[tuple[int, int, int, int]]) -> bool:
    return any(top <= row < bottom and left <= column < right for top, bottom, left, right in ranges)


def _intersects(grid: dict, ranges: list[tuple[int, int, int, int]], bounds: dict) -> bool:
    return any(
        grid.get("startRowIndex", 0) < bottom
        and top < grid.get("endRowIndex", bounds["rowCount"])
        and grid.get("startColumnIndex", 0) < right
        and left < grid.get("endColumnIndex", bounds["columnCount"])
        for top, bottom, left, right in ranges
    )


def _protection_blocks(
    protection: dict, named_ranges: dict, ranges: list[tuple[int, int, int, int]], bounds: dict
) -> bool:
    if protection.get("warningOnly") or protection.get("requestingUserCanEdit"):
        return False
    protected = protection.get("range") or named_ranges.get(protection.get("namedRangeId"))
    if protected is None:
        return True
    rows, columns = bounds["rowCount"], bounds["columnCount"]
    exceptions = protection.get("unprotectedRanges", [])
    for first_row, last_row, start, end in ranges:
        top = max(first_row, protected.get("startRowIndex", 0))
        bottom = min(last_row, protected.get("endRowIndex", rows))
        left = max(start, protected.get("startColumnIndex", 0))
        right = min(end, protected.get("endColumnIndex", columns))
        if left >= right or top >= bottom:
            continue
        # Verify every row/column strip is covered, allowing adjacent exceptions to
        # cover a range together without accepting holes between them.
        edges = {top, bottom}
        for region in exceptions:
            edges.update(
                max(top, min(bottom, region.get(key, default)))
                for key, default in (
                    ("startRowIndex", 0),
                    ("endRowIndex", rows),
                )
            )
        ordered = sorted(edges)
        for row_start, row_end in zip(ordered, ordered[1:], strict=False):
            intervals = sorted(
                (region.get("startColumnIndex", 0), region.get("endColumnIndex", columns))
                for region in exceptions
                if region.get("startRowIndex", 0) <= row_start
                and region.get("endRowIndex", rows) >= row_end
                and region.get("sheetId", protected.get("sheetId")) == protected.get("sheetId")
            )
            covered_until = left
            for col_start, col_end in intervals:
                if col_start <= covered_until:
                    covered_until = max(covered_until, col_end)
            if covered_until < right:
                return True
    return False


def _build_plan(settings: WeekUpdateSettings, sheet: dict, named_ranges: dict, wanted: str) -> _Plan:
    props = sheet["properties"]
    report = {
        "sheet_id": props["sheetId"],
        "sheet_name": props["title"],
        "ranges": [],
        "formula_count": 0,
        "written_cells": 0,
    }
    plan = _Plan(report, [], [], {})
    try:
        if props.get("sheetType", "GRID") != "GRID":
            raise ValueError("Поддерживаются только обычные листы Google Таблиц")
        bounds = props.get("gridProperties", {})
        row_count, column_count = bounds.get("rowCount", 0), bounds.get("columnCount", 0)
        if not row_count or not column_count:
            raise ValueError("Не удалось определить границы листа")
        cells = _cells(sheet)
        labels = {pos: _display(cell) for pos, cell in cells.items()}
        identity_rows: dict[int, set[str]] = {}
        for (row, _column), value in labels.items():
            identity = value.strip().upper()
            if identity in {"ARTICLE", "BARCODE"}:
                identity_rows.setdefault(row, set()).add(identity)
        table_rows = {row for row, identities in identity_rows.items() if identities == {"ARTICLE", "BARCODE"}}
        if not table_rows:
            raise ValueError("Не найдена строка заголовков с ARTICLE и BARCODE")
        anchors = sorted(
            pos for pos, value in labels.items() if pos[0] in table_rows and pos[1] >= 8 and value == wanted
        )
        if not anchors:
            raise ValueError(f"Неделя {wanted} не найдена в строке с ARTICLE и BARCODE начиная со столбца I")
        target_week = _week_start(wanted)
        spans, headers = [], []
        for row, anchor in anchors:
            start, last_week = anchor, target_week
            for col in range(anchor - 1, -1, -1):
                value = labels.get((row, col), "")
                previous = _week_start(value)
                if previous is None:
                    break
                if previous >= last_week:
                    raise ValueError(
                        f"Неоднозначный порядок недель около {search._address(row, col)}: "
                        "слева должны быть только более ранние недели"
                    )
                start, last_week = col, previous
            if start == anchor:
                continue
            span = start, anchor
            if any(start < end and left < anchor and span != (left, end) for left, end in spans):
                raise ValueError("Найдены пересекающиеся диапазоны с разными границами недель")
            if span not in spans:
                spans.append(span)
            headers.append(
                {
                    "row": row,
                    "start": start,
                    "end": anchor,
                    "values": [labels[row, c] for c in range(start, anchor + 1)],
                }
            )
        spans.sort()
        if not spans:
            raise ValueError("Слева от найденной недели нет непрерывного блока прошлых недель")
        # Identical spans can occur in repeated table headers, but their weeks must agree.
        by_span = {}
        for header in headers:
            key = header["start"], header["end"]
            if key in by_span and by_span[key] != header["values"]:
                raise ValueError("Недельные заголовки в разных строках не совпадают")
            by_span[key] = header["values"]
        # Each block starts below its own header. Repeated headers divide the rows
        # into separate ranges so even a later formula-based header is never pasted.
        ranges = []
        for start, end in spans:
            header_rows = sorted({h["row"] for h in headers if (h["start"], h["end"]) == (start, end)})
            for index, header_row in enumerate(header_rows):
                top = header_row + 1
                bottom = header_rows[index + 1] if index + 1 < len(header_rows) else row_count
                if top < bottom:
                    ranges.append((top, bottom, start, end))
        report["ranges"] = [
            f"{search._address(top, left)}:{search._address(bottom - 1, right - 1)}"
            for top, bottom, left, right in ranges
        ]
        for (row, col), value in labels.items():
            period = _week_start(value)
            if _inside(row, col, ranges) and period and period >= target_week:
                raise ValueError(f"Диапазон затрагивает {value} в {search._address(row, col)}")
        if props["title"] == settings.sheet_name:
            for source in settings.cells:
                if _inside(*search._position(source), ranges):
                    raise ValueError(f"Диапазон затрагивает исходную ячейку обновления недели {source}")
        for merge in sheet.get("merges", []):
            if not _intersects(merge, ranges, bounds):
                continue
            left, right = merge.get("startColumnIndex", 0), merge.get("endColumnIndex", column_count)
            top, bottom = merge.get("startRowIndex", 0), merge.get("endRowIndex", row_count)
            if not any(
                first_row <= top and bottom <= last_row and start <= left and right <= end
                for first_row, last_row, start, end in ranges
            ):
                raise ValueError("Объединённые ячейки пересекают границу диапазона фиксации")
        for protection in sheet.get("protectedRanges", []):
            if _protection_blocks(protection, named_ranges, ranges, bounds):
                raise ValueError("Диапазон пересекает защищённые ячейки; фиксация листа пропущена")
        for (row, col), cell in sorted(cells.items()):
            if not _inside(row, col, ranges):
                continue
            entered, effective = cell.get("userEnteredValue", {}), cell.get("effectiveValue", {})
            address = search._address(row, col)
            if effective.get("errorValue"):
                raise ValueError(f"В ячейке {address} ошибка вычисления; фиксация листа пропущена")
            if any(key in cell for key in ("pivotTable", "dataSourceTable", "dataSourceFormula")):
                raise ValueError(f"В диапазоне есть сводная таблица или внешний источник ({address})")
            if "formulaValue" in entered:
                if not any(key in effective for key in ("numberValue", "stringValue", "boolValue")):
                    raise ValueError(f"Формула {address} ещё не рассчитана")
                plan.formulas.append({"cell": address, "previous": entered, "effective_value": effective})
        # The API does not expose spill owners/extents. Outputs to the right/below a
        # copied formula can belong to it even outside the target columns. Outputs
        # strictly to its left/above cannot, so unrelated ARTICLE arrays are allowed.
        leftmost_formula = column_count
        for (row, col), cell in sorted(cells.items()):
            if "formulaValue" in cell.get("userEnteredValue", {}) and _inside(row, col, ranges):
                leftmost_formula = min(leftmost_formula, col)
            if (
                cell.get("effectiveValue")
                and not cell.get("userEnteredValue")
                and (_inside(row, col, ranges) or leftmost_formula <= col)
            ):
                raise ValueError(
                    "Диапазон может затронуть вывод формулы массива или сводной таблицы; границы её вывода нельзя безопасно определить"
                )
        report["formula_count"] = len(plan.formulas)
        plan.signature = {
            "properties": props,
            "merges": sheet.get("merges", []),
            "protections": sheet.get("protectedRanges", []),
            "named_ranges": named_ranges,
            "headers": headers,
            "ranges": ranges,
            "values": [
                (row, col, cell.get("userEnteredValue", {}))
                for (row, col), cell in sorted(cells.items())
                if _inside(row, col, ranges)
            ],
        }
        if plan.formulas:
            for top, bottom, start, end in ranges:
                grid = {
                    "sheetId": props["sheetId"],
                    "startRowIndex": top,
                    "endRowIndex": bottom,
                    "startColumnIndex": start,
                    "endColumnIndex": end,
                }
                plan.requests.append(
                    {
                        "copyPaste": {
                            "source": grid,
                            "destination": dict(grid),
                            "pasteType": "PASTE_VALUES",
                            "pasteOrientation": "NORMAL",
                        }
                    }
                )
    except ValueError as error:
        report["issue"] = str(error)
        report["formula_count"] = 0
        plan.requests, plan.formulas = [], []
    return plan


def _save_backup(settings: WeekUpdateSettings, wanted: str, plans: list[_Plan], now: datetime) -> str:
    folder = app_settings.database_path.parent / "backups" / "google-week-freeze"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}.json"
    payload = {
        "version": 1,
        "created_at": now.isoformat(),
        "week": wanted,
        "spreadsheet_url": settings.spreadsheet_url,
        "spreadsheet_id": week.spreadsheet_id(settings.spreadsheet_url),
        "sheets": [
            {
                "sheet_id": plan.report["sheet_id"],
                "sheet_name": plan.report["sheet_name"],
                "ranges": plan.report["ranges"],
                "cells": plan.formulas,
            }
            for plan in plans
            if plan.requests
        ],
    }
    with path.open("x", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    return path.name


def freeze_sheets(
    settings: WeekUpdateSettings,
    state: WeekFreezeState,
    *,
    now: datetime,
    service=None,
    dry_run: bool = False,
) -> dict:
    settings = week.validate(settings)
    doc_id = week.spreadsheet_id(settings.spreadsheet_url)
    if not state.selection_initialized or state.spreadsheet_id != doc_id:
        raise ValueError("Обновите список листов и сохраните выбор для текущей Google Таблицы")
    if not state.sheet_ids:
        raise ValueError("Выберите хотя бы один лист для фиксации прошлых недель")
    current, wanted = week._now(now), week.last_completed_week(now)
    reader = service or week._google_service(read_only=True)
    sheets, named_ranges = _read_sheets(reader, doc_id, state.sheet_ids)
    plans = []
    for sheet_id in state.sheet_ids:
        if sheet_id not in sheets:
            plans.append(
                _Plan(
                    {
                        "sheet_id": sheet_id,
                        "sheet_name": f"Лист #{sheet_id}",
                        "ranges": [],
                        "formula_count": 0,
                        "written_cells": 0,
                        "issue": "Выбранный лист удалён или недоступен; обновите список листов",
                    },
                    [],
                    [],
                    {},
                )
            )
        else:
            plans.append(_build_plan(settings, sheets[sheet_id], named_ranges, wanted))
    report = {
        "processed_at": current.isoformat(),
        "spreadsheet_url": settings.spreadsheet_url,
        "spreadsheet_id": doc_id,
        "sheet_ids": list(state.sheet_ids),
        "week": wanted,
        "source_sheet_name": settings.sheet_name,
        "source_cells": list(settings.cells),
        "complete": False,
        "dry_run": dry_run,
        "sheets": [plan.report for plan in plans],
        "total_formulas": 0,
        "written_cells": 0,
        "backup": None,
    }
    if not dry_run and any(plan.requests for plan in plans):
        fresh, fresh_named = _read_sheets(reader, doc_id, state.sheet_ids)
        for plan in plans:
            if not plan.requests:
                continue
            sheet = fresh.get(plan.report["sheet_id"])
            checked = _build_plan(settings, sheet, fresh_named, wanted) if sheet else None
            if checked is None or checked.report.get("issue") or checked.signature != plan.signature:
                plan.report["issue"] = (
                    "Структура, формулы или значения листа изменились во время проверки. Повторите фиксацию"
                )
                plan.report["formula_count"] = 0
                plan.requests, plan.formulas = [], []
            else:
                # Back up the rechecked formula results, which may legitimately have recalculated.
                plan.formulas = checked.formulas
        requests = [request for plan in plans for request in plan.requests]
        if requests:
            report["backup"] = _save_backup(settings, wanted, plans, current)
            writer = service or week._google_service()
            # No blind write retries after a timeout: the next run rereads and backs up again.
            writer.spreadsheets().batchUpdate(spreadsheetId=doc_id, body={"requests": requests}).execute(
                num_retries=0
            )
            for plan in plans:
                if plan.requests:
                    plan.report["written_cells"] = plan.report["formula_count"]
    report["complete"] = all(not plan.report.get("issue") for plan in plans)
    report["total_formulas"] = sum(plan.report["formula_count"] for plan in plans)
    report["written_cells"] = sum(plan.report["written_cells"] for plan in plans)
    return report


def _run(*, manual: bool, now: datetime | None = None) -> dict:
    """Caller holds google_week_update's shared run_tracked lock."""
    settings, state = week_repository.get_settings(), repository.get_state()
    current = week._now(now)
    if not manual and not is_due(settings, now=current):
        return {"skipped": True}
    if not settings.updated_at:
        raise ValueError("Сначала сохраните настройки обновления недели")
    attempted, slot = current.isoformat(), week.scheduled_at(settings, current).isoformat()
    repository.record_attempt(attempted)
    try:
        result = freeze_sheets(settings, state, now=current)
    except Exception as error:
        message = _error_message(error)
        repository.record_result(attempted, slot=slot, error=message)
        raise ValueError(message) from error
    repository.record_result(attempted, slot=slot, result=result)
    return result


def run_now(now: datetime | None = None) -> dict:
    return _run(manual=True, now=now)


def run_due(now: datetime | None = None) -> dict:
    return _run(manual=False, now=now)


def preview_now(now: datetime | None = None) -> dict:
    settings = week_repository.get_settings()
    if not settings.updated_at:
        raise ValueError("Сначала сохраните настройки обновления недели")
    try:
        return freeze_sheets(settings, repository.get_state(), now=week._now(now), dry_run=True)
    except Exception as error:
        raise ValueError(_error_message(error, preview=True)) from error


def _error_message(error: Exception, *, preview: bool = False) -> str:
    status = getattr(getattr(error, "resp", None), "status", None)
    action = "проверку" if preview else "фиксацию"
    if status:
        return f"Google Таблицы: HTTP {status}. Проверьте доступ и повторите {action}"
    if isinstance(error, ValueError):
        return str(error)
    return f"Не удалось выполнить {action} недель ({type(error).__name__}). Повторите запуск"
