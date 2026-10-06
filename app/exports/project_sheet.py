from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import PROJECT_STORE_ALIASES, STORES
from app.exports import stock_sheet as legacy
from app.jobs import locks as sync_locks
from app.repositories import project_sheet_export as repository

logger = logging.getLogger(__name__)

MARKETPLACES = repository.MARKETPLACES
EXPORT_KINDS = legacy.EXPORT_KINDS
PROJECT_HEADER = "ПРОЕКТ"
EXPORT_HEADERS = ("КЛЮЧ", PROJECT_HEADER, *legacy.EXPORT_HEADERS)
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
        _order_column_index(target.orders_quantity_column)
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
                orders_quantity_column=target.orders_quantity_column.strip().upper(),
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


def _order_column_index(value: str) -> int:
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", value):
        raise ValueError("Укажите столбец количества FBS латинскими буквами, например C, K или AA")
    column = 0
    for letter in value:
        column = column * 26 + ord(letter) - ord("A") + 1
    return column - 1


@dataclass
class OrderSheetPlan:
    sheet_name: str
    column: str
    updates: list[dict]
    clear_range: str | None
    prepare_requests: list[dict]
    row_count: int
    store_slugs: tuple[str, ...]
    warnings: list[str]


def _plan_fbs_orders(
    service,
    spreadsheet_id: str,
    target: MarketplaceExportTarget,
    totals: dict[str, dict[str, int]],
    sheet: dict,
) -> OrderSheetPlan:
    sheet_name = target.orders_sheet_name.strip()
    column_index = _order_column_index(target.orders_quantity_column)
    column = legacy._column_letter(column_index)
    quoted_sheet = legacy._quote_sheet(sheet_name)
    rows = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=quoted_sheet, valueRenderOption="FORMATTED_VALUE")
        .execute(num_retries=legacy.GOOGLE_REQUEST_RETRIES)
        .get("values", [])
    )
    headers = []
    for index, row in enumerate(rows):
        normalized = [legacy._header_key(value) for value in row]
        projects = [col for col, value in enumerate(normalized) if value in {"проект", "project"}]
        articles = [col for col, value in enumerate(normalized) if value in {"артикул", "article"}]
        if projects and articles:
            if len(projects) != 1 or len(articles) != 1:
                raise StockSheetExportError(
                    f"Лист «{sheet_name}»: неоднозначные колонки проекта или артикула"
                )
            headers.append((index, projects[0], articles[0]))
    if len(headers) != 1:
        raise StockSheetExportError(
            f"Лист «{sheet_name}»: нужна одна шапка с колонками ПРОЕКТ (Project) и АРТИКУЛ (Article)"
        )
    header_row, project_column, article_column = headers[0]
    header = rows[header_row]
    if column_index < len(header) and legacy._header_key(header[column_index]) in {
        "проект",
        "project",
        "артикул",
        "article",
        "ключ",
        "key",
        "barcode",
        "баркод",
        "штрихкод",
        "название",
        "name",
    }:
        raise StockSheetExportError(
            f"Лист «{sheet_name}»: столбец {column} содержит данные товара; выберите столбец количества"
        )
    first_row = header_row + 2
    last_row = len(rows)
    grid = sheet["properties"].get("gridProperties", {})
    grid_columns = grid.get("columnCount", column_index + 1)
    if column_index < grid_columns and first_row <= grid.get("rowCount", first_row):
        existing_values = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=f"{quoted_sheet}!{column}{first_row}:{column}",
                valueRenderOption="FORMULA",
            )
            .execute(num_retries=legacy.GOOGLE_REQUEST_RETRIES)
            .get("values", [])
        )
        last_row = max(last_row, first_row - 1 + len(existing_values))
    if any(
        merge.get("startRowIndex", 0) < last_row
        and merge.get("endRowIndex", 0) >= first_row
        and merge.get("startColumnIndex", 0) <= column_index < merge.get("endColumnIndex", 0)
        for merge in sheet.get("merges", ())
    ):
        raise StockSheetExportError(
            f"Лист «{sheet_name}»: колонка {column} в области записи объединена с другими ячейками; разделите их перед выгрузкой"
        )
    quantities = {
        (store_slug, legacy._article_key(str(article).strip().removeprefix("'"))): int(quantity or 0)
        for store_slug, articles in totals.items()
        for article, quantity in articles.items()
    }
    updates, matched = [], set()
    skipped_rows = 0
    row_count = 0
    for row_index, row in enumerate(rows[header_row + 1 :], header_row + 1):
        project = str(row[project_column] or "").strip() if project_column < len(row) else ""
        article = (
            legacy._article_key(str(row[article_column] or "").strip().removeprefix("'"))
            if article_column < len(row)
            else ""
        )
        store = PROJECT_STORE_ALIASES.get(project.casefold())
        if not article or store not in totals:
            if article or project:
                skipped_rows += 1
            continue
        key = (store, article)
        if key in quantities:
            matched.add(key)
        # Group adjacent matched rows without touching gaps or other columns.
        if updates and updates[-1]["last_row"] == row_index:
            updates[-1]["values"].append([quantities.get(key, 0)])
            updates[-1]["last_row"] = row_index + 1
        else:
            updates.append(
                {"first_row": row_index + 1, "last_row": row_index + 1, "values": [[quantities.get(key, 0)]]}
            )
        row_count += 1
    data = [
        {
            "range": f"{quoted_sheet}!{column}{item['first_row']}:{column}{item['last_row']}",
            "values": item["values"],
        }
        for item in updates
    ]
    missing = sum(quantity > 0 and key not in matched for key, quantity in quantities.items())
    warnings = []
    if skipped_rows:
        warnings.append(f"{sheet_name}: пропущено строк без известного проекта или артикула: {skipped_rows}")
    if missing:
        warnings.append(f"{sheet_name}: товаров с заказами, не найденных в листе: {missing}")
    if not row_count:
        warnings.append(f"{sheet_name}: не найдено строк товаров для обновления FBS-заказов")
    prepare_requests = []
    if data and grid_columns <= column_index:
        prepare_requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet["properties"]["sheetId"],
                        "gridProperties": {"columnCount": column_index + 1},
                    },
                    "fields": "gridProperties.columnCount",
                }
            }
        )
    clear_range = (
        f"{quoted_sheet}!{column}{first_row}:{column}{last_row}"
        if last_row >= first_row and (column_index < grid_columns or data)
        else None
    )
    return OrderSheetPlan(
        sheet_name, column, data, clear_range, prepare_requests, row_count, tuple(totals), warnings
    )


def _write_fbs_orders(service, spreadsheet_id: str, marketplace: str, plan: OrderSheetPlan) -> dict:
    report = {
        "marketplace": marketplace,
        "sheet": plan.sheet_name,
        "quantity_column": plan.column,
        "period_days": legacy.FBS_ORDER_LOOKBACK_DAYS,
        "rows": plan.row_count,
        "updated_cells": plan.row_count,
        "store_slugs": plan.store_slugs,
        "warnings": plan.warnings,
    }
    if not plan.updates and not plan.clear_range:
        return {**report, "skipped": True}
    if plan.prepare_requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": plan.prepare_requests}
        ).execute(num_retries=legacy.GOOGLE_REQUEST_RETRIES)
    if plan.clear_range:
        service.spreadsheets().values().batchClear(
            spreadsheetId=spreadsheet_id, body={"ranges": [plan.clear_range]}
        ).execute(num_retries=legacy.GOOGLE_REQUEST_RETRIES)
    if plan.updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"valueInputOption": "RAW", "data": plan.updates}
        ).execute(num_retries=legacy.GOOGLE_REQUEST_RETRIES)
    report["exported_at"] = legacy._now_iso()
    return report


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
        orders_plan = None
        if totals is not None:
            orders_plan = _plan_fbs_orders(
                service, spreadsheet_id, target, totals, sheets[target.orders_sheet_name.strip()]
            )
        if snapshots is not None:
            report = _write_marketplace(service, spreadsheet_id, settings, selected, snapshots)
        else:
            report = {"marketplace": selected, "stocks": {"skipped": True}, "updated_cells": 0}
        if orders_plan is not None:
            orders_report = _write_fbs_orders(service, spreadsheet_id, selected, orders_plan)
        else:
            orders_report = {"marketplace": selected, "skipped": True, "rows": 0, "updated_cells": 0}
        report["fbs_orders"] = orders_report
        report["warnings"] = [*report.get("warnings", []), *orders_report.get("warnings", [])]
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
