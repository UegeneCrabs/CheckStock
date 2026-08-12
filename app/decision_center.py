from __future__ import annotations

import logging
import math
import statistics
import time
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

PERIOD_DAYS = 28
SOURCE_INTERVAL_MINUTES = {"funnel": 60, "search": 60, "advertising": 30}
SOURCE_LABELS = {
    "funnel": "Воронка продаж WB",
    "search": "Поиск и позиции WB",
    "advertising": "Реклама WB",
}
ALLOWED_STATUSES = {"new", "in_progress", "completed"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _integer(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _items(value) -> list:
    return value if isinstance(value, list) else []


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _iso_day(value: date) -> str:
    return value.isoformat()


def _base_nm(article: str, fallback=None) -> int:
    raw = str(article or "").split("/")[0].strip()
    if raw.isdigit():
        return int(raw)
    return _integer(fallback)


def init_schema() -> None:

    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wb_decision_metrics (
                    store_slug TEXT NOT NULL,
                    nm_id INTEGER NOT NULL,
                    product_name TEXT,
                    views INTEGER NOT NULL DEFAULT 0,
                    carts INTEGER NOT NULL DEFAULT 0,
                    orders INTEGER NOT NULL DEFAULT 0,
                    buyouts INTEGER NOT NULL DEFAULT 0,
                    cancels INTEGER NOT NULL DEFAULT 0,
                    order_sum REAL NOT NULL DEFAULT 0,
                    rating REAL NOT NULL DEFAULT 0,
                    delivery_days REAL NOT NULL DEFAULT 0,
                    order_growth REAL NOT NULL DEFAULT 0,
                    avg_position REAL NOT NULL DEFAULT 0,
                    visibility REAL NOT NULL DEFAULT 0,
                    search_views INTEGER NOT NULL DEFAULT 0,
                    search_carts INTEGER NOT NULL DEFAULT 0,
                    search_orders INTEGER NOT NULL DEFAULT 0,
                    search_growth REAL NOT NULL DEFAULT 0,
                    estimated_reach INTEGER NOT NULL DEFAULT 0,
                    ad_impressions INTEGER NOT NULL DEFAULT 0,
                    ad_clicks INTEGER NOT NULL DEFAULT 0,
                    ad_spend REAL NOT NULL DEFAULT 0,
                    ad_orders INTEGER NOT NULL DEFAULT 0,
                    ad_position REAL NOT NULL DEFAULT 0,
                    funnel_synced_at TEXT,
                    search_synced_at TEXT,
                    advertising_synced_at TEXT,
                    PRIMARY KEY (store_slug, nm_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wb_decision_sync_state (
                    store_slug TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_attempt_at TEXT NOT NULL,
                    last_success_at TEXT,
                    records INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (store_slug, source)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wb_decision_actions (
                    fingerprint TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'new',
                    user_id INTEGER,
                    user_name TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute("DROP INDEX IF EXISTS idx_wb_decision_metrics_store")
            conn.execute("DROP INDEX IF EXISTS idx_wb_decision_actions_status")
            conn.execute("PRAGMA optimize")
            conn.commit()
        finally:
            conn.close()


def _sync_state(store_slug: str, source: str) -> dict | None:
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM wb_decision_sync_state WHERE store_slug = ? AND source = ?",
        (store_slug, source),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _record_sync(
    store_slug: str,
    source: str,
    status: str,
    attempted_at: str,
    records: int = 0,
    error: str | None = None,
    duration_ms: int = 0,
) -> None:
    with db.WRITE_LOCK:
        conn = db.get_connection()
        conn.execute(
            """
            INSERT INTO wb_decision_sync_state
                (store_slug, source, status, last_attempt_at, last_success_at,
                 records, error, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_slug, source) DO UPDATE SET
                status = excluded.status,
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = COALESCE(excluded.last_success_at,
                                           wb_decision_sync_state.last_success_at),
                records = excluded.records,
                error = excluded.error,
                duration_ms = excluded.duration_ms
            """,
            (
                store_slug,
                source,
                status,
                attempted_at,
                attempted_at if status == "success" else None,
                records,
                error,
                duration_ms,
            ),
        )
        conn.commit()
        conn.close()


def _is_due(store_slug: str, source: str, force: bool) -> bool:
    if force:
        return True
    state = _sync_state(store_slug, source)
    if not state:
        return True
    reference = state.get("last_success_at") or state.get("last_attempt_at")
    if not reference:
        return True
    try:
        moment = datetime.fromisoformat(str(reference).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    except ValueError:
        return True
    cooldown = 5 if state.get("status") == "error" else SOURCE_INTERVAL_MINUTES[source]
    return datetime.now(UTC) - moment >= timedelta(minutes=cooldown)


def _upsert_rows(
    store_slug: str, rows: list[dict], columns: tuple[str, ...], synced_column: str, synced_at: str
) -> int:
    if not rows:
        return 0
    all_columns = ("store_slug", "nm_id", *columns, synced_column)
    placeholders = ",".join("?" for _ in all_columns)
    updates = ",".join(f"{column}=excluded.{column}" for column in (*columns, synced_column))
    sql = (
        f"INSERT INTO wb_decision_metrics ({','.join(all_columns)}) "
        f"VALUES ({placeholders}) ON CONFLICT(store_slug,nm_id) DO UPDATE SET {updates}"
    )
    values = [
        (store_slug, row["nm_id"], *(row.get(column) for column in columns), synced_at)
        for row in rows
        if row.get("nm_id")
    ]
    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.executemany(sql, values)
            conn.commit()
        finally:
            conn.close()
    return len(values)


def _request(method: str, url: str, token: str, body=None):

    return wb_api.request(method, url, token, json_body=body)


def _periods() -> tuple[date, date, date, date]:
    current_end = datetime.now(UTC).date() - timedelta(days=1)
    current_start = current_end - timedelta(days=PERIOD_DAYS - 1)
    past_end = current_start - timedelta(days=1)
    past_start = past_end - timedelta(days=PERIOD_DAYS - 1)
    return current_start, current_end, past_start, past_end


def _sync_funnel(store_slug: str, token: str) -> int:
    start, end, past_start, past_end = _periods()
    response = _request(
        "POST",
        "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products",
        token,
        {
            "selectedPeriod": {"start": _iso_day(start), "end": _iso_day(end)},
            "pastPeriod": {"start": _iso_day(past_start), "end": _iso_day(past_end)},
            "nmIds": [],
            "brandNames": [],
            "subjectIds": [],
            "tagIds": [],
            "skipDeletedNm": True,
            "orderBy": {"field": "orderCount", "mode": "desc"},
            "limit": 1000,
        },
    )
    products = _items(_mapping(response).get("data", {}).get("products"))
    rows: list[dict] = []
    for item in products:
        item = _mapping(item)
        product = _mapping(item.get("product"))
        statistic = _mapping(item.get("statistic"))
        selected = _mapping(statistic.get("selected"))
        comparison = _mapping(statistic.get("comparison"))
        ready = _mapping(selected.get("timeToReady"))
        rows.append(
            {
                "nm_id": _integer(product.get("nmId")),
                "product_name": str(product.get("title") or product.get("name") or ""),
                "views": _integer(selected.get("openCount")),
                "carts": _integer(selected.get("cartCount")),
                "orders": _integer(selected.get("orderCount")),
                "buyouts": _integer(selected.get("buyoutCount")),
                "cancels": _integer(selected.get("cancelCount")),
                "order_sum": _number(selected.get("orderSum")),
                "rating": _number(product.get("feedbackRating")),
                "delivery_days": _number(ready.get("days")) + _number(ready.get("hours")) / 24,
                "order_growth": _number(comparison.get("orderCountDynamic")),
            }
        )
    return _upsert_rows(
        store_slug,
        rows,
        (
            "product_name",
            "views",
            "carts",
            "orders",
            "buyouts",
            "cancels",
            "order_sum",
            "rating",
            "delivery_days",
            "order_growth",
        ),
        "funnel_synced_at",
        _now_iso(),
    )


def _sync_search(store_slug: str, token: str) -> int:
    start, end, past_start, past_end = _periods()
    response = _request(
        "POST",
        "https://seller-analytics-api.wildberries.ru/api/v2/search-report/report",
        token,
        {
            "currentPeriod": {"start": _iso_day(start), "end": _iso_day(end)},
            "pastPeriod": {"start": _iso_day(past_start), "end": _iso_day(past_end)},
            "nmIds": [],
            "subjectIds": [],
            "brandNames": [],
            "tagIds": [],
            "positionCluster": "all",
            "orderBy": {"field": "avgPosition", "mode": "asc"},
            "includeSubstitutedSKUs": True,
            "includeSearchTexts": True,
            "limit": 1000,
            "offset": 0,
        },
    )
    groups = _items(_mapping(response).get("data", {}).get("groups"))
    rows: list[dict] = []
    for group in groups:
        for raw in _items(_mapping(group).get("items")):
            item = _mapping(raw)
            position = _mapping(item.get("avgPosition"))
            opened = _mapping(item.get("openCard"))
            carts = _mapping(item.get("addToCart"))
            orders = _mapping(item.get("orders"))
            visibility = _mapping(item.get("visibility"))
            views = _integer(opened.get("current"))
            visibility_value = _clamp(_number(visibility.get("current")), 0, 100)
            reach = round(views / max(0.018, 0.025 + visibility_value / 100 * 0.04))
            rows.append(
                {
                    "nm_id": _integer(item.get("nmId")),
                    "avg_position": _number(position.get("current")),
                    "visibility": visibility_value,
                    "search_views": views,
                    "search_carts": _integer(carts.get("current")),
                    "search_orders": _integer(orders.get("current")),
                    "search_growth": _number(orders.get("dynamics")),
                    "estimated_reach": reach,
                }
            )
    return _upsert_rows(
        store_slug,
        rows,
        (
            "avg_position",
            "visibility",
            "search_views",
            "search_carts",
            "search_orders",
            "search_growth",
            "estimated_reach",
        ),
        "search_synced_at",
        _now_iso(),
    )


def _flatten_advertising(response) -> list[dict]:
    rows: list[dict] = []
    for campaign in _items(response):
        for day in _items(_mapping(campaign).get("days")):
            day = _mapping(day)
            for app_row in _items(day.get("apps")):
                app_row = _mapping(app_row)
                for nm in _items(app_row.get("nms") or app_row.get("nm")):
                    nm = _mapping(nm)
                    rows.append(
                        {
                            "nm_id": _integer(nm.get("nmId")),
                            "impressions": _integer(nm.get("views")),
                            "clicks": _integer(nm.get("clicks")),
                            "spend": _number(nm.get("sum"), _number(nm.get("spend"))),
                            "orders": _integer(nm.get("orders")),
                            "position": _number(nm.get("avg_position"), _number(nm.get("avgPos"))),
                        }
                    )
    root = _mapping(response)
    for item in _items(root.get("items")):
        item = _mapping(item)
        for day in _items(item.get("dailyStats")):
            stat = _mapping(_mapping(day).get("stat"))
            rows.append(
                {
                    "nm_id": _integer(item.get("nmId")),
                    "impressions": _integer(stat.get("views")),
                    "clicks": _integer(stat.get("clicks")),
                    "spend": _number(stat.get("spend")),
                    "orders": _integer(stat.get("orders")),
                    "position": _number(stat.get("avgPos")),
                }
            )
    return rows


def _sync_advertising(store_slug: str, token: str) -> int:
    campaign_response = _request("GET", "https://advert-api.wildberries.ru/adv/v1/promotion/count", token)
    campaigns: list[dict] = []
    for group in _items(_mapping(campaign_response).get("adverts")):
        group = _mapping(group)
        if _integer(group.get("status")) not in {7, 9, 11}:
            continue
        campaigns.extend(_mapping(item) for item in _items(group.get("advert_list")))
    campaigns.sort(key=lambda item: str(item.get("changeTime") or ""), reverse=True)
    ids = [str(_integer(item.get("advertId"))) for item in campaigns[:50] if _integer(item.get("advertId"))]
    if not ids:
        return 0
    current_start, current_end, _, _ = _periods()
    query = (
        "https://advert-api.wildberries.ru/adv/v3/fullstats?ids="
        + ",".join(ids)
        + f"&beginDate={_iso_day(current_start)}&endDate={_iso_day(current_end)}"
    )
    raw_rows = _flatten_advertising(_request("GET", query, token))
    grouped: dict[int, dict] = {}
    for row in raw_rows:
        nm_id = row["nm_id"]
        if not nm_id:
            continue
        item = grouped.setdefault(
            nm_id,
            {
                "nm_id": nm_id,
                "ad_impressions": 0,
                "ad_clicks": 0,
                "ad_spend": 0.0,
                "ad_orders": 0,
                "position_sum": 0.0,
                "position_count": 0,
            },
        )
        item["ad_impressions"] += row["impressions"]
        item["ad_clicks"] += row["clicks"]
        item["ad_spend"] += row["spend"]
        item["ad_orders"] += row["orders"]
        if row["position"] > 0:
            item["position_sum"] += row["position"]
            item["position_count"] += 1
    rows = []
    for item in grouped.values():
        rows.append(
            {
                **item,
                "ad_spend": round(item["ad_spend"], 2),
                "ad_position": item["position_sum"] / max(1, item["position_count"]),
            }
        )
    return _upsert_rows(
        store_slug,
        rows,
        ("ad_impressions", "ad_clicks", "ad_spend", "ad_orders", "ad_position"),
        "advertising_synced_at",
        _now_iso(),
    )


def sync_store(store_slug: str, force: bool = False, sources: Iterable[str] | None = None) -> dict:
    if store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    if not wb_tokens.has_token(store_slug):
        return {"store": store_slug, "configured": False, "results": []}
    requested = [
        source for source in (sources or SOURCE_INTERVAL_MINUTES) if source in SOURCE_INTERVAL_MINUTES
    ]
    token = wb_tokens.get_token(store_slug)
    loaders = {"funnel": _sync_funnel, "search": _sync_search, "advertising": _sync_advertising}
    results = []
    for source in requested:
        if not _is_due(store_slug, source, force):
            results.append({"source": source, "status": "fresh"})
            continue
        attempted_at = _now_iso()
        started = time.monotonic()
        _record_sync(store_slug, source, "running", attempted_at)
        try:
            records = loaders[source](store_slug, token)
            duration = round((time.monotonic() - started) * 1000)
            _record_sync(store_slug, source, "success", attempted_at, records, None, duration)
            results.append({"source": source, "status": "success", "records": records})
        except Exception as exc:
            duration = round((time.monotonic() - started) * 1000)
            message = str(getattr(exc, "friendly", "") or exc or type(exc).__name__)[:500]
            _record_sync(store_slug, source, "error", attempted_at, 0, message, duration)
            logger.warning("Центр решений WB %s/%s: %s", store_slug, source, message)
            results.append({"source": source, "status": "error", "error": message})
    return {"store": store_slug, "configured": True, "results": results}


def sync_many(store_slugs: Iterable[str], force: bool = False) -> dict:
    return {
        slug: sync_store(slug, force=force)
        for slug in store_slugs
        if slug in STORES and wb_tokens.has_token(slug)
    }


def sync_all(force: bool = False) -> dict:
    return sync_many(STORES, force=force)


def _select_in(sql_prefix: str, store_slugs: list[str], params: list | None = None) -> list[dict]:
    if not store_slugs:
        return []
    placeholders = ",".join("?" for _ in store_slugs)
    conn = db.get_connection()
    rows = conn.execute(sql_prefix.format(stores=placeholders), [*(params or []), *store_slugs]).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _local_products(store_slugs: list[str]) -> dict[tuple[str, int], dict]:
    catalog_rows = _select_in(
        """
        SELECT si.store_slug, si.article, si.name, si.image_url,
               COALESCE(wm.nm_id, 0) AS metric_nm_id,
               COALESCE(wm.buyer_price, wm.discounted_price, wm.list_price, 0) AS price,
               COALESCE(wm.commission_fbs_rate, 0) AS commission_rate,
               COALESCE(uc.purchase_price, 0) AS purchase_price,
               COALESCE(uc.other_cost, 0) AS other_cost,
               COALESCE(ms.quantity, 0) AS mp_stock,
               COALESCE(fs.quantity, 0) AS ff_stock
          FROM stock_items si
          LEFT JOIN wb_unit_metrics wm
            ON wm.store_slug = si.store_slug AND wm.article = si.article
          LEFT JOIN unit_costs uc
            ON uc.store_slug = si.store_slug AND uc.article = si.article
          LEFT JOIN (
              SELECT store_slug, article, SUM(quantity) AS quantity
                FROM mp_stock WHERE marketplace = 'WB'
               GROUP BY store_slug, article
          ) ms ON ms.store_slug = si.store_slug AND ms.article = si.article
          LEFT JOIN (
              SELECT store_slug, article, SUM(quantity) AS quantity
                FROM ff_stock WHERE marketplace = 'WB'
               GROUP BY store_slug, article
          ) fs ON fs.store_slug = si.store_slug AND fs.article = si.article
         WHERE si.marketplace = 'WB' AND si.is_service = 0
           AND si.store_slug IN ({stores})
        """,
        store_slugs,
    )
    products: dict[tuple[str, int], dict] = {}
    for row in catalog_rows:
        nm_id = _base_nm(row.get("article"), row.get("metric_nm_id"))
        if not nm_id:
            continue
        key = (row["store_slug"], nm_id)
        item = products.setdefault(
            key,
            {
                "store_slug": row["store_slug"],
                "nm_id": nm_id,
                "article": str(nm_id),
                "name": row.get("name") or str(nm_id),
                "image_url": row.get("image_url") or "",
                "stock": 0,
                "price_values": [],
                "cost_values": [],
                "commission_values": [],
            },
        )
        if not item["image_url"] and row.get("image_url"):
            item["image_url"] = row["image_url"]
        item["stock"] += _integer(row.get("mp_stock")) + _integer(row.get("ff_stock"))
        price = _number(row.get("price"))
        cost = _number(row.get("purchase_price")) + _number(row.get("other_cost"))
        commission = _number(row.get("commission_rate"))
        if price > 0:
            item["price_values"].append(price)
        if cost > 0:
            item["cost_values"].append(cost)
        if commission > 0:
            item["commission_values"].append(commission)
    return products


def _sales_period(store_slugs: list[str], start: date, end: date) -> dict[tuple[str, int], dict]:
    rows = _select_in(
        """
        SELECT store_slug,
               CAST(json_extract(raw_json, '$.nmId') AS INTEGER) AS nm_id,
               SUM(MAX(quantity - cancelled_quantity, 0)) AS orders,
               SUM(cancelled_quantity) AS cancels,
               SUM(MAX(order_amount - cancelled_amount, 0)) AS revenue,
               SUM(sold_quantity) AS sold,
               SUM(sale_amount) AS sales_revenue
          FROM sales_order_lines
         WHERE marketplace = 'WB' AND ordered_at >= ? AND ordered_at < ?
           AND store_slug IN ({stores})
         GROUP BY store_slug, CAST(json_extract(raw_json, '$.nmId') AS INTEGER)
        """,
        store_slugs,
        [start.isoformat(), end.isoformat()],
    )
    return {(row["store_slug"], _integer(row["nm_id"])): row for row in rows if _integer(row.get("nm_id"))}


def _cached_metrics(store_slugs: list[str]) -> dict[tuple[str, int], dict]:
    rows = _select_in(
        "SELECT * FROM wb_decision_metrics WHERE store_slug IN ({stores})",
        store_slugs,
    )
    return {(row["store_slug"], _integer(row["nm_id"])): row for row in rows}


def _action_states() -> dict[str, str]:
    conn = db.get_connection()
    rows = conn.execute("SELECT fingerprint, status FROM wb_decision_actions").fetchall()
    conn.close()
    return {row["fingerprint"]: row["status"] for row in rows}


def _median(values: Iterable[float], fallback: float) -> float:
    clean = [value for value in values if value > 0 and math.isfinite(value)]
    return statistics.median(clean) if clean else fallback


def _build_products(store_slugs: list[str]) -> list[dict]:
    today = datetime.now(UTC).date()
    current_start = today - timedelta(days=PERIOD_DAYS)
    previous_start = current_start - timedelta(days=PERIOD_DAYS)
    local = _local_products(store_slugs)
    current = _sales_period(store_slugs, current_start, today + timedelta(days=1))
    previous = _sales_period(store_slugs, previous_start, current_start)
    cached = _cached_metrics(store_slugs)
    keys = set(local) | set(current) | set(cached)
    products: list[dict] = []
    for key in keys:
        base = local.get(key) or {
            "store_slug": key[0],
            "nm_id": key[1],
            "article": str(key[1]),
            "name": str(key[1]),
            "image_url": "",
            "stock": 0,
            "price_values": [],
            "cost_values": [],
            "commission_values": [],
        }
        live = cached.get(key) or {}
        recent = current.get(key) or {}
        old = previous.get(key) or {}
        orders = _integer(live.get("orders"), _integer(recent.get("orders")))
        buyouts = _integer(live.get("buyouts"), _integer(recent.get("sold")))
        cancels = _integer(live.get("cancels"), _integer(recent.get("cancels")))
        revenue = _number(live.get("order_sum"), _number(recent.get("revenue")))
        price = _median(base.get("price_values", []), revenue / max(orders, 1))
        if price <= 0:
            price = 1
        cost_values = base.get("cost_values", [])
        purchase_cost = _median(cost_values, price * 0.42)
        cost_modelled = not bool(cost_values)
        commission_rate = _median(base.get("commission_values", []), 20.0)
        commission_rate = commission_rate / 100 if commission_rate > 1 else commission_rate
        profit_per_unit = price - purchase_cost - price * commission_rate - price * 0.12
        profit_share = _clamp(profit_per_unit / price, 0.03, 0.65)
        views = max(_integer(live.get("views")), _integer(live.get("search_views")))
        carts = max(_integer(live.get("carts")), _integer(live.get("search_carts")))
        search_orders = _integer(live.get("search_orders"))
        orders = max(orders, search_orders)
        buyout_rate = buyouts / max(orders, 1)
        weekly_orders = orders / 4
        stock = _integer(base.get("stock"))
        stock_days = stock / max(orders / PERIOD_DAYS, 0.01)
        ad_impressions = _integer(live.get("ad_impressions"))
        ad_clicks = _integer(live.get("ad_clicks"))
        ad_spend = _number(live.get("ad_spend"))
        ad_orders = _integer(live.get("ad_orders"))
        ctr = ad_clicks / max(ad_impressions, 1)
        drr = ad_spend / max(revenue, 1)
        cart_rate = carts / max(views, 1)
        checkout_rate = orders / max(carts, 1)
        previous_orders = _integer(old.get("orders"))
        local_growth = (orders - previous_orders) / previous_orders * 100 if previous_orders else 0
        growth = _number(live.get("order_growth"), local_growth)
        rating = _number(live.get("rating"))
        estimated_reach = _integer(live.get("estimated_reach"))
        if not estimated_reach and views:
            estimated_reach = round(views / 0.045)
        health = round(
            _clamp(buyout_rate / 0.78, 0, 1) * 24
            + _clamp(stock_days / 28, 0, 1) * 20
            + _clamp((0.35 - drr) / 0.35, 0, 1) * 18
            + _clamp(cart_rate / 0.28, 0, 1) * 14
            + _clamp(checkout_rate / 0.32, 0, 1) * 14
            + _clamp(rating / 4.8, 0, 1) * 10
        )
        products.append(
            {
                "store": key[0],
                "storeName": STORES.get(key[0], {}).get("name", key[0].upper()),
                "nmId": key[1],
                "article": base.get("article") or str(key[1]),
                "name": live.get("product_name") or base.get("name") or str(key[1]),
                "imageUrl": base.get("image_url") or "",
                "price": round(price, 2),
                "purchaseCost": round(purchase_cost, 2),
                "costModelled": cost_modelled,
                "profitPerUnit": round(profit_per_unit, 2),
                "profitShare": profit_share,
                "stock": stock,
                "stockDays": round(stock_days, 1),
                "orders": orders,
                "buyouts": buyouts,
                "cancels": cancels,
                "revenue": round(revenue, 2),
                "weeklyOrders": round(weekly_orders, 1),
                "buyoutRate": buyout_rate,
                "views": views,
                "carts": carts,
                "estimatedReach": estimated_reach,
                "cartRate": cart_rate,
                "checkoutRate": checkout_rate,
                "rating": rating,
                "deliveryDays": _number(live.get("delivery_days")),
                "growth": growth,
                "visibility": _number(live.get("visibility")),
                "avgPosition": _number(live.get("avg_position")),
                "adImpressions": ad_impressions,
                "adClicks": ad_clicks,
                "adSpend": round(ad_spend, 2),
                "adOrders": ad_orders,
                "ctr": ctr,
                "drr": drr,
                "health": _clamp(health, 0, 100),
                "dataUpdatedAt": max(
                    str(live.get("funnel_synced_at") or ""),
                    str(live.get("search_synced_at") or ""),
                    str(live.get("advertising_synced_at") or ""),
                )
                or None,
            }
        )
    return products


def _evidence(label: str, value: str, tone: str = "neutral") -> dict:
    return {"label": label, "value": value, "tone": tone}


def _opportunities(products: list[dict], action_states: dict[str, str]) -> list[dict]:
    median_ctr = _median((p["ctr"] for p in products if p["adImpressions"] >= 500), 0.035)
    median_cart = _median((p["cartRate"] for p in products if p["views"] >= 50), 0.24)
    median_checkout = _median((p["checkoutRate"] for p in products if p["carts"] >= 15), 0.30)
    median_drr = _median((p["drr"] for p in products if p["adSpend"] > 0), 0.22)
    opportunities: list[dict] = []

    def add(
        product: dict,
        slug: str,
        domain: str,
        severity: str,
        title: str,
        summary: str,
        action: str,
        evidence: list[dict],
        expected_revenue: float,
        expected_profit: float,
        confidence: float,
        effort: float,
        metric: str,
        baseline: str,
        target: str,
        horizon: int,
        guardrail: str,
        experiment: bool = False,
    ) -> None:
        fingerprint = f"{product['store']}:{product['nmId']}:{slug}"
        expected_revenue = max(0.0, expected_revenue)
        expected_profit = _clamp(expected_profit, 0, max(expected_revenue, expected_profit))
        severity_bonus = {"critical": 32, "high": 20, "medium": 10}.get(severity, 0)
        impact = min(55, math.log10(expected_profit + 10) * 13)
        score = round(_clamp(severity_bonus + impact + confidence * 18 - effort * 1.5, 1, 99))
        opportunities.append(
            {
                "fingerprint": fingerprint,
                "domain": domain,
                "severity": severity,
                "title": title,
                "summary": summary,
                "action": action,
                "product": product["name"],
                "article": product["article"],
                "nmId": product["nmId"],
                "store": product["store"],
                "storeName": product["storeName"],
                "imageUrl": product["imageUrl"],
                "evidence": evidence,
                "expectedRevenue": round(expected_revenue, 2),
                "expectedProfit": round(expected_profit, 2),
                "confidence": confidence,
                "effortHours": effort,
                "score": score,
                "status": action_states.get(fingerprint, "new"),
                "primaryMetric": metric,
                "baseline": baseline,
                "target": target,
                "horizonDays": horizon,
                "guardrail": guardrail,
                "experiment": experiment,
            }
        )

    for product in products:
        revenue = product["revenue"]
        share = product["profitShare"]
        if product["weeklyOrders"] >= 0.5 and product["stockDays"] < 8:
            lost_share = _clamp((14 - product["stockDays"]) / 14, 0.15, 1)
            uplift = product["weeklyOrders"] * product["price"] * 2 * lost_share
            add(
                product,
                "stockout",
                "Наличие",
                "critical" if product["stockDays"] < 3 else "high",
                "Не дать товару уйти в out-of-stock",
                f"Текущего остатка хватит примерно на {product['stockDays']:.0f} дн. — позиции и продажи могут просесть до следующего пополнения.",
                "Сформировать пополнение и проверить распределение по складам",
                [
                    _evidence("Остаток", f"{product['stock']} шт.", "danger"),
                    _evidence("Покрытие", f"{product['stockDays']:.0f} дн.", "danger"),
                    _evidence("Спрос", f"{product['weeklyOrders']:.1f}/нед."),
                ],
                uplift,
                uplift * share,
                0.9,
                2,
                "Дни наличия",
                f"{product['stockDays']:.0f} дн.",
                "21–35 дн.",
                7,
                "Не создавать запас выше 60 дней",
            )
        if product["stockDays"] > 90 and product["stock"] >= 20:
            release = min(product["stock"] * product["price"] * 0.08, max(revenue * 0.25, 500))
            add(
                product,
                "slow-stock",
                "Ассортимент",
                "medium",
                "Освободить деньги из медленного остатка",
                "Запас заметно превышает текущий темп продаж и замораживает оборотный капитал.",
                "Остановить пополнение и протестировать мягкое промо или комплект",
                [
                    _evidence("Покрытие", f"{product['stockDays']:.0f} дн.", "warning"),
                    _evidence("Остаток", f"{product['stock']} шт."),
                    _evidence("Заказы", str(product["orders"])),
                ],
                release,
                release * max(share, 0.12),
                0.82,
                2,
                "Дни запаса",
                f"{product['stockDays']:.0f}",
                "≤ 60",
                28,
                "Маржа после скидки остаётся положительной",
                True,
            )
        drr_limit = max(0.28, median_drr * 1.25)
        if product["adSpend"] >= 300 and product["drr"] > drr_limit:
            saving = product["adSpend"] * _clamp(
                (product["drr"] - drr_limit) / max(product["drr"], 0.01), 0.12, 0.55
            )
            add(
                product,
                "ad-waste",
                "Реклама",
                "high" if product["drr"] > 0.5 else "medium",
                "Убрать неинкрементальный рекламный расход",
                "Доля рекламных расходов выше нормы портфеля; часть бюджета стоит вернуть товарам с сильной конверсией.",
                "Снизить ставки на слабых кластерах и оставить контрольную группу",
                [
                    _evidence("ДРР", f"{product['drr']:.1%}", "danger"),
                    _evidence("Портфель", f"{median_drr:.1%}"),
                    _evidence("Расход", f"{product['adSpend']:,.0f} ₽"),
                ],
                0,
                saving,
                0.84,
                2,
                "ДРР",
                f"{product['drr']:.1%}",
                f"≤ {drr_limit:.1%}",
                14,
                "Заказы не снижаются более чем на 7%",
                True,
            )
        if product["adImpressions"] >= 800 and product["ctr"] < median_ctr * 0.72:
            additional_clicks = product["adImpressions"] * (median_ctr * 0.9 - product["ctr"])
            cvr = product["orders"] / max(product["views"], product["adClicks"], 1)
            uplift = max(0, additional_clicks * cvr * product["price"])
            add(
                product,
                "ctr",
                "Карточка",
                "medium",
                "Поднять CTR главного фото",
                "Показы уже есть, но карточка забирает меньше переходов, чем медиана портфеля.",
                "Запустить тест главного фото и заголовка",
                [
                    _evidence("CTR", f"{product['ctr']:.2%}", "danger"),
                    _evidence("Медиана", f"{median_ctr:.2%}"),
                    _evidence("Показы", f"{product['adImpressions']:,}"),
                ],
                uplift,
                uplift * share,
                0.78,
                3,
                "CTR",
                f"{product['ctr']:.2%}",
                f"≥ {median_ctr * 0.9:.2%}",
                14,
                "Конверсия в заказ не ухудшается",
                True,
            )
        if product["views"] >= 80 and product["cartRate"] < median_cart * 0.75:
            extra_orders = (
                product["views"]
                * (median_cart * 0.9 - product["cartRate"])
                * max(product["checkoutRate"], 0.08)
            )
            uplift = max(0, extra_orders * product["price"])
            add(
                product,
                "cart-rate",
                "Карточка",
                "medium",
                "Усилить карточку до добавления в корзину",
                "Посетители открывают товар, но характеристики и контент убеждают хуже портфельной нормы.",
                "Закрыть возражения в первых экранах карточки и инфографике",
                [
                    _evidence("В корзину", f"{product['cartRate']:.1%}", "danger"),
                    _evidence("Медиана", f"{median_cart:.1%}"),
                    _evidence("Открытия", f"{product['views']:,}"),
                ],
                uplift,
                uplift * share,
                0.76,
                4,
                "Cart rate",
                f"{product['cartRate']:.1%}",
                f"≥ {median_cart * 0.9:.1%}",
                14,
                "CTR карточки остаётся стабильным",
                True,
            )
        if product["carts"] >= 20 and product["checkoutRate"] < median_checkout * 0.75:
            extra = product["carts"] * (median_checkout * 0.9 - product["checkoutRate"])
            uplift = max(0, extra * product["price"])
            add(
                product,
                "checkout",
                "Цена",
                "medium",
                "Проверить цену и условия доставки",
                "Товар часто кладут в корзину, но заказ оформляют заметно реже нормы — вероятен барьер цены, промо или срока доставки.",
                "Провести недельный тест цены без изменения рекламы",
                [
                    _evidence("Корзина → заказ", f"{product['checkoutRate']:.1%}", "danger"),
                    _evidence("Медиана", f"{median_checkout:.1%}"),
                    _evidence("Цена", f"{product['price']:,.0f} ₽"),
                ],
                uplift,
                uplift * share,
                0.72,
                3,
                "Checkout rate",
                f"{product['checkoutRate']:.1%}",
                f"≥ {median_checkout * 0.9:.1%}",
                14,
                "Маржа не ниже минимальной",
                True,
            )
        if product["orders"] >= 12 and product["buyoutRate"] < 0.62:
            recoverable = product["orders"] * (0.7 - product["buyoutRate"]) * product["price"]
            add(
                product,
                "buyout",
                "Выкуп",
                "high",
                "Снизить потери после заказа",
                "Выкуп ниже безопасного уровня: продажи теряются уже после оформления заказа.",
                "Проверить ожидания от товара, упаковку, комплектацию и причины возврата",
                [
                    _evidence("Выкуп", f"{product['buyoutRate']:.1%}", "danger"),
                    _evidence("Заказы", str(product["orders"])),
                    _evidence("Отмены", str(product["cancels"])),
                ],
                recoverable,
                recoverable * share,
                0.86,
                4,
                "Выкуп",
                f"{product['buyoutRate']:.1%}",
                "≥ 70%",
                28,
                "Рейтинг не снижается",
            )
        if (
            product["avgPosition"] > 35
            and product["orders"] >= 8
            and product["cartRate"] >= median_cart * 0.8
        ):
            uplift = revenue * _clamp((product["avgPosition"] - 25) / 100, 0.05, 0.18)
            add(
                product,
                "search-position",
                "Поиск",
                "medium",
                "Поднять органическую позицию",
                "Карточка конвертирует спрос, но находится ниже зоны стабильной видимости в поиске WB.",
                "Усилить релевантность названия и кластеров, затем точечно поддержать рекламой",
                [
                    _evidence("Позиция", f"{product['avgPosition']:.0f}", "warning"),
                    _evidence("Видимость", f"{product['visibility']:.0f}%"),
                    _evidence("Заказы", str(product["orders"])),
                ],
                uplift,
                uplift * share,
                0.74,
                3,
                "Средняя позиция",
                f"{product['avgPosition']:.0f}",
                "≤ 25",
                21,
                "ДРР остаётся в пределах нормы",
                True,
            )
        if product["growth"] > 25 and 8 <= product["stockDays"] < 35:
            uplift = product["weeklyOrders"] * product["price"] * 0.18 * 2
            add(
                product,
                "growth-stock",
                "Наличие",
                "high",
                "Поддержать растущий спрос остатком",
                "Заказы растут быстрее прошлого периода, текущий запас может стать ограничением через несколько недель.",
                "Увеличить ближайшее пополнение пропорционально подтверждённому росту",
                [
                    _evidence("Динамика", f"+{product['growth']:.0f}%", "success"),
                    _evidence("Покрытие", f"{product['stockDays']:.0f} дн."),
                    _evidence("Спрос", f"{product['weeklyOrders']:.1f}/нед."),
                ],
                uplift,
                uplift * share,
                0.8,
                2,
                "Заказы",
                f"+{product['growth']:.0f}%",
                "рост без OOS",
                14,
                "Покрытие не выше 60 дней",
            )
    return sorted(
        opportunities,
        key=lambda item: (item["status"] == "completed", -item["score"], -item["expectedProfit"]),
    )[:60]


def _sync_summary(store_slugs: list[str]) -> list[dict]:
    rows = _select_in(
        "SELECT * FROM wb_decision_sync_state WHERE store_slug IN ({stores}) ORDER BY store_slug, source",
        store_slugs,
    )
    result = []
    for row in rows:
        result.append(
            {
                "store": row["store_slug"],
                "storeName": STORES.get(row["store_slug"], {}).get("name", row["store_slug"]),
                "source": row["source"],
                "label": SOURCE_LABELS.get(row["source"], row["source"]),
                "status": row["status"],
                "lastSuccessAt": row.get("last_success_at"),
                "records": row.get("records") or 0,
                "error": row.get("error") or "",
                "intervalMinutes": SOURCE_INTERVAL_MINUTES.get(row["source"], 60),
            }
        )
    return result


def dashboard(store_slugs: Iterable[str]) -> dict:
    selected = [slug for slug in store_slugs if slug in STORES]
    products = _build_products(selected)
    states = _action_states()
    opportunities = _opportunities(products, states)
    active = [item for item in opportunities if item["status"] != "completed"]
    reach = sum(item["estimatedReach"] for item in products)
    views = sum(item["views"] for item in products)
    carts = sum(item["carts"] for item in products)
    orders = sum(item["orders"] for item in products)
    buyouts = sum(item["buyouts"] for item in products)
    if not views and orders:
        views = orders * 18
    if not carts and orders:
        carts = round(orders / 0.3)
    if not reach and views:
        reach = round(views / 0.045)
    donors = sorted((p for p in products if p["adSpend"] >= 300), key=lambda p: p["drr"], reverse=True)
    receivers = sorted(
        (p for p in products if p["orders"] >= 5 and p["drr"] < 0.25),
        key=lambda p: (p["growth"], p["checkoutRate"]),
        reverse=True,
    )
    reallocations = []
    used = set()
    for donor in donors:
        receiver = next(
            (
                p
                for p in receivers
                if (p["store"], p["nmId"]) != (donor["store"], donor["nmId"])
                and (p["store"], p["nmId"]) not in used
            ),
            None,
        )
        if not receiver:
            break
        budget = round(max(100, min(donor["adSpend"] / PERIOD_DAYS * 0.35, 3000)))
        reallocations.append(
            {
                "from": donor["name"],
                "fromStore": donor["storeName"],
                "fromDrr": donor["drr"],
                "to": receiver["name"],
                "toStore": receiver["storeName"],
                "toConversion": receiver["checkoutRate"],
                "dailyBudget": budget,
            }
        )
        used.add((receiver["store"], receiver["nmId"]))
        if len(reallocations) == 3:
            break
    latest = max((p.get("dataUpdatedAt") or "" for p in products), default="") or None
    return {
        "meta": {
            "marketplace": "WB",
            "marketplaceName": "Wildberries",
            "generatedAt": _now_iso(),
            "lastAnalyticsAt": latest,
            "periodDays": PERIOD_DAYS,
            "products": len(products),
            "stores": selected,
        },
        "summary": {
            "potentialRevenue": round(sum(item["expectedRevenue"] for item in active), 2),
            "potentialProfit": round(sum(item["expectedProfit"] for item in active), 2),
            "decisions": len(active),
            "critical": sum(item["severity"] == "critical" for item in active),
            "inProgress": sum(item["status"] == "in_progress" for item in opportunities),
            "completed": sum(item["status"] == "completed" for item in opportunities),
            "productsAtRisk": sum(p["health"] < 55 or p["stockDays"] < 10 for p in products),
            "averageHealth": round(sum(p["health"] for p in products) / max(len(products), 1), 1),
            "sellThrough": buyouts / max(buyouts + sum(p["stock"] for p in products), 1),
        },
        "funnel": [
            {"key": "reach", "label": "Оценка охвата", "value": reach, "rate": 1},
            {"key": "views", "label": "Переходы", "value": views, "rate": views / max(reach, 1)},
            {"key": "carts", "label": "Корзины", "value": carts, "rate": carts / max(views, 1)},
            {"key": "orders", "label": "Заказы", "value": orders, "rate": orders / max(carts, 1)},
            {"key": "buyouts", "label": "Выкупы", "value": buyouts, "rate": buyouts / max(orders, 1)},
        ],
        "opportunities": opportunities,
        "portfolio": sorted(products, key=lambda item: (item["health"], item["stockDays"]))[:100],
        "reallocations": reallocations,
        "sync": _sync_summary(selected),
        "playbooks": [
            {
                "icon": "CTR",
                "title": "Показ → карточка",
                "trigger": "CTR ниже медианы",
                "action": "Тест главного фото, заголовка и поисковой релевантности",
                "metric": "CTR + заказы",
            },
            {
                "icon": "CART",
                "title": "Карточка → корзина",
                "trigger": "Много просмотров, мало корзин",
                "action": "Контент, видео, характеристики и ответы на возражения",
                "metric": "Cart rate",
            },
            {
                "icon": "₽",
                "title": "Корзина → заказ",
                "trigger": "Сильная корзина, слабый заказ",
                "action": "Цена, промо, доставка и доверие",
                "metric": "Checkout rate",
            },
            {
                "icon": "✓",
                "title": "Заказ → выкуп",
                "trigger": "Выкуп ниже нормы",
                "action": "Причины возврата, упаковка, комплектация и ожидания",
                "metric": "Выкуп",
            },
            {
                "icon": "BOX",
                "title": "Наличие",
                "trigger": "Сток ограничивает спрос",
                "action": "Пополнение и распределение по складам",
                "metric": "Дни наличия",
            },
            {
                "icon": "A/B",
                "title": "Эксперимент",
                "trigger": "Причина не доказана",
                "action": "Одна гипотеза, контроль и заранее выбранная метрика",
                "metric": "Инкрементальный эффект",
            },
        ],
    }
