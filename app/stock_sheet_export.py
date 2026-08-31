from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

from app import db
from app.domain import MOSCOW_TIMEZONE
from app.ff_import import google_service_account
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.repositories import stock_sheet_export as repository
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

RIMILI_SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1O3LmZuRQPr_sO4-JX87g_6gfuK2hdM-2yjaPXppPKXo/edit?gid=0#gid=0"
)
SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
HEADER_SCAN_ROWS = 25
WB_SUPPLIER_STATUSES = {"complete", "confirm", "new"}
WB_SYSTEM_STATUSES = {"ready_for_pickup", "sorted", "waiting"}
OZON_EXCLUDED_FBS_STATUSES = {"delivered", "cancelled", "not_accepted"}
METRIC_LABELS = {
    "ff_stock": "Доступно ФФ для распределения",
    "fbs_stock": "Текущий сток в продаже FBS",
    "fbo_stock": "Текущий сток в продаже FBO",
    "fbs_orders": "Заказы по ФБС",
}
EXPORT_HEADERS = (
    "АРТИКУЛ",
    "ШТРИХКОД",
    "НАЗВАНИЕ",
    "ТОТАЛ",
    "ДОСТУПНО ФФ ДЛЯ РАСПРЕДЕЛЕНИЯ",
    "ТЕКУЩИЙ СТОК В ПРОДАЖЕ FBS",
    "ТЕКУЩИЙ СТОК В ПРОДАЖЕ FBO",
)
EXPORT_METRIC_HEADERS = {
    "ff_stock": EXPORT_HEADERS[4],
    "fbs_stock": EXPORT_HEADERS[5],
    "fbo_stock": EXPORT_HEADERS[6],
}

ExportTarget = repository.ExportTarget
MarketplaceSpreadsheet = repository.MarketplaceSpreadsheet
StockSheetExportSettings = repository.StockSheetExportSettings


class StockSheetExportError(RuntimeError):
    pass


def _default_targets() -> tuple[ExportTarget, ...]:
    return tuple(
        ExportTarget(
            marketplace=marketplace,
            metric=metric,
            sheet_name=marketplace,
            key_column_name=EXPORT_HEADERS[0],
            value_column_name=EXPORT_METRIC_HEADERS[metric],
        )
        for marketplace in repository.MARKETPLACES
        for metric in repository.allowed_metrics(marketplace)
    )


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(MOSCOW_TIMEZONE)).astimezone(MOSCOW_TIMEZONE).isoformat(timespec="seconds")


def default_settings(store_slug: str, now: datetime | None = None) -> StockSheetExportSettings:
    return StockSheetExportSettings(
        store_slug=store_slug,
        enabled=store_slug == "rimili",
        schedule_kind="daily" if store_slug == "rimili" else "weekly",
        weekday=6,
        run_time="01:00",
        spreadsheets=tuple(
            MarketplaceSpreadsheet(
                marketplace=marketplace,
                spreadsheet_url=RIMILI_SPREADSHEET_URL if store_slug == "rimili" else "",
            )
            for marketplace in repository.MARKETPLACES
        ),
        updated_at=_now_iso(now),
        last_attempt_at=None,
        last_success_at=None,
        last_error=None,
        targets=_default_targets(),
    )


def _normalise_legacy_targets(settings: StockSheetExportSettings) -> StockSheetExportSettings:
    """Collapse legacy per-metric mappings to one fixed snapshot sheet per marketplace."""
    configured = {(target.marketplace, target.metric): target for target in settings.targets}
    targets: list[ExportTarget] = []
    for marketplace in repository.MARKETPLACES:
        marketplace_targets = [target for target in settings.targets if target.marketplace == marketplace]
        preferred_target = next(
            (
                configured.get((marketplace, metric))
                for metric in repository.STOCK_METRICS
                if configured.get((marketplace, metric)) is not None
                and configured[(marketplace, metric)].sheet_name.strip()
            ),
            None,
        )
        sheet_name = (
            preferred_target.sheet_name.strip()
            if preferred_target is not None
            else next(
                (target.sheet_name.strip() for target in marketplace_targets if target.sheet_name.strip()),
                marketplace,
            )
        )
        for metric in repository.STOCK_METRICS:
            previous = configured.get((marketplace, metric))
            targets.append(
                ExportTarget(
                    marketplace=marketplace,
                    metric=metric,
                    sheet_name=sheet_name,
                    key_column_name=EXPORT_HEADERS[0],
                    value_column_name=EXPORT_METRIC_HEADERS[metric],
                    id=previous.id if previous is not None else None,
                )
            )
    normalized_targets = tuple(targets)
    if normalized_targets == settings.targets:
        return settings
    targets = normalized_targets
    return replace(settings, targets=targets)


def ensure_defaults() -> None:
    for store_slug in STORES:
        existing = repository.get_settings(store_slug)
        settings = (
            _normalise_legacy_targets(existing) if existing is not None else default_settings(store_slug)
        )
        if settings != existing:
            repository.save_settings(settings)


def get_settings(store_slug: str) -> StockSheetExportSettings:
    if store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    existing = repository.get_settings(store_slug)
    return _normalise_legacy_targets(existing) if existing is not None else default_settings(store_slug)


def list_settings() -> list[StockSheetExportSettings]:
    return [get_settings(store_slug) for store_slug in STORES]


def validate_settings(settings: StockSheetExportSettings) -> None:
    if settings.store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    if settings.schedule_kind not in {"daily", "weekly"}:
        raise ValueError("Периодичность должна быть ежедневной или еженедельной")
    if not 0 <= settings.weekday <= 6:
        raise ValueError("Некорректный день недели")
    if not TIME_RE.fullmatch(settings.run_time):
        raise ValueError("Время нужно указать в формате ЧЧ:ММ")
    expected = {
        (marketplace, metric)
        for marketplace in repository.MARKETPLACES
        for metric in repository.allowed_metrics(marketplace)
    }
    actual = {(target.marketplace, target.metric) for target in settings.targets}
    if actual != expected or len(settings.targets) != len(expected):
        raise ValueError("Для каждого маркетплейса должен быть указан один лист выгрузки")
    if any(
        len({target.sheet_name for target in settings.targets if target.marketplace == marketplace}) != 1
        for marketplace in repository.MARKETPLACES
    ):
        raise ValueError("Для каждого маркетплейса должен быть указан один лист выгрузки")
    configured_spreadsheets = [item.marketplace for item in settings.spreadsheets]
    if set(configured_spreadsheets) != set(repository.MARKETPLACES) or len(configured_spreadsheets) != len(
        repository.MARKETPLACES
    ):
        raise ValueError("Для каждого маркетплейса должна быть указана одна Google Таблица")
    target_marketplaces = {target.marketplace for target in settings.targets}
    if settings.enabled:
        for marketplace in target_marketplaces:
            if not SPREADSHEET_ID_RE.search(settings.spreadsheet_url_for(marketplace)):
                raise ValueError(f"Укажите корректную ссылку на Google Таблицу для {marketplace}")
    for target in settings.targets:
        values = (target.sheet_name, target.key_column_name, target.value_column_name)
        if any(not value.strip() for value in values):
            raise ValueError("Название листа и колонок не может быть пустым")
        if any(len(value) > 200 for value in values):
            raise ValueError("Название листа или колонки слишком длинное")


def save_settings(settings: StockSheetExportSettings) -> None:
    validate_settings(settings)
    repository.save_settings(settings)


def _parse_run_time(value: str) -> time:
    hours, minutes = (int(part) for part in value.split(":"))
    return time(hour=hours, minute=minutes, tzinfo=MOSCOW_TIMEZONE)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW_TIMEZONE)
    return parsed.astimezone(MOSCOW_TIMEZONE)


def scheduled_at(settings: StockSheetExportSettings, now: datetime) -> datetime:
    local_now = now.astimezone(MOSCOW_TIMEZONE)
    run_at = _parse_run_time(settings.run_time)
    target_date = local_now.date()
    if settings.schedule_kind == "weekly":
        target_date -= timedelta(days=(target_date.weekday() - settings.weekday) % 7)
    target = datetime.combine(target_date, run_at)
    if target > local_now:
        target -= timedelta(days=1 if settings.schedule_kind == "daily" else 7)
    return target


def is_due(settings: StockSheetExportSettings, now: datetime | None = None) -> bool:
    if not settings.enabled:
        return False
    current = (now or datetime.now(MOSCOW_TIMEZONE)).astimezone(MOSCOW_TIMEZONE)
    target = scheduled_at(settings, current)
    last_attempt = _parse_timestamp(settings.last_attempt_at)
    updated_at = _parse_timestamp(settings.updated_at)
    references = tuple(value for value in (last_attempt, updated_at) if value is not None)
    last_relevant_event = max(references) if references else None
    return last_relevant_event is None or last_relevant_event < target


def _unix_bounds(start: date, end: date) -> tuple[int, int]:
    """Convert a Moscow [start, end) interval to WB's inclusive Unix bounds."""
    date_from = int(datetime.combine(start, time.min, tzinfo=MOSCOW_TIMEZONE).timestamp())
    date_to = int(datetime.combine(end, time.min, tzinfo=MOSCOW_TIMEZONE).timestamp()) - 1
    return date_from, date_to


def wb_completed_week(now: datetime | None = None) -> tuple[date, date]:
    """Return the seven complete Moscow calendar days before the export date."""
    export_day = (now or datetime.now(MOSCOW_TIMEZONE)).astimezone(MOSCOW_TIMEZONE).date()
    return export_day - timedelta(days=7), export_day


def _ozon_rfc3339_bounds(start: date, end: date) -> tuple[str, str]:
    """Convert a Moscow [start, end) interval to UTC RFC3339 bounds for Ozon."""
    start_at = datetime.combine(start, time.min, tzinfo=MOSCOW_TIMEZONE).astimezone(UTC)
    end_at = datetime.combine(end, time.min, tzinfo=MOSCOW_TIMEZONE).astimezone(UTC)
    return (
        start_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        end_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def _wb_fbs_order_totals(store_slug: str, now: datetime | None = None) -> dict[str, int]:
    """Get one current WB FBS weekly snapshot and validate both order statuses."""
    token = wb_tokens.get_token(store_slug)
    orders_by_id: dict[int, dict] = {}
    start, end = wb_completed_week(now)
    date_from, date_to = _unix_bounds(start, end)
    for order in wb_api.get_fbs_orders(token, date_from, date_to):
        try:
            order_id = int(order.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if order_id:
            orders_by_id[order_id] = order

    statuses = wb_api.get_fbs_order_statuses(token, list(orders_by_id))
    totals: dict[str, int] = defaultdict(int)
    for order_id, order in orders_by_id.items():
        status = statuses.get(order_id) or {}
        supplier_status = str(status.get("supplierStatus") or "").casefold()
        wb_status = str(status.get("wbStatus") or "").casefold()
        if supplier_status not in WB_SUPPLIER_STATUSES or wb_status not in WB_SYSTEM_STATUSES:
            continue
        skus = order.get("skus") or ()
        article = str((skus[0] if skus else "") or order.get("nmId") or order.get("article") or "").strip()
        if article:
            totals[article] += 1
    return dict(totals)


def _ozon_fbs_order_totals(store_slug: str, now: datetime | None = None) -> dict[str, int]:
    """Get Ozon FBS units for the same seven complete Moscow days as WB."""
    client_id, api_key = ozon_tokens.get_credentials(store_slug)
    start, end = wb_completed_week(now)
    since, to = _ozon_rfc3339_bounds(start, end)
    postings_by_id: dict[str, dict] = {}
    for posting in ozon_api.get_fbs_postings_v4(client_id, api_key, since, to):
        posting_id = str(
            posting.get("posting_number") or posting.get("order_number") or posting.get("order_id") or ""
        ).strip()
        if posting_id:
            postings_by_id[posting_id] = posting

    totals: dict[str, int] = defaultdict(int)
    for posting in postings_by_id.values():
        status = str(posting.get("status") or "").strip().casefold()
        if status in OZON_EXCLUDED_FBS_STATUSES:
            continue
        for product in posting.get("products") or posting.get("items") or ():
            article = str(
                product.get("offer_id") or product.get("sku") or product.get("product_id") or ""
            ).strip()
            try:
                quantity = max(1, int(product.get("quantity") or 1))
            except (TypeError, ValueError):
                quantity = 1
            if article:
                totals[article] += quantity
    return dict(totals)


def _spreadsheet_id(url: str) -> str:
    match = SPREADSHEET_ID_RE.search(url.strip())
    if not match:
        raise StockSheetExportError("Некорректная ссылка на Google Таблицу")
    return match.group(1)


def _google_service():
    if not google_service_account.has_credentials():
        raise StockSheetExportError(
            f"Нет ключа сервисного аккаунта: {google_service_account.CREDENTIALS_PATH}"
        )
    try:
        from googleapiclient.discovery import build
    except ImportError as error:
        raise StockSheetExportError(
            "Для выгрузки нужны пакеты google-api-python-client и google-auth"
        ) from error
    return build(
        "sheets",
        "v4",
        credentials=google_service_account.get_credentials(),
        cache_discovery=False,
    )


def _header_key(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ").strip()).casefold()


def _article_key(value: object) -> str:
    result = str(value or "").replace("\xa0", " ").strip()
    if result.endswith(".0") and result[:-2].isdigit():
        result = result[:-2]
    return result.casefold()


def _column_letter(index: int) -> str:
    result = ""
    current = index + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _quote_sheet(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _sheet_identifier(value: object) -> str | int:
    text = str(value or "").strip().removeprefix("'").strip()
    if text.isdecimal() and len(text) <= 15:
        return int(text)
    return text


def _find_header(rows: list[list[object]], name: str, sheet_name: str) -> tuple[int, int]:
    needle = _header_key(name)
    matches = [
        (row_index, column_index)
        for row_index, row in enumerate(rows[:HEADER_SCAN_ROWS])
        for column_index, value in enumerate(row)
        if _header_key(value) == needle
    ]
    if not matches:
        raise StockSheetExportError(
            f"Лист «{sheet_name}»: колонка «{name}» не найдена в первых {HEADER_SCAN_ROWS} строках"
        )
    if len(matches) > 1:
        raise StockSheetExportError(
            f"Лист «{sheet_name}»: колонка «{name}» встречается в первых "
            f"{HEADER_SCAN_ROWS} строках несколько раз"
        )
    return matches[0]


def _catalog_aliases(catalog: list[dict]) -> tuple[dict[str, set[str]], set[str]]:
    aliases: dict[str, set[str]] = defaultdict(set)
    articles: set[str] = set()
    for item in catalog:
        article = str(item.get("article") or "").strip()
        if not article:
            continue
        articles.add(article)
        base_article = _article_key(article.partition(" / ")[0])
        if base_article:
            aliases[base_article].add(article)
        for field in ("article", "barcode", "mp_sku", "mp_product_id"):
            alias = _article_key(item.get(field))
            if alias:
                aliases[alias].add(article)
    return aliases, articles


def _metric_values(
    store_slug: str,
    marketplace: str,
    catalog: list[dict],
    metrics: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, int]]:
    articles = {str(item["article"]): 0 for item in catalog if item.get("article")}
    aliases, _ = _catalog_aliases(catalog)

    def with_zeroes(values: dict[str, int]) -> dict[str, int]:
        result = dict(articles)
        for source_key, quantity in values.items():
            source = str(source_key)
            matches = aliases.get(_article_key(source))
            if not matches:
                result[source] = result.get(source, 0) + int(quantity or 0)
                continue
            exact = source if source in matches else None
            target = exact or sorted(matches)[0]
            result[target] = result.get(target, 0) + int(quantity or 0)
        return result

    values: dict[str, dict[str, int]] = {}
    if "ff_stock" in metrics:
        values["ff_stock"] = with_zeroes(db.get_ff_available_totals(store_slug, marketplace=marketplace))
    if "fbs_stock" in metrics:
        values["fbs_stock"] = with_zeroes(db.get_mp_stock_totals(store_slug, marketplace, "fbs"))
    if "fbo_stock" in metrics:
        values["fbo_stock"] = with_zeroes(db.get_mp_stock_totals(store_slug, marketplace, "fbo"))
    if "fbs_orders" in metrics:
        if marketplace == "WB":
            order_totals = _wb_fbs_order_totals(store_slug, now)
        elif marketplace == "OZON":
            order_totals = _ozon_fbs_order_totals(store_slug, now)
        else:
            raise StockSheetExportError("Заказы FBS пока не поддерживаются для этого маркетплейса")
        values["fbs_orders"] = with_zeroes(order_totals)
    return values


def _sheet_names(service, spreadsheet_id: str) -> set[str]:
    metadata = (
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties(title)",
            includeGridData=False,
        )
        .execute()
    )
    return {str(sheet.get("properties", {}).get("title") or "") for sheet in metadata.get("sheets") or ()}


def _target_sheet_name(settings: StockSheetExportSettings, marketplace: str) -> str:
    for target in settings.targets:
        if target.marketplace == marketplace:
            return target.sheet_name
    return ""


def _stores_for_destination(
    settings: StockSheetExportSettings,
    marketplace: str,
    known_settings: list[StockSheetExportSettings],
) -> tuple[str, ...]:
    spreadsheet_id = _spreadsheet_id(settings.spreadsheet_url_for(marketplace))
    sheet_name = _target_sheet_name(settings, marketplace)
    settings_by_store = {item.store_slug: item for item in known_settings}
    settings_by_store[settings.store_slug] = settings
    matched: set[str] = set()
    for store_slug, candidate in settings_by_store.items():
        if _target_sheet_name(candidate, marketplace) != sheet_name:
            continue
        try:
            candidate_spreadsheet_id = _spreadsheet_id(candidate.spreadsheet_url_for(marketplace))
        except (KeyError, StockSheetExportError):
            continue
        if candidate_spreadsheet_id == spreadsheet_id:
            matched.add(store_slug)
    return tuple(store_slug for store_slug in STORES if store_slug in matched)


def _combined_stock_snapshot(
    store_slugs: tuple[str, ...],
    marketplace: str,
    *,
    now: datetime | None = None,
) -> tuple[list[dict], dict[str, dict[str, int]]]:
    catalog_by_key: dict[str, dict] = {}
    values_by_metric: dict[str, dict[str, int]] = {metric: {} for metric in repository.STOCK_METRICS}
    for store_slug in store_slugs:
        catalog = db.get_catalog_items(store_slug, marketplace)
        store_values = _metric_values(
            store_slug,
            marketplace,
            catalog,
            repository.STOCK_METRICS,
            now=now,
        )
        for item in catalog:
            article = str(item.get("article") or "").strip()
            if not article:
                continue
            article_key = article.casefold()
            combined_item = catalog_by_key.get(article_key)
            if combined_item is None:
                combined_item = dict(item)
                combined_item["article"] = article
                catalog_by_key[article_key] = combined_item
            else:
                for field in ("barcode", "name"):
                    if not combined_item.get(field) and item.get(field):
                        combined_item[field] = item[field]
            combined_article = str(combined_item["article"])
            for metric in repository.STOCK_METRICS:
                quantity = int(store_values.get(metric, {}).get(article, 0) or 0)
                metric_values = values_by_metric[metric]
                metric_values[combined_article] = metric_values.get(combined_article, 0) + quantity
    return list(catalog_by_key.values()), values_by_metric


def _write_marketplace(
    service,
    spreadsheet_id: str,
    settings: StockSheetExportSettings,
    marketplace: str,
    catalog: list[dict],
    values_by_metric: dict[str, dict[str, int]],
) -> dict:
    targets = [target for target in settings.targets if target.marketplace == marketplace]
    if not targets:
        return {"marketplace": marketplace, "metrics": {}, "updated_cells": 0}
    existing_sheets = _sheet_names(service, spreadsheet_id)
    sheet_names = list(dict.fromkeys(target.sheet_name for target in targets))
    missing = sorted(set(sheet_names) - existing_sheets)
    if missing:
        raise StockSheetExportError(f"В таблице нет листов: {', '.join(missing)}")

    data_rows: list[list[object]] = []
    for item in catalog:
        article = str(item.get("article") or "").strip()
        if not article:
            continue
        ff_stock = int(values_by_metric.get("ff_stock", {}).get(article, 0) or 0)
        fbs_stock = int(values_by_metric.get("fbs_stock", {}).get(article, 0) or 0)
        fbo_stock = int(values_by_metric.get("fbo_stock", {}).get(article, 0) or 0)
        data_rows.append(
            [
                _sheet_identifier(article),
                _sheet_identifier(item.get("barcode")),
                str(item.get("name") or "").strip(),
                ff_stock + fbs_stock + fbo_stock,
                ff_stock,
                fbs_stock,
                fbo_stock,
            ]
        )

    values = [list(EXPORT_HEADERS), *data_rows]
    clear_ranges = [f"{_quote_sheet(sheet_name)}!A2:G" for sheet_name in sheet_names]
    (
        service.spreadsheets()
        .values()
        .batchClear(
            spreadsheetId=spreadsheet_id,
            body={"ranges": clear_ranges},
        )
        .execute()
    )
    updates = [
        {
            "range": f"{_quote_sheet(sheet_name)}!A2:G{len(values) + 1}",
            "values": values,
        }
        for sheet_name in sheet_names
    ]
    if updates:
        (
            service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "RAW", "data": updates},
            )
            .execute()
        )
    row_count = len(data_rows)
    return {
        "marketplace": marketplace,
        "sheets": sheet_names,
        "rows": row_count,
        "metrics": {metric: {"rows": row_count} for metric in repository.STOCK_METRICS},
        "updated_cells": len(values) * len(EXPORT_HEADERS) * len(sheet_names),
    }


def export_store(store_slug: str, now: datetime | None = None) -> dict:
    settings = get_settings(store_slug)
    validate_settings(settings)
    known_settings = list_settings()
    service = _google_service()
    reports = []
    spreadsheet_ids: dict[str, str] = {}
    for marketplace in repository.MARKETPLACES:
        targets = tuple(target for target in settings.targets if target.marketplace == marketplace)
        if not targets:
            continue
        spreadsheet_id = _spreadsheet_id(settings.spreadsheet_url_for(marketplace))
        spreadsheet_ids[marketplace] = spreadsheet_id
        combined_store_slugs = _stores_for_destination(
            settings,
            marketplace,
            known_settings,
        )
        catalog, values_by_metric = _combined_stock_snapshot(
            combined_store_slugs,
            marketplace,
            now=now,
        )
        marketplace_report = _write_marketplace(
            service,
            spreadsheet_id,
            settings,
            marketplace,
            catalog,
            values_by_metric,
        )
        marketplace_report["store_slugs"] = combined_store_slugs
        reports.append(marketplace_report)
    return {
        "store_slug": store_slug,
        "spreadsheet_ids": spreadsheet_ids,
        "marketplaces": reports,
    }


def run_store(store_slug: str, now: datetime | None = None) -> dict:
    attempted_at = _now_iso(now)
    logger.info("stock_sheet_export_started store=%s", store_slug)
    try:
        repository.record_attempt(store_slug, attempted_at)
    except Exception:
        logger.exception("stock_sheet_export_attempt_record_failed store=%s", store_slug)
        raise
    try:
        report = export_store(store_slug, now)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"[:2000]
        logger.exception("stock_sheet_export_failed store=%s", store_slug)
        try:
            repository.record_result(store_slug, attempted_at, error=message)
        except Exception:
            logger.exception("stock_sheet_export_result_record_failed store=%s", store_slug)
        raise
    try:
        repository.record_result(store_slug, attempted_at, error=None)
    except Exception:
        logger.exception("stock_sheet_export_result_record_failed store=%s", store_slug)
        raise
    updated_cells = sum(int(item.get("updated_cells") or 0) for item in report["marketplaces"])
    logger.info(
        "stock_sheet_export_completed store=%s marketplaces=%s updated_cells=%s",
        store_slug,
        len(report["marketplaces"]),
        updated_cells,
    )
    return report


def run_due(
    now: datetime | None = None,
    store_slugs: tuple[str, ...] | None = None,
) -> dict[str, dict]:
    current = now or datetime.now(MOSCOW_TIMEZONE)
    allowed_stores = set(STORES if store_slugs is None else store_slugs)
    report: dict[str, dict] = {}
    for settings in list_settings():
        if settings.store_slug not in allowed_stores:
            continue
        if not is_due(settings, current):
            continue
        try:
            report[settings.store_slug] = {"ok": True, "report": run_store(settings.store_slug, current)}
        except Exception as error:
            report[settings.store_slug] = {
                "ok": False,
                "error": f"{type(error).__name__}: {error}"[:2000],
            }
    failed_stores = [store_slug for store_slug, item in report.items() if not item["ok"]]
    if failed_stores:
        raise StockSheetExportError("Не выполнена выгрузка магазинов: " + ", ".join(failed_stores))
    return report
