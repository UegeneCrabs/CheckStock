"""Write WB FBO snapshots for the Sunday of each explicitly configured source week."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.integrations import google_week_sales as sales
from app.integrations import google_week_search as search
from app.integrations import google_week_update as week
from app.repositories import google_week_stock as source
from app.repositories import google_week_update as repository
from app.repositories.google_week_update import WeekStockState, WeekUpdateSettings


def is_due(settings: WeekUpdateSettings, *, now: datetime) -> bool:
    return week.update_is_due(search.schedule_settings(settings, repository.get_stock_state()), now)


def result_matches_settings(result: dict, settings: WeekUpdateSettings, state: WeekStockState) -> bool:
    return (
        result.get("spreadsheet_url") == settings.spreadsheet_url
        and result.get("source_sheet_name") == settings.sheet_name
        and result.get("source_cells") == list(settings.cells)
        and result.get("sheet_name") == state.sheet_name
    )


def source_weeks(settings: WeekUpdateSettings, rows: list[list]) -> list[dict]:
    values = []
    for cell in settings.cells:
        value = str(sales.cell_value(rows, *search._position(cell)))
        if not search._is_week(value):
            raise ValueError(f"В исходной ячейке {cell} нет недели в формате W39 2026")
        values.append({"cell": cell, "value": value})
    return values


def find_columns(
    settings: WeekUpdateSettings, target: WeekUpdateSettings, sheet: dict, rows: list[list], weeks: list[dict]
) -> dict:
    """Find exactly the source week labels, without extending into adjacent weeks."""
    protected = set(settings.cells) if settings.sheet_name == target.sheet_name else set()
    sources = []
    for origin in weeks:
        matches = []
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                address = search._address(r, c)
                if address in protected or search._normalized(value) != search._normalized(origin["value"]):
                    continue
                columns = {
                    name: [
                        {"cell": search._address(r, col), "value": str(item)}
                        for col, item in enumerate(row)
                        if search._normalized(item).upper() == name
                    ]
                    for name in ("ARTICLE", "BARCODE")
                }
                matches.append(
                    {
                        "cell": address,
                        "range": f"{address}:{address}",
                        "row": r + 1,
                        "headers": [{"cell": address, "value": str(value)}],
                        "columns": columns,
                        "stop": None,
                    }
                )
        sources.append({**origin, "matches": matches})
    return {
        "spreadsheet_url": settings.spreadsheet_url,
        "source_sheet_name": settings.sheet_name,
        "sheet_name": target.sheet_name,
        "sheet_id": sheet["properties"]["sheetId"],
        "source_cells": list(settings.cells),
        "sources": sources,
    }


def build_plan(
    settings: WeekUpdateSettings,
    target: WeekUpdateSettings,
    sheet: dict,
    rows: list[list],
    weeks: list[dict],
    *,
    now: datetime,
) -> dict:
    result = find_columns(settings, target, sheet, rows, weeks)
    header_row, article_col, barcode_col, headers = sales.layout(result)
    products, issues = sales.match_products(rows, header_row, article_col, barcode_col)
    periods = []
    for header in headers:
        start, end = sales.week_dates(header["value"])
        periods.append(
            {
                **header,
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "snapshot_day": end.isoformat(),
            }
        )
    stores = tuple(sorted({p["store_slug"] for p in products}))
    days = tuple(sorted({p["snapshot_day"] for p in periods}))
    history = source.snapshots(stores, days)
    writes, missing, zero_filled, captures = [], [], [], set()
    today = week._now(now).date().isoformat()
    for product in products:
        for period in periods:
            day = period["snapshot_day"]
            cell = search._address(product["row"] - 1, search._position(period["cell"])[1])
            snapshot = history.get((product["store_slug"], product["nm_id"], day))
            if day >= today:
                missing.append({**product, "cell": cell, "week": period["value"], "missing_days": [day]})
                continue
            if snapshot is None:
                zero_filled.append({**product, "cell": cell, "week": period["value"], "missing_days": [day]})
                writes.append({"cell": cell, "value": 0})
                continue
            writes.append({"cell": cell, "value": snapshot["quantity"]})
            captures.add((product["store_slug"], day, snapshot["captured_at"]))
    return {
        **result,
        "periods": periods,
        "product_rows": len(products) + len(issues),
        "matched_products": len(products),
        "issues": issues,
        "missing_data": missing,
        "zero_filled": zero_filled,
        "writes": writes,
        "snapshot_times": [
            {"store_slug": store, "day": day, "captured_at": captured}
            for store, day, captured in sorted(captures)
        ],
        "complete": bool(products) and not issues and not missing and not zero_filled,
        "processed_at": week._now(now).isoformat(),
    }


def export_stock(
    settings: WeekUpdateSettings, state: WeekStockState, *, now: datetime, dry_run: bool = False
) -> dict:
    settings = week.validate(settings)
    target = week.validate(replace(settings, sheet_name=state.sheet_name))
    # The same A1 address on a different sheet is not an original source cell.
    if target.sheet_name != settings.sheet_name:
        target = replace(target, cells=())
    reader = week._google_service(read_only=True)
    source_sheet, source_rows = search.read_sheet(settings, reader)
    weeks = source_weeks(settings, source_rows)
    sheet, rows = search.read_sheet(target, reader)
    plan = build_plan(settings, target, sheet, rows, weeks, now=now)
    sales._check_targets(target, sheet, plan)
    if dry_run:
        return plan

    def check_source():
        current_sheet, current_rows = search.read_sheet(settings, reader)
        if (
            current_sheet["properties"]["sheetId"] != source_sheet["properties"]["sheetId"]
            or source_weeks(settings, current_rows) != weeks
        ):
            raise ValueError("Исходная неделя изменилась во время расчёта. Повторите выгрузку остатков")

    return sales.write_plan(
        target, sheet, rows, plan, reader, backup_kind="google-week-stock", before_write=check_source
    )


def _run(*, manual: bool, now: datetime | None = None) -> dict:
    """Caller holds the shared google_week_update run_tracked lock."""
    settings, state = repository.get_settings(), repository.get_stock_state()
    current = week._now(now)
    if not manual and not is_due(settings, now=current):
        return {"skipped": True}
    if not settings.updated_at:
        raise ValueError("Сначала сохраните настройки обновления недели")
    attempted, slot = current.isoformat(), week.scheduled_at(settings, current).isoformat()
    repository.record_stock_attempt(attempted)
    try:
        result = export_stock(settings, state, now=current)
    except Exception as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        if status:
            message = f"Google Таблицы: HTTP {status}. Проверьте доступ и повторите выгрузку остатков"
        elif isinstance(error, ValueError):
            message = str(error)
        else:
            message = f"Не удалось выгрузить остатки FBO ({type(error).__name__}). Повторите запуск"
        repository.record_stock_result(attempted, slot=slot, error=message)
        raise ValueError(message) from error
    repository.record_stock_result(attempted, slot=slot, result=result)
    return result


def run_now(now: datetime | None = None) -> dict:
    return _run(manual=True, now=now)


def run_due(now: datetime | None = None) -> dict:
    return _run(manual=False, now=now)
