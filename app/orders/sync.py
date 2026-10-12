"""Load marketplace orders, validate FBS snapshots and persist the shared registry."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from app import db
from app.config import settings
from app.core.domain import MARKETPLACES, MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.orders.normalizers import ozon as ozon_normalizer
from app.orders.normalizers import wb as wb_normalizer
from app.orders.normalizers import yandex as yandex_normalizer
from app.orders.normalizers.common import integer
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.repositories import sales as sales_repository
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex_api
from app.yandex import tokens as yandex_tokens

logger = logging.getLogger(__name__)
MOSCOW = MOSCOW_TIMEZONE
MAX_RANGE_DAYS = {"WB": 90, "OZON": 365, "YANDEX MARKET": 365}
SOURCE_WINDOW_DAYS = {"WB": 90, "OZON": 30, "YANDEX MARKET": 30}
INITIAL_LOOKBACK_DAYS = {"WB": 90, "OZON": 365, "YANDEX MARKET": 365}
REFRESH_LOOKBACK_DAYS = 2


def _now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def _windows(start: date, end: date, days: int = 30) -> Iterable[tuple[date, date]]:

    cursor = start
    while cursor < end:
        next_cursor = min(cursor + timedelta(days=days), end)
        yield cursor, next_cursor
        cursor = next_cursor


def _load_wb_fbs_lines(
    store_slug: str,
    token: str,
    start: date,
    end: date,
) -> tuple[list[dict], list[str]]:
    orders_by_id: dict[int, dict] = {}
    warnings: list[str] = []
    for window_start, window_end in _windows(start, end, 30):
        date_from = int(datetime.combine(window_start, time.min, tzinfo=MOSCOW).timestamp())
        date_to = int(datetime.combine(window_end, time.min, tzinfo=MOSCOW).timestamp()) - 1
        try:
            for order in wb_api.get_fbs_orders(token, date_from, date_to):
                order_id = integer(order.get("id"))
                if order_id:
                    orders_by_id[order_id] = order
        except Exception as exc:
            warnings.append(
                f"FBS {window_start:%d.%m}-{window_end - timedelta(days=1):%d.%m}: "
                f"{type(exc).__name__}: {exc}"
            )

    statuses: dict[int, dict] = {}
    if orders_by_id:
        try:
            statuses = wb_api.get_fbs_order_statuses(token, list(orders_by_id))
        except Exception as exc:
            warnings.append(f"FBS статусы: {type(exc).__name__}: {exc}")
    return wb_normalizer.normalize_fbs(store_slug, list(orders_by_id.values()), statuses), warnings


def _sync_wb(store_slug: str, start: date, end: date) -> tuple[list[dict], list[str]]:
    token = wb_tokens.get_token(store_slug)
    date_from = datetime.combine(start, time.min).isoformat(timespec="seconds")
    orders = wb_api.get_orders(token, date_from, max_pages=settings.wb_sales_max_pages)
    warnings: list[str] = []
    fbs_lines, fbs_warnings = _load_wb_fbs_lines(store_slug, token, start, end)
    warnings.extend(fbs_warnings)
    try:
        sales_rows = wb_api.get_sales(token, date_from, max_pages=settings.wb_sales_max_pages)
    except Exception as exc:
        sales_rows = []
        warnings.append(f"продажи: {type(exc).__name__}: {exc}")
    statistics_lines = wb_normalizer.normalize(store_slug, orders, sales_rows)
    return wb_normalizer.merge_lines(statistics_lines, fbs_lines), warnings


def _sync_ozon(
    store_slug: str, start: date, end: date, *, strict_fbs: bool = False
) -> tuple[list[dict], list[str]]:
    client_id, api_key = ozon_tokens.get_credentials(store_slug)
    lines: list[dict] = []
    warnings: list[str] = []
    for window_start, window_end in _windows(start, end, SOURCE_WINDOW_DAYS["OZON"]):
        since = datetime.combine(window_start, time.min, tzinfo=MOSCOW).astimezone(UTC).isoformat()
        to = (
            datetime.combine(window_end, time.min, tzinfo=MOSCOW).astimezone(UTC) - timedelta(microseconds=1)
        ).isoformat()
        loaders = (
            ("fbo", ozon_api.get_fbo_postings),
            ("fbs", ozon_api.get_fbs_postings_v4),
        )
        for scheme, loader in loaders:
            if strict_fbs and scheme != "fbs":
                continue
            try:
                postings = loader(client_id, api_key, since, to)
                lines.extend(
                    _fbs_snapshot_lines(store_slug, "OZON", postings)
                    if strict_fbs
                    else ozon_normalizer.normalize(store_slug, postings, scheme)
                )
            except Exception as exc:
                if strict_fbs:
                    raise
                warnings.append(
                    f"{scheme.upper()} {window_start:%d.%m}-{window_end:%d.%m}: {type(exc).__name__}: {exc}"
                )
    if not lines and warnings:
        raise RuntimeError("; ".join(warnings[:3]))
    return lines, warnings


def _resolve_yandex_business_id(store_slug: str, api_key: str, *, require_unique: bool = False) -> int:
    business_id = yandex_tokens.get_business_id(store_slug)
    if business_id:
        return business_id
    campaigns = yandex_api.get_campaigns(api_key)
    ids = sorted(
        {
            integer(campaign.get("business", {}).get("id") or campaign.get("businessId"))
            for campaign in campaigns
        }
        - {0}
    )
    if not ids:
        raise RuntimeError("Яндекс Маркет не вернул businessId кабинета")
    if require_unique and len(ids) != 1:
        raise FbsOrderRefreshError("Укажите business_id магазина: ключ ЯМ открывает несколько кабинетов")
    return ids[0]


def _sync_yandex(
    store_slug: str, start: date, end: date, *, strict_fbs: bool = False
) -> tuple[list[dict], list[str]]:
    api_key = yandex_tokens.get_api_key(store_slug)
    business_id = _resolve_yandex_business_id(store_slug, api_key, require_unique=strict_fbs)
    orders: list[dict] = []
    warnings: list[str] = []
    for window_start, window_end in _windows(start, end, SOURCE_WINDOW_DAYS["YANDEX MARKET"]):
        try:
            orders.extend(
                yandex_api.get_business_orders(
                    api_key,
                    business_id,
                    window_start.isoformat(),
                    window_end.isoformat(),
                )
            )
        except Exception as exc:
            if strict_fbs:
                raise
            warnings.append(f"{window_start:%d.%m}-{window_end:%d.%m}: {type(exc).__name__}: {exc}")
    if not orders and warnings:
        raise RuntimeError("; ".join(warnings[:3]))
    return (
        _fbs_snapshot_lines(store_slug, "YANDEX MARKET", orders)
        if strict_fbs
        else yandex_normalizer.normalize(store_slug, orders)
    ), warnings


class FbsOrderRefreshError(RuntimeError):
    """The requested order window cannot safely be published."""


def _fbs_snapshot_lines(store_slug: str, marketplace: str, orders: list[dict]) -> list[dict]:
    """Reuse sales identities/status rules, but only update fields supplied by the order API.

    This refresh is for quantities/statuses, not an accounting or returns sync.
    In particular v4 postings requested without financial data must not erase it.
    """
    yandex = marketplace == "YANDEX MARKET"
    result: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for order in orders:
        if not isinstance(order, dict):
            raise ValueError("Некорректный заказ")
        order_id = order.get("orderId") or order.get("id") if yandex else order.get("posting_number")
        created = (
            (order.get("creationDate") or order.get("createdAt"))
            if yandex
            else (order.get("created_at") or order.get("in_process_at"))
        )
        if not order_id or not str(order.get("status") or "").strip() or not created:
            raise ValueError("В заказе отсутствуют обязательные поля")
        # Do not let the permissive historical normalizers invent today's date.
        datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        if yandex and not order.get("programType"):
            raise ValueError("В заказе отсутствует схема работы")
        items = order.get("items" if yandex else "products")
        if not isinstance(items, list) or not items:
            raise ValueError("В заказе отсутствует состав товаров")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Некорректный товар заказа")
            identity = (
                (item.get("id") or item.get("marketSku") or item.get("offerId"))
                if yandex
                else (item.get("sku") or item.get("product_id") or item.get("offer_id"))
            )
            article = (
                (item.get("offerId") or item.get("shopSku"))
                if yandex
                else (item.get("offer_id") or item.get("product_id"))
            )
            quantity = item.get("count" if yandex else "quantity")
            if not identity or not str(article or "").strip() or isinstance(quantity, bool):
                raise ValueError("В товаре отсутствуют обязательные поля")
            if int(str(quantity)) < 0:
                raise ValueError("Отрицательное количество товара")
        normalized = (
            yandex_normalizer.normalize(store_slug, [order])
            if yandex
            else (ozon_normalizer.normalize(store_slug, [order], "fbs"))
        )
        for line, item in zip(normalized, items, strict=True):
            key = (line["order_key"], line["line_key"])
            if key in seen:
                raise ValueError("Повтор строки заказа в выгрузке")
            seen.add(key)
            fields = {
                "store_slug",
                "marketplace",
                "order_key",
                "line_key",
                "external_order_id",
                "scheme",
                "status",
                "article",
                "ordered_at",
                "quantity",
                "cancelled_quantity",
                "sold_quantity",
            }
            for target, source in (
                ("substatus", order.get("substatus")),
                (
                    "source_updated_at",
                    (order.get("updateDate") or order.get("updatedAt"))
                    if yandex
                    else (order.get("updated_at") or order.get("status_updated_at")),
                ),
                ("barcode", item.get("barcode")),
                ("name", item.get("offerName") or item.get("name")),
            ):
                if source is not None:
                    fields.add(target)
            line = {field: line[field] for field in fields}
            line["quantity"] = int(str(item["count" if yandex else "quantity"]))
            for field in ("cancelled_quantity", "sold_quantity"):
                line[field] = min(line[field], line["quantity"])
            result.append(line)
    return result


def refresh_fbs_order_totals(
    store_slug: str, marketplace: str, start: date, end: date, statuses: tuple[str, ...]
) -> dict[str, int]:
    """Fetch every page before reconciling the exact Moscow window and reading totals.

    Returned totals belong to this validated snapshot: another job cannot change
    them between the registry transaction and a Google publication.
    """
    if end <= start or marketplace not in {"OZON", "YANDEX MARKET"}:
        raise ValueError("Некорректный период или маркетплейс FBS")
    attempted_at = _now_iso()
    try:
        loader = _sync_ozon if marketplace == "OZON" else _sync_yandex
        lines, warnings = loader(store_slug, start, end, strict_fbs=True)
        if warnings:
            raise ValueError("Загрузка завершилась с предупреждениями")
        lines = [
            line
            for line in lines
            if line["scheme"] == "fbs" and start.isoformat() <= line["ordered_at"][:10] < end.isoformat()
        ]
        return sales_repository.replace_fbs_order_period(
            store_slug, marketplace, start.isoformat(), end.isoformat(), lines, attempted_at, statuses
        )
    except Exception as error:
        # API errors can include credentials/response bodies. Never propagate them
        # to export reports, Google, sync state or the exception logging chain.
        status = getattr(error, "status", None)
        detail = f"; HTTP {status}" if isinstance(status, int) else ""
        if isinstance(error, FbsOrderRefreshError):
            detail = f"; {error}"
        raise FbsOrderRefreshError(
            f"{store_slug} / {marketplace} / FBS [{start}, {end}) МСК: "
            f"не удалось подтвердить полную загрузку заказов и актуальных статусов "
            f"({type(error).__name__}{detail}). Выгрузка не опубликована; повторите обновление."
        ) from None


def _sync_store_range(
    store_slug: str,
    marketplace: str,
    start: date,
    end: date,
    lookback_days: int,
    *,
    exact_order_period: bool,
) -> dict:
    marketplace = marketplace.upper()
    attempted_at = _now_iso()
    loader = {"WB": _sync_wb, "OZON": _sync_ozon, "YANDEX MARKET": _sync_yandex}[marketplace]
    try:
        lines, warnings = loader(store_slug, start, end)
        if exact_order_period:
            start_key = start.isoformat()
            end_key = end.isoformat()
            lines = [line for line in lines if start_key <= str(line.get("ordered_at") or "")[:10] < end_key]
        rows = db.upsert_sales_order_lines(lines, attempted_at)
        ok = not warnings
        error = "; ".join(warnings)[:1500] or None
        db.record_sales_sync(store_slug, marketplace, ok, error, rows, lookback_days, attempted_at)
        db.record_sync_health(store_slug, marketplace, "orders", ok, error, attempted_at)
        return {"ok": ok, "rows": rows, "warnings": warnings}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:1500]
        db.record_sales_sync(store_slug, marketplace, False, error, 0, lookback_days, attempted_at)
        db.record_sync_health(store_slug, marketplace, "orders", False, error, attempted_at)
        logger.warning("Продажи %s/%s не обновлены: %s", marketplace, store_slug, error)
        return {"ok": False, "rows": 0, "error": error}


def sync_store(store_slug: str, marketplace: str, lookback_days: int = REFRESH_LOOKBACK_DAYS) -> dict:
    end = datetime.now(MOSCOW).date() + timedelta(days=1)
    start = end - timedelta(days=max(lookback_days, 1))
    return _sync_store_range(
        store_slug,
        marketplace,
        start,
        end,
        lookback_days,
        exact_order_period=False,
    )


def sync_store_period(store_slug: str, marketplace: str, start: date, end: date) -> dict:
    """Sync an exact half-open order period [start, end)."""

    if end <= start:
        raise ValueError("Дата окончания периода должна быть позже даты начала")
    return _sync_store_range(
        store_slug,
        marketplace,
        start,
        end,
        (end - start).days,
        exact_order_period=True,
    )


def _configured_stores(marketplace: str) -> list[str]:
    if marketplace == "WB":
        return [slug for slug in STORES if wb_tokens.has_token(slug)]
    if marketplace == "OZON":
        return [slug for slug in STORES if ozon_tokens.has_credentials(slug)]
    return [slug for slug in STORES if yandex_tokens.has_credentials(slug)]


def sync_all(refresh_days: int = REFRESH_LOOKBACK_DAYS) -> dict:

    report: dict[str, dict] = {}
    for marketplace in MARKETPLACES:
        platform_report: dict[str, dict] = {}
        for store_slug in _configured_stores(marketplace):
            days = refresh_days
            if not db.sales_has_history(store_slug, marketplace):
                days = max(days, INITIAL_LOOKBACK_DAYS[marketplace])
            platform_report[store_slug] = sync_store(store_slug, marketplace, days)
        report[marketplace] = platform_report
    return report
