"""Persisted WB order and buyout metrics from the sales-funnel report."""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Lock

from app import db
from app.domain import MOSCOW_TIMEZONE
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

ENDPOINT = f"{wb_api.ANALYTICS_BASE}/api/analytics/v3/sales-funnel/products"
PAGE_SIZE = 1_000
MAX_PAGES = 100
DEFAULT_BUYOUT_PERIOD_DAYS = 14
MIN_BUYOUT_PERIOD_DAYS = 1
MAX_BUYOUT_PERIOD_DAYS = 29
BUYOUT_SYNC_INTERVAL_SECONDS = 4 * 60 * 60
RECENT_REFRESH_DAYS = 7
MOSCOW = MOSCOW_TIMEZONE
_SYNC_LOCK = Lock()
_STORE_SYNC_LOCKS = {store_slug: Lock() for store_slug in STORES}


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _items(value: object) -> list:
    return value if isinstance(value, list) else []


def _number(value: object) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _integer(value: object) -> int:
    return max(0, int(_number(value)))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _period_payload(
    date_from: date,
    date_to: date,
    cursor: str | None = None,
    nm_ids: tuple[int, ...] = (),
) -> dict:
    period_days = (date_to - date_from).days + 1
    previous_end = date_from - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    payload = {
        "selectedPeriod": {"start": date_from.isoformat(), "end": date_to.isoformat()},
        "pastPeriod": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
        "nmIds": list(nm_ids),
        "brandNames": [],
        "subjectIds": [],
        "tagIds": [],
        "skipDeletedNm": True,
        "orderBy": {"field": "orderCount", "mode": "desc"},
        "limit": PAGE_SIZE,
    }
    if cursor:
        payload["cursor"] = cursor
    return payload


def _payload(
    day: date,
    cursor: str | None = None,
    nm_ids: tuple[int, ...] = (),
) -> dict:
    return _period_payload(day, day, cursor, nm_ids)


def _product_values(
    raw: object,
) -> tuple[str, str, str, int, float, int, float, int, float, float] | None:
    item = _mapping(raw)
    product = _mapping(item.get("product"))
    article = str(product.get("nmId") or product.get("nmID") or "").strip()
    if not article:
        logger.warning("Воронка WB: пропущена карточка без nmId")
        return None
    selected = _mapping(_mapping(item.get("statistic")).get("selected"))
    orders_count = _integer(selected.get("orderCount"))
    orders_amount = round(max(0.0, _number(selected.get("orderSum"))), 2)
    cancel_count = _integer(selected.get("cancelCount"))
    cancel_amount = round(max(0.0, _number(selected.get("cancelSum"))), 2)
    buyout_count = _integer(selected.get("buyoutCount"))
    buyout_amount = round(max(0.0, _number(selected.get("buyoutSum"))), 2)
    conversions = _mapping(selected.get("conversions"))
    buyout_percent = round(
        min(max(_number(conversions.get("buyoutPercent")), 0.0), 100.0),
        2,
    )
    return (
        article,
        str(product.get("vendorCode") or "").strip(),
        str(product.get("title") or "").strip(),
        orders_count,
        orders_amount,
        cancel_count,
        cancel_amount,
        buyout_count,
        buyout_amount,
        buyout_percent,
    )


def _product_metric_values(raw: object) -> tuple[str, int, float, int, float, float] | None:
    values = _product_values(raw)
    if values is None:
        return None
    return (
        values[0],
        values[3],
        values[4],
        values[5],
        values[6],
        values[9],
    )


def _daily_products(
    token: str,
    day: date,
    nm_ids: tuple[int, ...] = (),
) -> list[tuple[str, str, str, int, float, int, float, int, float, float]]:
    """Return daily raw orderCount and orderSum separately for every WB product card.

    The v3 endpoint returns an aggregate for the selected period per card. We
    deliberately request exactly one day, then follow its cursor until every
    card in the seller account has been collected.
    """

    products: dict[str, tuple[str, str, str, int, float, int, float, int, float, float]] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(MAX_PAGES):
        response = wb_api.request(
            "POST",
            ENDPOINT,
            token,
            json_body=_payload(day, cursor, nm_ids),
        )
        data = _mapping(_mapping(response).get("data"))
        rows = _items(data.get("products"))
        for raw in rows:
            values = _product_values(raw)
            if values is not None:
                products[values[0]] = values
        next_cursor = str(data.get("cursor") or data.get("nextCursor") or "").strip() or None
        if not next_cursor:
            if len(rows) >= PAGE_SIZE:
                raise wb_api.WBApiError(None, detail="WB не вернул cursor для следующей страницы воронки")
            return list(products.values())
        if next_cursor in seen_cursors:
            raise wb_api.WBApiError(None, detail="WB вернул повторяющийся cursor воронки")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise wb_api.WBApiError(None, detail="Превышен безопасный лимит страниц воронки WB")


def _period_product_metrics(
    token: str,
    date_from: date,
    date_to: date,
    nm_ids: tuple[int, ...] = (),
) -> list[tuple[str, int, float, int, float, float]]:
    products: dict[str, tuple[str, int, float, int, float, float]] = {}
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(MAX_PAGES):
        response = wb_api.request(
            "POST",
            ENDPOINT,
            token,
            json_body=_period_payload(date_from, date_to, cursor, nm_ids),
        )
        data = _mapping(_mapping(response).get("data"))
        rows = _items(data.get("products"))
        for raw in rows:
            values = _product_metric_values(raw)
            if values is not None:
                products[values[0]] = values
        next_cursor = str(data.get("cursor") or data.get("nextCursor") or "").strip() or None
        if not next_cursor:
            if len(rows) >= PAGE_SIZE:
                raise wb_api.WBApiError(
                    None,
                    detail="WB не вернул cursor для следующей страницы воронки",
                )
            return list(products.values())
        if next_cursor in seen_cursors:
            raise wb_api.WBApiError(None, detail="WB вернул повторяющийся cursor воронки")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise wb_api.WBApiError(None, detail="Превышен безопасный лимит страниц воронки WB")


def _replace_day(store_slug: str, day: date, products: list[tuple]) -> None:
    now = _now_iso()
    normalized = []
    for product in products:
        article, vendor_code, product_name, count, amount, *metrics = product
        cancel_count = metrics[0] if metrics else 0
        cancel_amount = metrics[1] if len(metrics) > 1 else 0
        buyout_count = metrics[2] if len(metrics) > 2 else None
        buyout_amount = metrics[3] if len(metrics) > 3 else None
        buyout_percent = metrics[4] if len(metrics) > 4 else None
        normalized.append(
            (
                store_slug,
                day.isoformat(),
                article,
                vendor_code,
                product_name,
                _integer(count),
                round(max(0.0, _number(amount)), 2),
                _integer(cancel_count),
                round(max(0.0, _number(cancel_amount)), 2),
                _integer(buyout_count) if buyout_count is not None else None,
                round(max(0.0, _number(buyout_amount)), 2) if buyout_amount is not None else None,
                (
                    round(min(max(_number(buyout_percent), 0.0), 100.0), 2)
                    if buyout_percent is not None
                    else None
                ),
                now,
            )
        )
    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.execute(
                "DELETE FROM wb_funnel_daily_orders WHERE store_slug = ? AND day = ?",
                (store_slug, day.isoformat()),
            )
            conn.executemany(
                """
                INSERT INTO wb_funnel_daily_orders
                    (store_slug, day, article, vendor_code, product_name,
                     orders_count, orders_amount, cancel_count, cancel_amount,
                     buyout_count, buyout_amount, buyout_percent,
                     source_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 4, ?)
                ON CONFLICT(store_slug, day, article) DO UPDATE SET
                    vendor_code = excluded.vendor_code,
                    product_name = excluded.product_name,
                    orders_count = excluded.orders_count,
                    orders_amount = excluded.orders_amount,
                    cancel_count = excluded.cancel_count,
                    cancel_amount = excluded.cancel_amount,
                    buyout_count = excluded.buyout_count,
                    buyout_amount = excluded.buyout_amount,
                    buyout_percent = excluded.buyout_percent,
                    source_version = excluded.source_version,
                    updated_at = excluded.updated_at
                """,
                normalized,
            )
            conn.commit()
        finally:
            conn.close()


def _replace_product_metrics(
    store_slug: str,
    date_from: date,
    date_to: date,
    products: list[tuple],
) -> None:
    now = _now_iso()
    normalized = []
    for product in products:
        article, orders_count, orders_amount, *metrics = product
        if len(metrics) >= 3:
            cancel_count, cancel_amount, buyout_percent = metrics[:3]
        else:
            cancel_count, cancel_amount, buyout_percent = 0, 0, metrics[0] if metrics else 0
        normalized.append(
            (
                store_slug,
                article,
                date_from.isoformat(),
                date_to.isoformat(),
                _integer(orders_count),
                round(max(0.0, _number(orders_amount)), 2),
                _integer(cancel_count),
                round(max(0.0, _number(cancel_amount)), 2),
                round(min(max(_number(buyout_percent), 0.0), 100.0), 2),
                now,
            )
        )
    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM wb_funnel_product_metrics WHERE store_slug = ?", (store_slug,))
            conn.executemany(
                """
                INSERT INTO wb_funnel_product_metrics
                    (store_slug, article, period_from, period_to,
                     orders_count, orders_amount, cancel_count, cancel_amount,
                     buyout_percent, source_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?)
                """,
                normalized,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _record_sync(store_slug: str, status: str, records: int = 0, error: str | None = None) -> None:
    now = _now_iso()
    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO wb_funnel_orders_sync_state
                    (store_slug, status, last_attempt_at, last_success_at, records, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug) DO UPDATE SET
                    status = excluded.status,
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = COALESCE(excluded.last_success_at,
                                                wb_funnel_orders_sync_state.last_success_at),
                    records = excluded.records,
                    error = excluded.error
                """,
                (store_slug, status, now, now if status == "success" else None, records, error),
            )
            conn.commit()
        finally:
            conn.close()


def _days_to_sync(store_slug: str) -> list[date]:
    del store_slug
    end = datetime.now(MOSCOW).date()
    return [end - timedelta(days=offset) for offset in range(RECENT_REFRESH_DAYS)]


def _active_nm_ids(store_slug: str) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                int(str(item.get("article") or "").partition(" / ")[0])
                for item in db.get_catalog_items(store_slug, "WB")
                if str(item.get("article") or "").partition(" / ")[0].isdigit()
            }
        )
    )


def sync_store(store_slug: str) -> dict:
    """Refresh the current seven-day window for one WB seller account."""

    store = store_slug.strip().lower()
    if store not in STORES:
        raise ValueError("Неизвестный магазин")
    if not wb_tokens.has_token(store):
        return {"store": store, "status": "skipped", "records": 0}
    with _STORE_SYNC_LOCKS[store]:
        days = _days_to_sync(store)
        _record_sync(store, "running")
        token = wb_tokens.get_token(store)
        records = 0
        try:
            for day in days:
                products = _daily_products(
                    token,
                    day,
                    (),
                )
                _replace_day(store, day, products)
                records += len(products)
        except Exception as exc:
            message = str(exc)[:700]
            _record_sync(store, "error", records, message)
            logger.warning("Воронка WB %s не обновлена: %s", store, message)
            return {"store": store, "status": "error", "records": records, "error": message}
        _record_sync(store, "success", records)
        return {"store": store, "status": "success", "records": records}


def sync_weekly_metrics_store(store_slug: str) -> dict:
    """Refresh WB buyout metrics for the cabinet's completed-day window."""

    store = store_slug.strip().lower()
    if store not in STORES:
        raise ValueError("Неизвестный магазин")
    if not wb_tokens.has_token(store):
        return {"store": store, "status": "skipped", "records": 0}
    with _STORE_SYNC_LOCKS[store]:
        configured_days = db.get_unit_economics_1c_cabinet_settings(store).buyout_period_days
        period_days = min(
            max(int(configured_days or DEFAULT_BUYOUT_PERIOD_DAYS), MIN_BUYOUT_PERIOD_DAYS),
            MAX_BUYOUT_PERIOD_DAYS,
        )
        date_to = datetime.now(MOSCOW).date() - timedelta(days=1)
        date_from = date_to - timedelta(days=period_days - 1)
        try:
            products = _period_product_metrics(
                wb_tokens.get_token(store),
                date_from,
                date_to,
                (),
            )
            _replace_product_metrics(store, date_from, date_to, products)
        except Exception as exc:
            message = str(exc)[:700]
            logger.warning("Процент выкупа WB %s не обновлён: %s", store, message)
            return {"store": store, "status": "error", "records": 0, "error": message}
        return {"store": store, "status": "success", "records": len(products)}


def _parallel_store_results(
    callback,
    store_slugs: tuple[str, ...] | None = None,
) -> dict[str, dict]:
    store_slugs = tuple(
        store_slug for store_slug in (tuple(STORES) if store_slugs is None else store_slugs)
        if store_slug in STORES
    )
    if not store_slugs:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, len(store_slugs))) as executor:
        results = executor.map(callback, store_slugs)
        return dict(zip(store_slugs, results, strict=True))


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Refresh every WB account in parallel; each account is paced independently."""

    with _SYNC_LOCK:
        return _parallel_store_results(sync_store, store_slugs)


def _sync_previous_day_store(store_slug: str, day: date) -> dict:
    if not wb_tokens.has_token(store_slug):
        return {"store": store_slug, "status": "skipped", "records": 0}
    with _STORE_SYNC_LOCKS[store_slug]:
        try:
            products = _daily_products(
                wb_tokens.get_token(store_slug),
                day,
                (),
            )
            _replace_day(store_slug, day, products)
        except Exception as exc:
            message = str(exc)[:700]
            logger.warning("Закрытие воронки WB %s за %s не выполнено: %s", store_slug, day, message)
            return {
                "store": store_slug,
                "day": day.isoformat(),
                "status": "error",
                "records": 0,
                "error": message,
            }
        return {
            "store": store_slug,
            "day": day.isoformat(),
            "status": "success",
            "records": len(products),
        }


def sync_previous_day_all(store_slugs: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Close yesterday explicitly; the regular refresh may correct it later."""

    day = datetime.now(MOSCOW).date() - timedelta(days=1)
    with _SYNC_LOCK:
        return _parallel_store_results(
            lambda store_slug: _sync_previous_day_store(store_slug, day),
            store_slugs,
        )


def sync_weekly_metrics_all(store_slugs: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Refresh persisted cabinet-specific buyout percentages every four hours."""

    with _SYNC_LOCK:
        return _parallel_store_results(sync_weekly_metrics_store, store_slugs)


def dashboard(date_from: str, date_to: str, store_slug: str | None = None) -> dict:
    """Read daily funnel orderCount and orderSum values for the sales chart."""

    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError as exc:
        raise ValueError("Укажите период в формате ГГГГ-ММ-ДД") from exc
    if end < start:
        raise ValueError("Дата начала должна быть раньше даты окончания")
    if store_slug and store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    params: tuple[object, ...] = (start.isoformat(), end.isoformat())
    where = "day >= ? AND day <= ? AND source_version >= 3"
    if store_slug:
        where += " AND store_slug = ?"
        params += (store_slug,)
    conn = db.get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT day,
                   SUM(orders_count) AS orders_count,
                   SUM(orders_amount) AS orders_amount,
                   SUM(cancel_count) AS cancel_count,
                   SUM(cancel_amount) AS cancel_amount,
                   SUM(buyout_count) AS buyout_count,
                   SUM(buyout_amount) AS buyout_amount
            FROM wb_funnel_daily_orders
            WHERE {where}
            GROUP BY day
            ORDER BY day
            """,
            params,
        ).fetchall()
        states = conn.execute(
            "SELECT store_slug, status, last_success_at, error FROM wb_funnel_orders_sync_state ORDER BY store_slug"
        ).fetchall()
    finally:
        conn.close()
    by_day = {str(row["day"]): row for row in rows}
    series = []
    current = start
    while current <= end:
        row = by_day.get(current.isoformat())
        orders_count = _integer(row["orders_count"]) if row else 0
        orders_amount = round(_number(row["orders_amount"]) if row else 0.0, 2)
        cancel_count = _integer(row["cancel_count"]) if row else 0
        cancel_amount = round(_number(row["cancel_amount"]) if row else 0.0, 2)
        series.append(
            {
                "date": current.isoformat(),
                "orders_count": orders_count,
                "orders_amount": orders_amount,
                "cancel_count": cancel_count,
                "cancel_amount": cancel_amount,
                "net_orders_count": orders_count - cancel_count,
                "net_orders_amount": round(orders_amount - cancel_amount, 2),
            }
        )
        current += timedelta(days=1)
    return {
        "ok": True,
        "marketplace": "WB",
        "store": store_slug or "all",
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "series": series,
        "sync": [dict(row) for row in states],
    }
