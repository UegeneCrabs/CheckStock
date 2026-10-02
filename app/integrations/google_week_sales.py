"""Export net WB funnel order counts to dynamically discovered product/week cells."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

from app.config import settings as app_settings
from app.integrations import google_week_search as search
from app.integrations import google_week_update as week
from app.repositories import google_week_sales as source
from app.repositories import google_week_update as repository
from app.repositories.google_week_update import WeekUpdateSettings


def cell_value(rows: list[list], row: int, col: int):
    return rows[row][col] if row < len(rows) and col < len(rows[row]) else ""


def week_dates(label: str) -> tuple[date, date]:
    if not search._is_week(label):
        raise ValueError(f"Неправильная неделя: {label}")
    number, year = search._normalized(label).split()
    monday = date.fromisocalendar(int(year), int(number[1:]), 1)
    return monday, monday + timedelta(days=6)


def is_due(settings: WeekUpdateSettings, *, now: datetime) -> bool:
    state = repository.get_sales_state()
    return week.update_is_due(search.schedule_settings(settings, state), now)


def layout(result: dict) -> tuple[int, int, int, list[dict]]:
    sources = result["sources"]
    if any(item.get("issue") or not item["matches"] for item in sources):
        raise ValueError("Не для всех исходных ячеек найден недельный диапазон. Проверьте настройки")
    matches = [match for item in sources for match in item["matches"]]
    if not matches or len({item["row"] for item in matches}) != 1:
        raise ValueError("Недельные заголовки должны находиться в одной строке")
    columns = matches[0]["columns"]
    if any(len(columns.get(name, [])) != 1 for name in ("ARTICLE", "BARCODE")):
        raise ValueError("В строке недель нужен ровно один столбец ARTICLE и один BARCODE")
    header_row = matches[0]["row"] - 1
    article_col = search._position(columns["ARTICLE"][0]["cell"])[1]
    barcode_col = search._position(columns["BARCODE"][0]["cell"])[1]
    headers = {cell["cell"]: cell for match in matches for cell in match["headers"]}
    return header_row, article_col, barcode_col, list(headers.values())


def _identities(products: list[dict]) -> tuple[dict, dict]:
    articles, barcodes = defaultdict(set), defaultdict(set)
    for item in products:
        key = (item["store_slug"], item["article"])
        for value in (item["article"], *item.get("article_aliases", [])):
            articles[str(value).strip()].add(key)
        for value in (item.get("barcode", ""), *item.get("barcodes", [])):
            if str(value).strip():
                barcodes[str(value).strip()].add(key)
    return articles, barcodes


def match_products(
    rows: list[list], header_row: int, article_col: int, barcode_col: int
) -> tuple[list, list]:
    """Resolve one WB product/store per row; retain actionable identity issues."""
    articles, barcodes = _identities(source.products())
    matched, issues, seen = [], [], defaultdict(list)
    for row in range(header_row + 1, len(rows)):
        article = str(cell_value(rows, row, article_col)).strip()
        barcode = str(cell_value(rows, row, barcode_col)).strip()
        if not article and not barcode:
            continue
        item = {"row": row + 1, "article": article, "barcode": barcode}
        candidates = articles.get(article, set()) if article else barcodes.get(barcode, set())
        if barcode:
            candidates = candidates & barcodes.get(barcode, set())
        if len(candidates) != 1:
            issues.append(
                {
                    **item,
                    "reason": "Товар не найден по ARTICLE/BARCODE"
                    if not candidates
                    else "Товар совпал с несколькими записями каталога",
                }
            )
            continue
        key = next(iter(candidates))
        item.update(store_slug=key[0], nm_id=key[1])
        matched.append(item)
        seen[key].append(row + 1)
    unique = []
    for item in matched:
        duplicates = seen[(item["store_slug"], item["nm_id"])]
        if len(duplicates) > 1:
            issues.append(
                {**item, "reason": "Один товар повторяется в строках " + ", ".join(map(str, duplicates))}
            )
        else:
            unique.append(item)
    return unique, issues


def build_plan(settings: WeekUpdateSettings, sheet: dict, rows: list[list], *, now: datetime) -> dict:
    result = search.describe_headers(settings, sheet, rows)
    header_row, article_col, barcode_col, headers = layout(result)
    periods = []
    for cell in headers:
        start, end = week_dates(cell["value"])
        periods.append(
            {
                **cell,
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "days": [(start + timedelta(days=i)).isoformat() for i in range(7)],
            }
        )
    unique, issues = match_products(rows, header_row, article_col, barcode_col)
    stores = tuple(sorted({item["store_slug"] for item in unique}))
    facts, coverage = source.daily_facts(
        stores, min(p["date_from"] for p in periods), max(p["date_to"] for p in periods)
    )
    writes, gaps, zero_filled = [], [], []
    today = week._now(now).date().isoformat()
    for item in unique:
        for period in periods:
            col = search._position(period["cell"])[1]
            address = search._address(item["row"] - 1, col)
            if period["date_to"] >= today:
                gaps.append(
                    {
                        **item,
                        "cell": address,
                        "week": period["value"],
                        "missing_days": [day for day in period["days"] if day >= today],
                    }
                )
                continue
            missing, values = [], []
            for day in period["days"]:
                key = (item["store_slug"], item["nm_id"], day)
                if key in facts and facts[key] is not None:
                    values.append(facts[key])
                else:
                    # Clear stale sheet values even when the database has no usable fact.
                    values.append(0)
                    if key in facts or day not in coverage.get(item["store_slug"], set()):
                        missing.append(day)
            if missing:
                zero_filled.append(
                    {**item, "cell": address, "week": period["value"], "missing_days": missing}
                )
            writes.append({"cell": address, "value": sum(values)})
    return {
        **result,
        "periods": [{k: v for k, v in p.items() if k != "days"} for p in periods],
        "product_rows": len(unique) + len(issues),
        "matched_products": len(unique),
        "issues": issues,
        "missing_data": gaps,
        "zero_filled": zero_filled,
        "writes": writes,
        "complete": bool(unique) and not issues and not gaps and not zero_filled,
        "processed_at": now.isoformat(),
    }


def _check_targets(settings: WeekUpdateSettings, sheet: dict, plan: dict) -> None:
    grid = sheet["properties"]["gridProperties"]
    protected = set(settings.cells)
    for entry in plan["writes"]:
        r, c = search._position(entry["cell"])
        if entry["cell"] in protected or r >= grid["rowCount"] or c >= grid["columnCount"]:
            raise ValueError(f"Недопустимая ячейка назначения: {entry['cell']}")
        for merge in sheet.get("merges", []):
            if (
                merge.get("startRowIndex", 0) <= r < merge["endRowIndex"]
                and merge.get("startColumnIndex", 0) <= c < merge["endColumnIndex"]
            ):
                raise ValueError(f"Ячейка {entry['cell']} объединена. Выгрузка остановлена")


def save_backup(
    settings: WeekUpdateSettings,
    sheet: dict,
    writes: list[dict],
    formulas: list[list],
    *,
    kind: str = "google-week-sales",
) -> str:
    folder = app_settings.database_path.parent / "backups" / kind
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}.json"
    cells = [
        {**entry, "previous": cell_value(formulas, *search._position(entry["cell"]))} for entry in writes
    ]
    with path.open("x", encoding="utf-8") as file:
        json.dump(
            {
                "spreadsheet_url": settings.spreadsheet_url,
                "sheet_name": settings.sheet_name,
                "sheet_id": sheet["properties"]["sheetId"],
                "cells": cells,
            },
            file,
            ensure_ascii=False,
        )
    return path.name


def export_orders(settings: WeekUpdateSettings, *, now: datetime, dry_run: bool = False) -> dict:
    # Use read-only credentials for inspection; create a writer only after all checks and backup succeed.
    reader = week._google_service(read_only=True)
    sheet, rows = search.read_sheet(settings, reader)
    plan = build_plan(settings, sheet, rows, now=now)
    _check_targets(settings, sheet, plan)
    if dry_run:
        return plan
    return write_plan(settings, sheet, rows, plan, reader)


def write_plan(
    settings: WeekUpdateSettings,
    sheet: dict,
    rows: list[list],
    plan: dict,
    reader,
    *,
    backup_kind: str = "google-week-sales",
    before_write=None,
) -> dict:
    """Write only validated changed cells, after a fresh read and a durable local backup."""
    _check_targets(settings, sheet, plan)
    plan = dict(plan)
    writes = plan.pop("writes")
    report = {
        **plan,
        "checked_cells": len(writes),
        "written_cells": 0,
        "zeroed_cells": 0,
        "unchanged_cells": 0,
        "backup": None,
    }
    if not writes:
        return report
    doc_id = week.spreadsheet_id(settings.spreadsheet_url)
    quoted = "'" + settings.sheet_name.replace("'", "''") + "'"
    formulas = (
        reader.spreadsheets()
        .values()
        .get(spreadsheetId=doc_id, range=quoted, valueRenderOption="FORMULA")
        .execute(num_retries=2)
        .get("values", [])
    )
    # Recheck the source, product identities, headers and destination values immediately before writing.
    current_sheet, current_rows = search.read_sheet(settings, reader)
    header_row, article_col, barcode_col, _ = layout(plan)
    watched = set(settings.cells) | {p["cell"] for p in plan["periods"]} | {item["cell"] for item in writes}
    for r in range(header_row, max(len(rows), len(current_rows))):
        watched.update((search._address(r, article_col), search._address(r, barcode_col)))
    if current_sheet != sheet or any(
        cell_value(rows, *search._position(cell)) != cell_value(current_rows, *search._position(cell))
        for cell in watched
    ):
        raise ValueError("Таблица изменилась во время расчёта. Повторите выгрузку")
    _check_targets(settings, current_sheet, plan | {"writes": writes})
    changed = []
    for entry in writes:
        previous = cell_value(formulas, *search._position(entry["cell"]))
        # Empty cells, formulas, text "0" and boolean FALSE must become numeric zero.
        if type(previous) not in (int, float) or previous != entry["value"]:
            changed.append(entry)
    report["unchanged_cells"] = len(writes) - len(changed)
    writes = changed
    if not writes:
        return report
    data = [{"range": f"{quoted}!{entry['cell']}", "values": [[entry["value"]]]} for entry in writes]
    body = {"valueInputOption": "RAW", "data": data}
    if len(json.dumps(body).encode("utf-8")) > 1_800_000:
        raise ValueError("Слишком много ячеек для одной выгрузки. Уменьшите диапазон товаров или недель")
    if before_write is not None:
        before_write()
    if backup_kind == "google-week-sales":
        report["backup"] = save_backup(settings, sheet, writes, formulas)
    else:
        report["backup"] = save_backup(settings, sheet, writes, formulas, kind=backup_kind)
    writer = week._google_service()
    writer.spreadsheets().values().batchUpdate(spreadsheetId=doc_id, body=body).execute(num_retries=2)
    report["written_cells"] = len(writes)
    report["zeroed_cells"] = sum(entry["value"] == 0 for entry in writes)
    return report


def _run(*, manual: bool, now: datetime | None = None) -> dict:
    """Caller holds google_week_update's shared run_tracked lock."""
    settings = repository.get_settings()
    current = week._now(now)
    if not manual and not is_due(settings, now=current):
        return {"skipped": True}
    if not settings.updated_at:
        raise ValueError("Сначала сохраните настройки обновления недели")
    settings = week.validate(settings)
    attempted = current.isoformat()
    slot = week.scheduled_at(settings, current).isoformat()
    repository.record_sales_attempt(attempted)
    try:
        result = export_orders(settings, now=current)
    except Exception as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        if status:
            message = f"Google Таблицы: HTTP {status}. Проверьте доступ и повторите выгрузку"
        elif isinstance(error, ValueError):
            message = str(error)
        else:
            message = f"Не удалось выгрузить заказы ({type(error).__name__}). Повторите запуск"
        repository.record_sales_result(attempted, slot=slot, error=message)
        raise ValueError(message) from error
    repository.record_sales_result(attempted, slot=slot, result=result)
    return result


def run_now(now: datetime | None = None) -> dict:
    return _run(manual=True, now=now)


def run_due(now: datetime | None = None) -> dict:
    return _run(manual=False, now=now)
