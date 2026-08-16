"""WB sales funnel for the sales overview.

The Decision Center stores a fixed 28-day snapshot for its own calculations.
This module deliberately requests the WB report for the period selected on the
sales screen, without altering those snapshots.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, date, datetime, timedelta

from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

ENDPOINT = f"{wb_api.ANALYTICS_BASE}/api/analytics/v3/sales-funnel/products"
PAGE_SIZE = 1_000
MAX_PAGES = 3
CACHE_TTL_SECONDS = 5 * 60

_cache: dict[tuple[str, str, str], tuple[float, dict]] = {}


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _items(value: object) -> list:
    return value if isinstance(value, list) else []


def _integer(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _number(value: object) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _percentage(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _parse_period(date_from: str, date_to: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError as exc:
        raise ValueError("Укажите период в формате ГГГГ-ММ-ДД") from exc
    if end < start:
        raise ValueError("Дата начала должна быть раньше даты окончания")
    if end > date.today():
        raise ValueError("Нельзя запросить воронку за будущую дату")
    if (end - start).days + 1 > 90:
        raise ValueError("Для воронки WB можно выбрать не больше 90 дней")
    return start, end


def _period_payload(start: date, end: date, offset: int) -> dict:
    days = (end - start).days + 1
    past_end = start - timedelta(days=1)
    past_start = past_end - timedelta(days=days - 1)
    return {
        "selectedPeriod": {"start": start.isoformat(), "end": end.isoformat()},
        "pastPeriod": {"start": past_start.isoformat(), "end": past_end.isoformat()},
        "nmIds": [],
        "brandNames": [],
        "subjectIds": [],
        "tagIds": [],
        "skipDeletedNm": True,
        "orderBy": {"field": "orderCount", "mode": "desc"},
        "limit": PAGE_SIZE,
        "offset": offset,
    }


def _normalise_product(raw: object) -> dict:
    item = _mapping(raw)
    product = _mapping(item.get("product"))
    statistic = _mapping(item.get("statistic"))
    selected = _mapping(statistic.get("selected"))
    comparison = _mapping(statistic.get("comparison"))

    views = _integer(selected.get("openCount"))
    carts = _integer(selected.get("cartCount"))
    orders = _integer(selected.get("orderCount"))
    buyouts = _integer(selected.get("buyoutCount"))
    favorites = _integer(selected.get("addToWishList", selected.get("addToWishlist")))
    return {
        "nm_id": _integer(product.get("nmId", item.get("nmId"))),
        "name": str(product.get("title") or product.get("name") or "Без названия"),
        "article": str(product.get("vendorCode") or product.get("nmId") or item.get("nmId") or ""),
        "views": views,
        "carts": carts,
        "favorites": favorites,
        "orders": orders,
        "buyouts": buyouts,
        "cancels": _integer(selected.get("cancelCount")),
        "order_sum": round(_number(selected.get("orderSum")), 2),
        "view_to_cart": _percentage(carts, views),
        "cart_to_order": _percentage(orders, carts),
        "order_to_buyout": _percentage(buyouts, orders),
        "orders_delta": round(_number(comparison.get("orderCountDynamic")), 1),
    }


def _fetch_products(token: str, start: date, end: date) -> tuple[list[dict], bool]:
    products: list[dict] = []
    truncated = False
    for page in range(MAX_PAGES):
        response = wb_api.request("POST", ENDPOINT, token, json_body=_period_payload(start, end, page * PAGE_SIZE))
        data = _mapping(response).get("data")
        rows = _items(_mapping(data).get("products"))
        products.extend(_normalise_product(row) for row in rows)
        if len(rows) < PAGE_SIZE:
            break
    else:
        truncated = True
    return [item for item in products if item["nm_id"]], truncated


def _dashboard_payload(store_slug: str, start: date, end: date) -> dict:
    try:
        token = wb_tokens.get_token(store_slug)
    except wb_tokens.TokenNotFoundError as exc:
        raise ValueError(str(exc)) from exc

    products, truncated = _fetch_products(token, start, end)
    products.sort(key=lambda item: (item["orders"], item["carts"], item["views"]), reverse=True)
    totals = {
        key: sum(item[key] for item in products)
        for key in ("views", "carts", "favorites", "orders", "buyouts", "cancels")
    }
    totals["order_sum"] = round(sum(item["order_sum"] for item in products), 2)
    totals.update(
        {
            "view_to_cart": _percentage(totals["carts"], totals["views"]),
            "cart_to_order": _percentage(totals["orders"], totals["carts"]),
            "order_to_buyout": _percentage(totals["buyouts"], totals["orders"]),
        }
    )
    return {
        "ok": True,
        "marketplace": "WB",
        "store": store_slug,
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "products": products,
        "totals": totals,
        "truncated": truncated,
    }


def dashboard(store_slug: str, date_from: str, date_to: str) -> dict:
    """Return WB card funnel data for one store and a selected period."""

    store = store_slug.strip().lower()
    if not store:
        raise ValueError("Выберите магазин WB для просмотра воронки")
    start, end = _parse_period(date_from, date_to)
    key = (store, start.isoformat(), end.isoformat())
    cached = _cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    payload = _dashboard_payload(store, start, end)
    _cache[key] = (now, payload)
    return payload
