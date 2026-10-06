from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.exports import stock_sheet as legacy
from app.jobs import locks as sync_locks
from app.repositories import project_sheet_export as repository

logger = logging.getLogger(__name__)

MARKETPLACES = repository.MARKETPLACES
EXPORT_KINDS = legacy.EXPORT_KINDS
PROJECT_HEADER = "ПРОЕКТ"
EXPORT_HEADERS = ("КЛЮЧ", PROJECT_HEADER, *legacy.EXPORT_HEADERS)
ORDER_EXPORT_HEADERS = (PROJECT_HEADER, *legacy.ORDER_EXPORT_HEADERS)
SPREADSHEET_PATH_RE = re.compile(r"^/spreadsheets/d/([a-zA-Z0-9_-]+)(?:/.*)?$")

ProjectSheetExportSettings = repository.ProjectSheetExportSettings
MarketplaceExportTarget = repository.MarketplaceExportTarget
StockSheetExportError = legacy.StockSheetExportError


@dataclass
class ProjectStockSnapshot:
    store_slug: str
    catalog: list[dict]
    values_by_metric: dict[str, dict[str, int | None]]
    warnings: list[str]


def default_settings(now: datetime | None = None) -> ProjectSheetExportSettings:
    return ProjectSheetExportSettings(
        enabled=False,
        schedule_kind="daily",
        weekday=6,
        run_time="01:00",
        updated_at=legacy._now_iso(now),
        last_attempt_at=None,
        last_success_at=None,
        last_error=None,
        targets=tuple(MarketplaceExportTarget(marketplace, "", "") for marketplace in MARKETPLACES),
    )


def ensure_defaults() -> None:
    repository.save_settings(default_settings(), only_if_missing=True)


def get_settings() -> ProjectSheetExportSettings:
    return repository.get_settings() or default_settings()


def _spreadsheet_id(value: str) -> str:
    try:
        url = urlsplit(value.strip())
        match = SPREADSHEET_PATH_RE.fullmatch(url.path)
        valid = url.scheme == "https" and url.netloc.casefold() == "docs.google.com" and match
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Укажите корректную ссылку https://docs.google.com/spreadsheets/d/…")
    return match.group(1)


def validate_settings(settings: ProjectSheetExportSettings) -> None:
    if settings.schedule_kind not in {"daily", "weekly"}:
        raise ValueError("Периодичность должна быть ежедневной или еженедельной")
    if not 0 <= settings.weekday <= 6:
        raise ValueError("Некорректный день недели")
    if not legacy.TIME_RE.fullmatch(settings.run_time):
        raise ValueError("Время нужно указать в формате ЧЧ:ММ")
    actual = [target.marketplace for target in settings.targets]
    if set(actual) != set(MARKETPLACES) or len(actual) != len(MARKETPLACES):
        raise ValueError("Для каждого маркетплейса должна быть одна настройка выгрузки")
    destinations: dict[tuple[str, str], str] = {}
    for target in settings.targets:
        spreadsheet_url = target.spreadsheet_url.strip()
        has_sheets = bool(target.stock_sheet_name.strip() or target.orders_sheet_name.strip())
        if has_sheets and not spreadsheet_url:
            raise ValueError(f"{target.marketplace}: укажите ссылку на Google Таблицу")
        spreadsheet_id = _spreadsheet_id(spreadsheet_url) if spreadsheet_url else ""
        for label, raw_name in (("стоки", target.stock_sheet_name), ("заказы", target.orders_sheet_name)):
            name = raw_name.strip()
            if not name:
                continue
            if len(name) > 100 or any(char in name for char in "[]:*?/\\"):
                raise ValueError(f"Некорректное название листа: {name}")
            key = (spreadsheet_id, name.casefold())
            if key in destinations:
                raise ValueError(
                    f"Лист «{name}» в одной таблице указан несколько раз ({destinations[key]} и "
                    f"{target.marketplace}: {label}). Для каждой выгрузки нужен отдельный лист."
                )
            destinations[key] = f"{target.marketplace}: {label}"


def save_settings(settings: ProjectSheetExportSettings) -> None:
    settings = replace(
        settings,
        targets=tuple(
            replace(
                target,
                spreadsheet_url=target.spreadsheet_url.strip(),
                stock_sheet_name=target.stock_sheet_name.strip(),
                orders_sheet_name=target.orders_sheet_name.strip(),
            )
            for target in settings.targets
        ),
    )
    validate_settings(settings)
    with sync_locks.hold("stock_sheet_export"):
        repository.save_settings(settings)


def current_error(settings: ProjectSheetExportSettings) -> str | None:
    return legacy.current_error(settings)


def scheduled_at(settings: ProjectSheetExportSettings, now: datetime) -> datetime:
    return legacy.scheduled_at(settings, now)


def is_due(settings: ProjectSheetExportSettings, now: datetime | None = None) -> bool:
    return legacy.is_due(settings, now)


def _stock_snapshot(marketplace: str, *, now: datetime | None = None) -> list[ProjectStockSnapshot]:
    snapshots = []
    for store_slug in STORES:
        # A separate snapshot keeps unknown inbound quantities local to this project.
        catalog, values, warnings = legacy._combined_stock_snapshot(
            (store_slug,), marketplace, now=now, allow_saved_snapshot=True
        )
        snapshots.append(ProjectStockSnapshot(store_slug, catalog, values, warnings))
    return snapshots


def _order_totals(marketplace: str, *, now: datetime | None = None) -> dict[str, dict[str, int]]:
    run = legacy._FbsExportRun(now or datetime.now(MOSCOW_TIMEZONE))
    totals = {}
    for store_slug in STORES:
        try:
            totals[store_slug] = legacy._combined_fbs_order_totals((store_slug,), marketplace, run=run)
        except Exception as error:
            raise StockSheetExportError(
                f"{STORES[store_slug].name} / {marketplace} / FBS-заказы: {error}"
            ) from error
    return totals


def _write_marketplace(
    service,
    spreadsheet_id: str,
    settings: ProjectSheetExportSettings,
    marketplace: str,
    snapshots: list[ProjectStockSnapshot],
) -> dict:
    sheet_name = settings.target(marketplace).stock_sheet_name.strip()
    ff_metrics = sorted(
        {
            metric
            for snapshot in snapshots
            for metric in snapshot.values_by_metric
            if metric.startswith(legacy.FF_STOCK_METRIC_PREFIX)
        },
        key=str.casefold,
    )
    headers = [
        *EXPORT_HEADERS,
        *(
            f"{legacy.FF_STOCK_HEADER_PREFIX}{metric.removeprefix(legacy.FF_STOCK_METRIC_PREFIX)}"
            for metric in ff_metrics
        ),
    ]
    rows = []
    for snapshot in snapshots:
        project = STORES[snapshot.store_slug].name
        catalog = [item for item in snapshot.catalog if str(item.get("article") or "").strip()]
        stock_rows = legacy._stock_export_rows(catalog, snapshot.values_by_metric, ff_metrics)
        for item, row in zip(catalog, stock_rows, strict=True):
            # Use the source text so leading zeroes are retained in the key.
            barcode = str(item.get("barcode") or "").strip().removeprefix("'").strip()
            rows.append([f"{project} {barcode}", project, *row])
    report = legacy._write_stock_rows(
        service, spreadsheet_id, marketplace, [sheet_name], headers, rows, replace_sheet=True
    )
    report["store_slugs"] = tuple(snapshot.store_slug for snapshot in snapshots)
    report["warnings"] = [warning for snapshot in snapshots for warning in snapshot.warnings]
    return report


def _order_sheet_preparation(sheet_name: str, sheet: dict, *, required_rows: int | None = None) -> list[dict]:
    properties = sheet["properties"]
    requests = []
    column_count = len(ORDER_EXPORT_HEADERS)
    grid = properties.get("gridProperties", {})
    required_grid = {
        field: count
        for field, count in (("columnCount", column_count), ("rowCount", required_rows))
        if count is not None and grid.get(field, count) < count
    }
    if required_grid:
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": properties["sheetId"],
                        "gridProperties": required_grid,
                    },
                    "fields": ",".join(f"gridProperties.{field}" for field in required_grid),
                }
            }
        )
    for merged in sheet.get("merges", ()):
        if merged.get("endRowIndex", 0) <= 1 or merged.get("startColumnIndex", 0) >= column_count:
            continue
        if merged.get("startRowIndex", 0) < 1 or merged.get("endColumnIndex", 0) > column_count:
            raise StockSheetExportError(
                f"Лист «{sheet_name}»: объединённые ячейки выходят за диапазон A2:C. "
                "Разделите их. Данные листа не изменены."
            )
        requests.append({"unmergeCells": {"range": {**merged, "sheetId": properties["sheetId"]}}})
    return requests


def _write_fbs_orders(
    service,
    spreadsheet_id: str,
    settings: ProjectSheetExportSettings,
    marketplace: str,
    totals: dict[str, dict[str, int]],
) -> dict:
    sheet_name = settings.target(marketplace).orders_sheet_name.strip()
    existing_sheets = legacy._sheet_metadata(service, spreadsheet_id)
    if sheet_name not in existing_sheets:
        raise StockSheetExportError(f"В таблице нет листа: {sheet_name}")
    destination_sheets = {sheet_name: existing_sheets[sheet_name]}
    legacy._check_timestamp_cells(service, spreadsheet_id, destination_sheets)
    data_rows = [
        [STORES[store_slug].name, legacy._sheet_identifier(article), int(quantities[article])]
        for store_slug, quantities in totals.items()
        for article in sorted(quantities, key=legacy._order_article_sort_key)
        if int(quantities[article] or 0) > 0
    ]
    prepare_requests = _order_sheet_preparation(
        sheet_name, existing_sheets[sheet_name], required_rows=len(data_rows) + 2
    )
    values = [list(ORDER_EXPORT_HEADERS), *data_rows]
    quoted_sheet = legacy._quote_sheet(sheet_name)
    if prepare_requests:
        (
            service.spreadsheets()
            .batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": prepare_requests})
            .execute(num_retries=legacy.GOOGLE_REQUEST_RETRIES)
        )
    (
        service.spreadsheets()
        .values()
        .batchClear(spreadsheetId=spreadsheet_id, body={"ranges": [f"{quoted_sheet}!A2:C"]})
        .execute(num_retries=legacy.GOOGLE_REQUEST_RETRIES)
    )
    (
        service.spreadsheets()
        .values()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "valueInputOption": "RAW",
                "data": [{"range": f"{quoted_sheet}!A2:C{len(values) + 1}", "values": values}],
            },
        )
        .execute(num_retries=legacy.GOOGLE_REQUEST_RETRIES)
    )
    exported_at = legacy._write_export_timestamp(service, spreadsheet_id, destination_sheets)
    return {
        "marketplace": marketplace,
        "sheet": sheet_name,
        "period_days": legacy.FBS_ORDER_LOOKBACK_DAYS,
        "rows": len(data_rows),
        "updated_cells": len(values) * len(ORDER_EXPORT_HEADERS) + 2,
        "exported_at": exported_at,
        "store_slugs": tuple(totals),
    }


def export_all(
    now: datetime | None = None,
    *,
    marketplace: str | None = None,
    export_kind: str | None = None,
) -> dict:
    if marketplace is not None and marketplace not in MARKETPLACES:
        raise ValueError("Неизвестный маркетплейс")
    if export_kind is not None and export_kind not in EXPORT_KINDS:
        raise ValueError("Неизвестный тип выгрузки")
    settings = get_settings()
    validate_settings(settings)
    now = now or datetime.now(MOSCOW_TIMEZONE)
    service = None
    spreadsheet_ids: dict[str, str] = {}
    reports = []
    for selected in (marketplace,) if marketplace is not None else MARKETPLACES:
        target = settings.target(selected)
        include_stocks = export_kind in (None, "stocks") and bool(target.stock_sheet_name.strip())
        include_orders = export_kind in (None, "fbs_orders") and bool(target.orders_sheet_name.strip())
        if not include_stocks and not include_orders:
            reports.append({"marketplace": selected, "skipped": True, "updated_cells": 0})
            continue
        spreadsheet_id = _spreadsheet_id(target.spreadsheet_url)
        spreadsheet_ids[selected] = spreadsheet_id
        # Validate all seven order sources before touching either destination for this marketplace.
        totals = _order_totals(selected, now=now) if include_orders else None
        snapshots = _stock_snapshot(selected, now=now) if include_stocks else None
        if service is None:
            service = legacy._google_service()
        names = [
            name.strip()
            for included, name in (
                (include_stocks, target.stock_sheet_name),
                (include_orders, target.orders_sheet_name),
            )
            if included
        ]
        sheets = legacy._sheet_metadata(service, spreadsheet_id)
        missing = [name for name in names if name not in sheets]
        if missing:
            raise StockSheetExportError(f"В таблице нет листов: {', '.join(missing)}")
        if include_orders:
            order_sheet = target.orders_sheet_name.strip()
            legacy._check_timestamp_cells(service, spreadsheet_id, {order_sheet: sheets[order_sheet]})
            _order_sheet_preparation(order_sheet, sheets[order_sheet])
        if snapshots is not None:
            report = _write_marketplace(service, spreadsheet_id, settings, selected, snapshots)
        else:
            report = {"marketplace": selected, "stocks": {"skipped": True}, "updated_cells": 0}
        if totals is not None:
            orders_report = _write_fbs_orders(service, spreadsheet_id, settings, selected, totals)
        else:
            orders_report = {"marketplace": selected, "skipped": True, "rows": 0, "updated_cells": 0}
        report["fbs_orders"] = orders_report
        report["updated_cells"] += orders_report["updated_cells"]
        reports.append(report)
    return {"spreadsheet_ids": spreadsheet_ids, "store_slugs": tuple(STORES), "marketplaces": reports}


def run_export(
    now: datetime | None = None,
    *,
    marketplace: str | None = None,
    export_kind: str | None = None,
) -> dict:
    ensure_defaults()
    now = now or datetime.now(MOSCOW_TIMEZONE)
    attempted_at = legacy._now_iso(now)
    scoped = marketplace is not None or export_kind is not None
    if not scoped:
        repository.record_attempt(attempted_at)
    try:
        report = export_all(now, marketplace=marketplace, export_kind=export_kind)
    except Exception as error:
        if not scoped:
            try:
                repository.record_result(attempted_at, error=f"{type(error).__name__}: {error}"[:2000])
            except Exception:
                logger.exception("project_sheet_export_result_record_failed")
        raise
    exported_times = [
        str(part["exported_at"])
        for item in report["marketplaces"]
        for part in (item, item.get("fbs_orders") or {})
        if part.get("exported_at") and not part.get("skipped")
    ]
    if exported_times:
        exported_at = max(exported_times)
        repository.record_success(exported_at)
        report["last_success_at"] = exported_at
    return report


def run_due(now: datetime | None = None) -> dict:
    current = now or datetime.now(MOSCOW_TIMEZONE)
    if not is_due(get_settings(), current):
        return {}
    return {"all_projects": {"ok": True, "report": run_export(current)}}
