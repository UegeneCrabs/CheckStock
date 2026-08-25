"""Persisted daily net WB orders from the sales-funnel report."""

from __future__ import annotations

import logging
import math
import time
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
INITIAL_HISTORY_DAYS = 90
REQUEST_PAUSE_SECONDS = 0.35
MOSCOW = MOSCOW_TIMEZONE
_SYNC_LOCK = Lock()


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


def _payload(
    day: date,
    cursor: str | None = None,
    nm_ids: tuple[int, ...] = (),
) -> dict:
    previous_day = day - timedelta(days=1)
    payload = {
        "selectedPeriod": {"start": day.isoformat(), "end": day.isoformat()},
        "pastPeriod": {"start": previous_day.isoformat(), "end": previous_day.isoformat()},
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


def _product_values(raw: object) -> tuple[str, str, str, int, float] | None:
    item = _mapping(raw)
    product = _mapping(item.get("product"))
    article = str(product.get("nmId") or product.get("nmID") or "").strip()
    if not article:
        logger.warning("Воронка WB: пропущена карточка без nmId")
        return None
    selected = _mapping(_mapping(item.get("statistic")).get("selected"))
    orders_count = max(0, _integer(selected.get("orderCount")) - _integer(selected.get("cancelCount")))
    orders_amount = round(max(0.0, _number(selected.get("orderSum")) - _number(selected.get("cancelSum"))), 2)
    return (
        article,
        str(product.get("vendorCode") or "").strip(),
        str(product.get("title") or "").strip(),
        orders_count,
        orders_amount,
    )


def _daily_products(
    token: str,
    day: date,
    nm_ids: tuple[int, ...] = (),
) -> list[tuple[str, str, str, int, float]]:
    """Return daily net orders separately for every WB product card.

    The v3 endpoint returns an aggregate for the selected period per card. We
    deliberately request exactly one day, then follow its cursor until every
    card in the seller account has been collected.
    """

    products: dict[str, tuple[str, str, str, int, float]] = {}
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


def _history_start() -> date:
    return datetime.now(MOSCOW).date() - timedelta(days=INITIAL_HISTORY_DAYS - 1)


def _saved_days(store_slug: str, start: date, end: date) -> set[str]:
    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT day FROM wb_funnel_daily_orders
            WHERE store_slug = ? AND day >= ? AND day <= ?
            """,
            (store_slug, start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return {str(row["day"]) for row in rows}


def _replace_day(store_slug: str, day: date, products: list[tuple[str, str, str, int, float]]) -> None:
    now = _now_iso()
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
                    (store_slug, day, article, vendor_code, product_name, orders_count, orders_amount, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, day, article) DO UPDATE SET
                    vendor_code = excluded.vendor_code,
                    product_name = excluded.product_name,
                    orders_count = excluded.orders_count,
                    orders_amount = excluded.orders_amount,
                    updated_at = excluded.updated_at
                """,
                [
                    (store_slug, day.isoformat(), article, vendor_code, product_name, count, amount, now)
                    for article, vendor_code, product_name, count, amount in products
                ],
            )
            conn.commit()
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
    start = _history_start()
    end = datetime.now(MOSCOW).date()
    saved = _saved_days(store_slug, start, end)
    recent = {end, end - timedelta(days=1)}
    result = []
    current = start
    while current <= end:
        if current.isoformat() not in saved or current in recent:
            result.append(current)
        current += timedelta(days=1)
    return result


def sync_store(store_slug: str) -> dict:
    """Fill missing 90-day history and refresh today plus yesterday for one cabinet."""

    store = store_slug.strip().lower()
    if store not in STORES:
        raise ValueError("Неизвестный магазин")
    if not wb_tokens.has_token(store):
        return {"store": store, "status": "skipped", "records": 0}
    days = _days_to_sync(store)
    if not days:
        return {"store": store, "status": "fresh", "records": 0}
    _record_sync(store, "running")
    token = wb_tokens.get_token(store)
    active_nm_ids = tuple(
        sorted(
            {
                int(str(item.get("article") or "").partition(" / ")[0])
                for item in db.get_catalog_items(store, "WB")
                if str(item.get("article") or "").partition(" / ")[0].isdigit()
            }
        )
    )
    records = 0
    try:
        for index, day in enumerate(days):
            if index:
                time.sleep(REQUEST_PAUSE_SECONDS)
            products = _daily_products(
                token,
                day,
                active_nm_ids if len(active_nm_ids) <= 1_000 else (),
            )
            excluded_nm_ids = db.get_excluded_nm_ids(store, "WB")
            products = [row for row in products if row[0] not in excluded_nm_ids]
            _replace_day(store, day, products)
            records += len(products)
    except Exception as exc:
        message = str(exc)[:700]
        _record_sync(store, "error", records, message)
        logger.warning("Воронка WB %s не обновлена: %s", store, message)
        return {"store": store, "status": "error", "records": records, "error": message}
    _record_sync(store, "success", records)
    return {"store": store, "status": "success", "records": records}


def sync_all() -> dict[str, dict]:
    """Run initial backfill or the regular refresh for every WB store."""

    with _SYNC_LOCK:
        return {store_slug: sync_store(store_slug) for store_slug in STORES}


def dashboard(date_from: str, date_to: str, store_slug: str | None = None) -> dict:
    """Read daily net funnel orders from the local database for the sales chart."""

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
    where = "day >= ? AND day <= ?"
    if store_slug:
        where += " AND store_slug = ?"
        params += (store_slug,)
    conn = db.get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT day, SUM(orders_count) AS orders_count, SUM(orders_amount) AS orders_amount
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
        series.append(
            {
                "date": current.isoformat(),
                "orders_count": _integer(row["orders_count"]) if row else 0,
                "orders_amount": round(_number(row["orders_amount"]) if row else 0.0, 2),
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
