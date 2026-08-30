from __future__ import annotations

import json
import logging
import math
import time
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from io import BytesIO

from app import db
from app.config import settings
from app.domain import MOSCOW_TIMEZONE
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens
from app.yandex import api as yandex_api
from app.yandex import tokens as yandex_tokens

logger = logging.getLogger(__name__)
MOSCOW = MOSCOW_TIMEZONE
SYNC_COOLDOWN = timedelta(minutes=settings.rnp_sync_cooldown_minutes)
_SCHEMA_READY = False

FUNNEL_COLUMNS = (
    "traffic_clicks",
    "traffic_carts",
    "traffic_orders",
    "buyout_count",
    "buyout_amount",
    "buyout_percent",
    "return_count",
    "return_amount",
)
AD_COLUMNS = (
    "ad_spend",
    "ad_media",
    "ad_internal",
    "ad_external",
    "ad_impressions",
    "ad_clicks",
    "ad_carts",
    "ad_orders",
    "ad_sales_amount",
)
CAMPAIGN_PREFIXES = (
    "unified",
    "manual_search",
    "manual_recommendations",
    "cpc_search",
)
CAMPAIGN_RAW_SUFFIXES = ("impressions", "clicks", "spend", "orders", "carts")
CAMPAIGN_COLUMNS = tuple(
    f"{prefix}_{suffix}" for prefix in CAMPAIGN_PREFIXES for suffix in CAMPAIGN_RAW_SUFFIXES
)
SELF_PURCHASE_COLUMNS = ("self_purchase_count", "self_purchase_amount")
PRICE_COLUMNS = ("price_before_spp", "price_after_spp", "spp_percent")
STOCK_COLUMNS = (
    "stock_units",
    "stock_value",
    "stock_total",
    "stock_velocity_7d",
    "stock_turnover_days",
    "stock_depletion_date",
    "stock_to_client",
    "stock_from_client",
    "stock_regions",
)
REPUTATION_COLUMNS = (
    "rating",
    "reviews_count",
    "reviews_delta",
    "reviews_1",
    "reviews_2",
)
PLAN_COLUMNS = (
    "plan_orders_amount",
    "plan_orders_count",
    "plan_buyouts_amount",
    "plan_buyouts_count",
    "plan_ad_budget",
    "plan_drr",
    "plan_margin",
    "plan_roi",
    "plan_profit",
)
SNAPSHOT_COLUMNS = (*PRICE_COLUMNS, *STOCK_COLUMNS, *REPUTATION_COLUMNS, *PLAN_COLUMNS)
ALL_COLUMNS = (
    *FUNNEL_COLUMNS,
    *AD_COLUMNS,
    *CAMPAIGN_COLUMNS,
    *SELF_PURCHASE_COLUMNS,
    *SNAPSHOT_COLUMNS,
)

DAILY_COLUMN_TYPES = {
    **{
        column: "INTEGER"
        for column in (
            "traffic_clicks",
            "traffic_carts",
            "traffic_orders",
            "buyout_count",
            "return_count",
            "ad_impressions",
            "ad_clicks",
            "ad_carts",
            "ad_orders",
            "self_purchase_count",
            "stock_units",
            "stock_total",
            "stock_to_client",
            "stock_from_client",
            "reviews_count",
            "reviews_delta",
            "reviews_1",
            "reviews_2",
            "plan_orders_count",
            "plan_buyouts_count",
            *(
                f"{prefix}_{suffix}"
                for prefix in CAMPAIGN_PREFIXES
                for suffix in ("impressions", "clicks", "orders", "carts")
            ),
        )
    },
    **{
        column: "REAL"
        for column in (
            "buyout_amount",
            "buyout_percent",
            "return_amount",
            "ad_spend",
            "ad_media",
            "ad_internal",
            "ad_external",
            "ad_sales_amount",
            "self_purchase_amount",
            "price_before_spp",
            "price_after_spp",
            "spp_percent",
            "stock_value",
            "stock_velocity_7d",
            "stock_turnover_days",
            "rating",
            "plan_orders_amount",
            "plan_buyouts_amount",
            "plan_ad_budget",
            "plan_drr",
            "plan_margin",
            "plan_roi",
            "plan_profit",
            *(f"{prefix}_spend" for prefix in CAMPAIGN_PREFIXES),
        )
    },
    "stock_depletion_date": "TEXT",
    "stock_regions": "TEXT",
    "funnel_synced_at": "TEXT",
    "advertising_synced_at": "TEXT",
    "snapshot_synced_at": "TEXT",
}


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


def _day(value) -> str:
    return str(value or "")[:10]


def init_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rnp_daily_metrics (
                    store_slug TEXT NOT NULL,
                    marketplace TEXT NOT NULL,
                    article TEXT NOT NULL,
                    day TEXT NOT NULL,
                    traffic_clicks INTEGER,
                    traffic_carts INTEGER,
                    traffic_orders INTEGER,
                    buyout_count INTEGER,
                    buyout_amount REAL,
                    buyout_percent REAL,
                    return_count INTEGER,
                    return_amount REAL,
                    ad_spend REAL,
                    ad_media REAL,
                    ad_internal REAL,
                    ad_external REAL,
                    ad_impressions INTEGER,
                    ad_clicks INTEGER,
                    ad_carts INTEGER,
                    ad_orders INTEGER,
                    ad_sales_amount REAL,
                    funnel_synced_at TEXT,
                    advertising_synced_at TEXT,
                    PRIMARY KEY (store_slug, marketplace, article, day)
                )
                """
            )
            existing_columns = conn.column_names("rnp_daily_metrics")
            for column, sql_type in DAILY_COLUMN_TYPES.items():
                if column not in existing_columns:
                    conn.execute(f"ALTER TABLE rnp_daily_metrics ADD COLUMN {column} {sql_type}")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rnp_metric_sync_state (
                    store_slug TEXT NOT NULL,
                    marketplace TEXT NOT NULL,
                    source TEXT NOT NULL,
                    period_from TEXT NOT NULL,
                    period_to TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_attempt_at TEXT NOT NULL,
                    last_success_at TEXT,
                    rows_received INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    PRIMARY KEY (store_slug, marketplace, source)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rnp_daily_lookup "
                "ON rnp_daily_metrics (store_slug, marketplace, day, article)"
            )
            conn.commit()
            _SCHEMA_READY = True
        finally:
            conn.close()


def _article_lookup(store_slug: str, marketplace: str) -> dict[str, str]:
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT article, mp_sku, mp_product_id FROM stock_items "
        "WHERE store_slug = ? AND marketplace = ? AND is_service = 0",
        (store_slug, marketplace),
    ).fetchall()
    conn.close()
    result: dict[str, str] = {}
    for row in rows:
        article = str(row["article"] or "").strip()
        for value in (article, row["mp_sku"], row["mp_product_id"]):
            key = str(value or "").strip()
            if key:
                result[key] = article
    return result


def _upsert(
    store_slug: str,
    marketplace: str,
    rows: list[dict],
    columns: tuple[str, ...],
    synced_column: str,
    preserve_nulls: bool = False,
) -> int:
    prepared = [row for row in rows if row.get("article") and row.get("day")]
    if not prepared:
        return 0
    all_columns = ("store_slug", "marketplace", "article", "day", *columns, synced_column)
    placeholders = ",".join("?" for _ in all_columns)
    updates = ",".join(
        (
            f"{column}=COALESCE(excluded.{column},rnp_daily_metrics.{column})"
            if preserve_nulls and column != synced_column
            else f"{column}=excluded.{column}"
        )
        for column in (*columns, synced_column)
    )
    synced_at = _now_iso()
    values = [
        (
            store_slug,
            marketplace,
            str(row["article"]),
            str(row["day"]),
            *(row.get(column) for column in columns),
            synced_at,
        )
        for row in prepared
    ]
    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.executemany(
                f"INSERT INTO rnp_daily_metrics ({','.join(all_columns)}) "
                f"VALUES ({placeholders}) ON CONFLICT(store_slug,marketplace,article,day) "
                f"DO UPDATE SET {updates}",
                values,
            )
            conn.commit()
        finally:
            conn.close()
    return len(values)


def _record_state(
    store_slug: str,
    marketplace: str,
    source: str,
    date_from: date,
    date_to: date,
    status: str,
    rows: int = 0,
    error: str | None = None,
) -> None:
    attempted = _now_iso()
    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.execute(
                """
                INSERT INTO rnp_metric_sync_state
                    (store_slug, marketplace, source, period_from, period_to,
                     status, last_attempt_at, last_success_at, rows_received, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(store_slug, marketplace, source) DO UPDATE SET
                    period_from=excluded.period_from,
                    period_to=excluded.period_to,
                    status=excluded.status,
                    last_attempt_at=excluded.last_attempt_at,
                    last_success_at=COALESCE(excluded.last_success_at,
                                             rnp_metric_sync_state.last_success_at),
                    rows_received=excluded.rows_received,
                    error=excluded.error
                """,
                (
                    store_slug,
                    marketplace,
                    source,
                    date_from.isoformat(),
                    date_to.isoformat(),
                    status,
                    attempted,
                    attempted if status == "success" else None,
                    rows,
                    error,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _fresh(
    store_slug: str, marketplace: str, source: str, date_from: date, date_to: date, force: bool
) -> bool:
    if force:
        return False
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM rnp_metric_sync_state WHERE store_slug=? AND marketplace=? AND source=?",
        (store_slug, marketplace, source),
    ).fetchone()
    conn.close()
    if not row or row["status"] != "success":
        return False
    if str(row["period_from"]) > date_from.isoformat() or str(row["period_to"]) < date_to.isoformat():
        return False
    try:
        moment = datetime.fromisoformat(str(row["last_success_at"]).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return False
    return datetime.now(UTC) - moment < SYNC_COOLDOWN


def _run_source(
    store_slug: str, marketplace: str, source: str, date_from: date, date_to: date, loader, force: bool
) -> dict:
    if _fresh(store_slug, marketplace, source, date_from, date_to, force):
        return {"source": source, "status": "fresh"}
    _record_state(store_slug, marketplace, source, date_from, date_to, "running")
    try:
        rows = loader()
        _record_state(store_slug, marketplace, source, date_from, date_to, "success", rows)
        return {"source": source, "status": "success", "rows": rows}
    except Exception as exc:
        message = str(getattr(exc, "friendly", "") or exc or type(exc).__name__)[:700]
        _record_state(store_slug, marketplace, source, date_from, date_to, "error", 0, message)
        logger.warning("RNP %s/%s/%s: %s", marketplace, store_slug, source, message)
        return {"source": source, "status": "error", "error": message}


def _sync_wb_funnel(
    store_slug: str, date_from: date, date_to: date, articles: list[str] | None = None
) -> int:

    today = datetime.now(MOSCOW).date()
    start = max(date_from, today - timedelta(days=6))
    end = min(date_to, today)
    if start > end:
        return 0
    lookup = _article_lookup(store_slug, "WB")
    selected_articles = {str(value) for value in articles} if articles is not None else None
    nm_ids = []
    for key, article in lookup.items():
        base = key.split("/", 1)[0]
        if not base.isdigit() or selected_articles is not None and article not in selected_articles:
            continue
        value = int(base)
        if value not in nm_ids:
            nm_ids.append(value)

    if articles is None:
        nm_ids = nm_ids[:20]
    if not nm_ids:
        return 0
    response: list[dict] = []
    token = wb_tokens.get_token(store_slug)
    for index in range(0, len(nm_ids), 20):
        response.extend(
            wb_api.request(
                "POST",
                "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products/history",
                token,
                json_body={
                    "selectedPeriod": {"start": start.isoformat(), "end": end.isoformat()},
                    "nmIds": nm_ids[index : index + 20],
                    "skipDeletedNm": True,
                    "aggregationLevel": "day",
                },
            )
            or []
        )
    rows: list[dict] = []
    for product_row in _items(response):
        product_row = _mapping(product_row)
        product = _mapping(product_row.get("product"))
        article = lookup.get(str(product.get("nmId") or "")) or str(product.get("vendorCode") or "")
        if not article:
            continue
        for raw in _items(product_row.get("history")):
            item = _mapping(raw)
            rows.append(
                {
                    "article": article,
                    "day": _day(item.get("date")),
                    "traffic_clicks": _integer(item.get("openCount")),
                    "traffic_carts": _integer(item.get("cartCount")),
                    "traffic_orders": _integer(item.get("orderCount")),
                    "buyout_count": _integer(item.get("buyoutCount")),
                    "buyout_amount": _number(item.get("buyoutSum")),
                    "buyout_percent": _number(item.get("buyoutPercent")),
                    "return_count": _integer(item.get("returnCount"))
                    if item.get("returnCount") is not None
                    else None,
                    "return_amount": _number(item.get("returnSum"))
                    if item.get("returnSum") is not None
                    else None,
                }
            )
    return _upsert(store_slug, "WB", rows, FUNNEL_COLUMNS, "funnel_synced_at")


def _wb_campaign_ids(token: str) -> list[str]:
    response = wb_api.request("GET", "https://advert-api.wildberries.ru/adv/v1/promotion/count", token)
    campaigns: list[dict] = []
    for group in _items(_mapping(response).get("adverts")):
        group = _mapping(group)
        if _integer(group.get("status")) in {7, 9, 11}:
            campaigns.extend(_mapping(row) for row in _items(group.get("advert_list")))
    campaigns.sort(key=lambda row: str(row.get("changeTime") or ""), reverse=True)
    return [str(_integer(row.get("advertId"))) for row in campaigns[:50] if _integer(row.get("advertId"))]


def _wb_campaign_kinds(token: str, ids: list[str]) -> dict[str, str]:

    if not ids:
        return {}
    try:
        response = wb_api.request(
            "GET",
            "https://advert-api.wildberries.ru/api/advert/v2/adverts",
            token,
            params={"ids": ",".join(ids)},
        )
    except Exception as exc:
        logger.warning("WB: не удалось определить типы рекламных кампаний: %s", exc)
        return {}

    adverts = response if isinstance(response, list) else _items(_mapping(response).get("adverts"))
    result: dict[str, str] = {}
    for raw in _items(adverts):
        advert = _mapping(raw)
        advert_id = str(_integer(advert.get("id") or advert.get("advertId") or advert.get("advert_id")))
        if not advert_id or advert_id == "0":
            continue
        settings = _mapping(advert.get("settings"))
        bid_type = str(advert.get("bid_type") or advert.get("bidType") or "").casefold()
        payment_type = str(
            settings.get("payment_type")
            or settings.get("paymentType")
            or advert.get("payment_type")
            or advert.get("paymentType")
            or ""
        ).casefold()
        placements = _mapping(settings.get("placements") or advert.get("placements"))
        in_search = bool(placements.get("search"))
        in_recommendations = bool(placements.get("recommendations") or placements.get("recommendation"))
        if payment_type == "cpc":
            result[advert_id] = "cpc_search"
        elif bid_type == "unified":
            result[advert_id] = "unified"
        elif bid_type == "manual" and in_search and not in_recommendations:
            result[advert_id] = "manual_search"
        elif bid_type == "manual" and in_recommendations and not in_search:
            result[advert_id] = "manual_recommendations"

    return result


def _sync_wb_advertising(store_slug: str, date_from: date, date_to: date) -> int:
    token = wb_tokens.get_token(store_slug)
    ids = _wb_campaign_ids(token)
    if not ids:
        return 0
    campaign_kinds = _wb_campaign_kinds(token, ids)
    lookup = _article_lookup(store_slug, "WB")
    grouped: dict[tuple[str, str], dict] = {}
    cursor = date_from
    while cursor <= date_to:
        end = min(cursor + timedelta(days=30), date_to)
        response = wb_api.request(
            "GET",
            "https://advert-api.wildberries.ru/adv/v3/fullstats",
            token,
            params={"ids": ",".join(ids), "beginDate": cursor.isoformat(), "endDate": end.isoformat()},
        )
        for raw_campaign in _items(response):
            campaign = _mapping(raw_campaign)
            campaign_id = str(
                _integer(campaign.get("advertId") or campaign.get("advert_id") or campaign.get("id"))
            )
            campaign_prefix = campaign_kinds.get(campaign_id)
            for raw_day in _items(campaign.get("days")):
                day_row = _mapping(raw_day)
                day_value = _day(day_row.get("date"))
                for app_row in _items(day_row.get("apps")):
                    for raw_nm in _items(_mapping(app_row).get("nm") or _mapping(app_row).get("nms")):
                        nm = _mapping(raw_nm)
                        article = lookup.get(str(nm.get("nmId") or ""))
                        if not article or not day_value:
                            continue
                        target = grouped.setdefault(
                            (article, day_value),
                            {
                                "article": article,
                                "day": day_value,
                                "ad_spend": 0.0,
                                "ad_media": 0.0,
                                "ad_internal": 0.0,
                                "ad_external": 0.0,
                                "ad_impressions": 0,
                                "ad_clicks": 0,
                                "ad_carts": 0,
                                "ad_orders": 0,
                                "ad_sales_amount": 0.0,
                            },
                        )
                        target["ad_spend"] += _number(nm.get("sum"), _number(nm.get("spend")))
                        target["ad_internal"] = target["ad_spend"]
                        target["ad_impressions"] += _integer(nm.get("views"))
                        target["ad_clicks"] += _integer(nm.get("clicks"))
                        target["ad_carts"] += _integer(nm.get("atbs"))
                        target["ad_orders"] += _integer(nm.get("orders"))
                        target["ad_sales_amount"] += _number(nm.get("sum_price"), _number(nm.get("revenue")))
                        if campaign_prefix:
                            target[f"{campaign_prefix}_impressions"] = _integer(
                                target.get(f"{campaign_prefix}_impressions")
                            ) + _integer(nm.get("views"))
                            target[f"{campaign_prefix}_clicks"] = _integer(
                                target.get(f"{campaign_prefix}_clicks")
                            ) + _integer(nm.get("clicks"))
                            target[f"{campaign_prefix}_spend"] = _number(
                                target.get(f"{campaign_prefix}_spend")
                            ) + _number(nm.get("sum"), _number(nm.get("spend")))
                            target[f"{campaign_prefix}_orders"] = _integer(
                                target.get(f"{campaign_prefix}_orders")
                            ) + _integer(nm.get("orders"))
                            target[f"{campaign_prefix}_carts"] = _integer(
                                target.get(f"{campaign_prefix}_carts")
                            ) + _integer(nm.get("atbs"))
        cursor = end + timedelta(days=1)
    for item in grouped.values():
        for column in ("ad_spend", "ad_media", "ad_internal", "ad_external", "ad_sales_amount"):
            item[column] = round(_number(item[column]), 2)
        for prefix in CAMPAIGN_PREFIXES:
            column = f"{prefix}_spend"
            if item.get(column) is not None:
                item[column] = round(_number(item[column]), 2)
    return _upsert(
        store_slug,
        "WB",
        list(grouped.values()),
        (*AD_COLUMNS, *CAMPAIGN_COLUMNS, *SELF_PURCHASE_COLUMNS),
        "advertising_synced_at",
    )


def _ozon_dimensions(row: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in _items(row.get("dimensions")):
        item = _mapping(item)
        key = str(item.get("key") or item.get("name") or item.get("type") or "").lower()
        value = str(item.get("id") or item.get("value") or item.get("name") or "")
        if "day" in key or len(value) >= 10 and value[4:5] == "-":
            result["day"] = _day(value)
        elif "sku" in key or value.isdigit():
            result["sku"] = value
    return result


def _sync_ozon_funnel(store_slug: str, date_from: date, date_to: date) -> int:
    client_id, api_key = ozon_tokens.get_credentials(store_slug)

    metrics = ["ordered_units"]
    lookup = _article_lookup(store_slug, "OZON")
    rows: list[dict] = []
    offset = 0
    while True:
        payload = {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "metrics": metrics,
            "dimension": ["day", "sku"],
            "filters": [],
            "sort": [],
            "limit": 1000,
            "offset": offset,
        }
        response = ozon_api.request("/v1/analytics/data", client_id, api_key, payload)
        page = _items(_mapping(response).get("result", response).get("data"))
        for raw in page:
            item = _mapping(raw)
            dimensions = _ozon_dimensions(item)
            article = lookup.get(dimensions.get("sku", ""))
            day_value = dimensions.get("day", "")
            if not article or not day_value:
                continue
            values = _items(item.get("metrics"))
            metric_map = {
                name: _number(values[index]) if index < len(values) else 0.0
                for index, name in enumerate(metrics)
            }
            rows.append(
                {
                    "article": article,
                    "day": day_value,
                    "traffic_clicks": None,
                    "traffic_carts": None,
                    "traffic_orders": _integer(metric_map.get("ordered_units")),
                    "buyout_count": None,
                    "buyout_amount": None,
                    "buyout_percent": None,
                    "return_count": None,
                    "return_amount": None,
                }
            )
        if len(page) < 1000:
            break
        offset += len(page)
        if offset > 200_000:
            break

    with db.WRITE_LOCK:
        conn = db.get_connection()
        try:
            conn.execute(
                "UPDATE rnp_daily_metrics SET traffic_clicks=NULL, traffic_carts=NULL, "
                "traffic_orders=NULL, buyout_count=NULL, buyout_amount=NULL, "
                "buyout_percent=NULL, return_count=NULL, return_amount=NULL "
                "WHERE store_slug=? AND marketplace='OZON' AND day>=? AND day<=?",
                (store_slug, date_from.isoformat(), date_to.isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    return _upsert(store_slug, "OZON", rows, FUNNEL_COLUMNS, "funnel_synced_at")


def _walk_report_rows(value):
    if isinstance(value, dict):
        if value.get("offerId") and (value.get("day") or value.get("date")):
            yield value
        for child in value.values():
            yield from _walk_report_rows(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_report_rows(child)


def _download_json_report(url: str) -> list[dict]:
    with urllib.request.urlopen(url, timeout=settings.rnp_report_download_timeout_seconds) as response:
        content = response.read()
    result: list[dict] = []
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".json"):
                continue
            data = json.loads(archive.read(name).decode("utf-8-sig"))
            result.extend(_walk_report_rows(data))
    return result


def _sync_yandex_funnel(store_slug: str, date_from: date, date_to: date) -> int:
    api_key = yandex_tokens.get_api_key(store_slug)
    campaigns = yandex_api.get_campaigns(api_key)
    grouped: dict[tuple[str, str], dict] = {}
    business_ids: set[int] = set()
    for campaign in campaigns:
        campaign = _mapping(campaign)
        business = _mapping(campaign.get("business"))
        business_id = _integer(business.get("id") or campaign.get("businessId"))
        if business_id:
            business_ids.add(business_id)
    for business_id in sorted(business_ids):
        generated = yandex_api.request(
            "/v2/reports/shows-sales/generate",
            api_key,
            payload={
                "businessId": business_id,
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
                "grouping": "OFFERS",
            },
            params={"format": "JSON"},
        )
        report_id = str(_mapping(generated).get("reportId") or "")
        if not report_id:
            raise RuntimeError("Яндекс Маркет не вернул идентификатор отчёта аналитики")
        report_info: dict = {}
        for _ in range(settings.rnp_report_poll_attempts):
            report_info = yandex_api.request(
                f"/v2/reports/info/{report_id}",
                api_key,
                params={"sourceType": "SELLER"},
                method="GET",
            )
            status = str(report_info.get("status") or "")
            if status == "DONE":
                break
            if status == "FAILED":
                raise RuntimeError(
                    f"Отчёт Яндекс Маркета не сформирован: {report_info.get('subStatus') or status}"
                )
            time.sleep(settings.rnp_report_poll_interval_seconds)
        if str(report_info.get("status") or "") != "DONE":
            raise RuntimeError("Отчёт Яндекс Маркета ещё формируется; повторите обновление через минуту")
        for item in _download_json_report(str(report_info.get("file") or "")):
            article = str(item.get("offerId") or "").strip()
            day_value = _day(item.get("day") or item.get("date"))
            if not article or not day_value:
                continue
            target = grouped.setdefault(
                (article, day_value),
                {
                    "article": article,
                    "day": day_value,
                    "traffic_clicks": 0,
                    "traffic_carts": 0,
                    "traffic_orders": 0,
                    "buyout_count": 0,
                    "buyout_amount": 0.0,
                    "buyout_percent": None,
                    "return_count": 0,
                    "return_amount": None,
                },
            )
            target["traffic_clicks"] += _integer(item.get("clicks"))
            target["traffic_carts"] += _integer(item.get("toCart"))
            target["traffic_orders"] += _integer(item.get("orderItems"))
            target["buyout_count"] += _integer(item.get("orderItemsDeliveredCount"))
            target["buyout_amount"] += _number(item.get("orderItemsDeliveredTotalAmount"))
            target["return_count"] += _integer(item.get("orderItemsReturnedCount"))
    for item in grouped.values():
        orders = _integer(item.get("traffic_orders"))
        item["buyout_percent"] = (
            round(_integer(item.get("buyout_count")) / orders * 100, 2) if orders else None
        )
    return _upsert(store_slug, "YANDEX MARKET", list(grouped.values()), FUNNEL_COLUMNS, "funnel_synced_at")


def _history_price_rows(
    store_slug: str, marketplace: str, date_from: date, date_to: date, articles: list[str] | None
) -> list[dict]:

    params: list = [store_slug, marketplace, date_from.isoformat(), (date_to + timedelta(days=1)).isoformat()]
    article_sql = ""
    if articles:
        article_sql = " AND article IN (" + ",".join("?" for _ in articles) + ")"
        params.extend(articles)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT article, substr(ordered_at,1,10) AS day, "
        "SUM(CASE WHEN order_amount-cancelled_amount>0 "
        "THEN order_amount-cancelled_amount ELSE 0 END) AS amount, "
        "SUM(CASE WHEN quantity-cancelled_quantity>0 "
        "THEN quantity-cancelled_quantity ELSE 0 END) AS units "
        "FROM sales_order_lines WHERE store_slug=? AND marketplace=? "
        "AND ordered_at>=? AND ordered_at<?" + article_sql + " GROUP BY article, substr(ordered_at,1,10)",
        params,
    ).fetchall()
    result = []
    for row in rows:
        units = _integer(row["units"])
        result.append(
            {
                "article": str(row["article"] or ""),
                "day": str(row["day"] or ""),
                "price_before_spp": None,
                "price_after_spp": round(_number(row["amount"]) / units, 2) if units else None,
                "spp_percent": None,
            }
        )

    if marketplace == "WB":
        raw_rows = conn.execute(
            "SELECT article, substr(ordered_at,1,10) AS day, raw_json "
            "FROM sales_order_lines WHERE store_slug=? AND marketplace='WB' "
            "AND ordered_at>=? AND ordered_at<?" + article_sql,
            [store_slug, date_from.isoformat(), (date_to + timedelta(days=1)).isoformat(), *(articles or [])],
        ).fetchall()
        aggregate: dict[tuple[str, str], dict] = {}
        for row in raw_rows:
            try:
                raw = json.loads(str(row["raw_json"] or "{}"))
            except (TypeError, ValueError):
                continue
            if not isinstance(raw, dict) or raw.get("isCancel"):
                continue
            before = raw.get("priceWithDisc")
            after = raw.get("finishedPrice")
            spp = raw.get("spp")
            target = aggregate.setdefault(
                (str(row["article"] or ""), str(row["day"] or "")),
                {
                    "before": [],
                    "after": [],
                    "spp": [],
                },
            )
            if before is not None:
                target["before"].append(_number(before))
            if after is not None:
                target["after"].append(_number(after))
            if spp is not None:
                target["spp"].append(_number(spp))
        by_key = {(row["article"], row["day"]): row for row in result}
        for key, values in aggregate.items():
            target = by_key.setdefault(
                key,
                {
                    "article": key[0],
                    "day": key[1],
                    "price_before_spp": None,
                    "price_after_spp": None,
                    "spp_percent": None,
                },
            )
            if values["before"]:
                target["price_before_spp"] = round(sum(values["before"]) / len(values["before"]), 2)
            if values["after"]:
                target["price_after_spp"] = round(sum(values["after"]) / len(values["after"]), 2)
            if values["spp"]:
                target["spp_percent"] = round(sum(values["spp"]) / len(values["spp"]), 2)
        result = list(by_key.values())
    conn.close()
    return result


def _ozon_current_prices(store_slug: str) -> dict[str, tuple[float | None, float | None]]:
    result: dict[str, tuple[float | None, float | None]] = {}
    try:
        client_id, api_key = ozon_tokens.get_credentials(store_slug)
        cursor = ""
        while True:
            response = ozon_api.request(
                "/v5/product/info/prices",
                client_id,
                api_key,
                {"cursor": cursor, "filter": {"visibility": "ALL"}, "limit": 1000},
            )
            page = _items(response.get("items") or _mapping(response.get("result")).get("items"))
            for raw in page:
                item = _mapping(raw)
                offer_id = str(item.get("offer_id") or item.get("offerId") or "").strip()
                price = _mapping(item.get("price"))
                before = _number(price.get("old_price"), 0.0) or None
                after = (
                    _number(price.get("marketing_seller_price"), 0.0)
                    or _number(price.get("marketing_price"), 0.0)
                    or _number(price.get("retail_price"), 0.0)
                    or None
                )
                if offer_id:
                    result[offer_id] = (before, after)
            next_cursor = str(response.get("cursor") or _mapping(response.get("result")).get("cursor") or "")
            if not page or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
    except Exception as exc:
        logger.warning("Ozon: текущие цены для снимка РНП недоступны: %s", exc)
    return result


def _yandex_current_prices(store_slug: str) -> dict[str, tuple[float | None, float | None]]:
    result: dict[str, tuple[float | None, float | None]] = {}
    try:
        api_key = yandex_tokens.get_api_key(store_slug)
        for campaign in yandex_api.get_campaigns(api_key):
            campaign_id = _integer(_mapping(campaign).get("id"))
            if not campaign_id:
                continue
            page_token = ""
            while True:
                response = yandex_api.request(
                    f"/v2/campaigns/{campaign_id}/offer-prices",
                    api_key,
                    params={"limit": 500, "pageToken": page_token},
                    method="GET",
                )
                page = _items(response.get("offers"))
                for raw in page:
                    item = _mapping(raw)
                    offer_id = str(item.get("id") or "").strip()
                    price = _mapping(item.get("price"))
                    before = _number(price.get("discountBase"), 0.0) or None
                    after = _number(price.get("value"), 0.0) or None
                    if offer_id:
                        result[offer_id] = (before, after)
                next_token = str(_mapping(response.get("paging")).get("nextPageToken") or "")
                if not page or not next_token or next_token == page_token:
                    break
                page_token = next_token
    except Exception as exc:
        logger.warning("Яндекс Маркет: текущие цены для снимка РНП недоступны: %s", exc)
    return result


def _wb_current_reputation(rows: list[dict]) -> dict[str, tuple[float | None, int | None]]:
    nm_to_article: dict[str, str] = {}
    for row in rows:
        for raw in (row.get("nm_id"), row.get("mp_sku"), row.get("mp_product_id"), row.get("article")):
            value = str(raw or "").strip()
            if value.isdigit():
                nm_to_article[value] = str(row["article"])
                break
    result: dict[str, tuple[float | None, int | None]] = {}
    try:
        report = wb_api.get_storefront_products(list(nm_to_article))
    except Exception as exc:
        logger.warning("WB: рейтинг и отзывы для снимка РНП недоступны: %s", exc)
        return result
    for raw in _items(_mapping(report).get("products")):
        item = _mapping(raw)
        article = nm_to_article.get(str(item.get("id") or item.get("nmId") or item.get("nmID") or ""))
        if article:
            result[article] = (
                _number(item.get("reviewRating"), 0.0) if item.get("reviewRating") is not None else None,
                _integer(item.get("feedbacks")) if item.get("feedbacks") is not None else None,
            )
    return result


def _current_snapshot_rows(
    store_slug: str, marketplace: str, snapshot_day: date, articles: list[str] | None
) -> list[dict]:
    params: list = [store_slug, marketplace]
    article_sql = ""
    if articles:
        article_sql = " AND si.article IN (" + ",".join("?" for _ in articles) + ")"
        params.extend(articles)
    conn = db.get_connection()
    rows = [
        dict(row)
        for row in conn.execute(
            "WITH stock AS (SELECT article,SUM(quantity) AS quantity FROM mp_stock "
            "WHERE store_slug=? AND marketplace=? GROUP BY article) "
            "SELECT si.article,si.mp_sku,si.mp_product_id,COALESCE(stock.quantity,0) AS stock_units,"
            "NULL AS nm_id,NULL AS list_price,NULL AS discounted_price,"
            "NULL AS buyer_price,NULL AS spp_percent "
            "FROM stock_items si LEFT JOIN stock ON stock.article=si.article "
            "WHERE si.store_slug=? AND si.marketplace=? AND si.is_service=0" + article_sql,
            [store_slug, marketplace, store_slug, marketplace, *(articles or [])],
        ).fetchall()
    ]

    region_rows = conn.execute(
        "SELECT article,COALESCE(NULLIF(cluster,''),warehouse) AS region,SUM(quantity) AS quantity "
        "FROM mp_warehouse_stock WHERE store_slug=? AND marketplace=? GROUP BY article,region",
        (store_slug, marketplace),
    ).fetchall()
    regions: dict[str, dict[str, int]] = {}
    for row in region_rows:
        quantity = _integer(row["quantity"])
        if quantity:
            regions.setdefault(str(row["article"]), {})[str(row["region"] or "Без региона")] = quantity

    velocity_from = (snapshot_day - timedelta(days=6)).isoformat()
    velocity_to = (snapshot_day + timedelta(days=1)).isoformat()
    velocity_rows = conn.execute(
        "SELECT article,SUM(sold_quantity) AS units FROM sales_order_lines "
        "WHERE store_slug=? AND marketplace=? AND sold_at>=? AND sold_at<? GROUP BY article",
        (store_slug, marketplace, velocity_from, velocity_to),
    ).fetchall()
    velocities = {str(row["article"]): _number(row["units"]) / 7 for row in velocity_rows}

    previous_rows = conn.execute(
        "SELECT article,reviews_count," + ",".join(PLAN_COLUMNS) + " FROM rnp_daily_metrics "
        "WHERE store_slug=? AND marketplace=? AND day<? ORDER BY article,day DESC",
        (store_slug, marketplace, snapshot_day.isoformat()),
    ).fetchall()
    previous: dict[str, dict] = {}
    for row in previous_rows:
        previous.setdefault(str(row["article"]), dict(row))
    conn.close()

    current_prices: dict[str, tuple[float | None, float | None]] = {}
    if marketplace == "OZON" and ozon_tokens.has_credentials(store_slug):
        current_prices = _ozon_current_prices(store_slug)
    elif marketplace == "YANDEX MARKET" and yandex_tokens.has_credentials(store_slug):
        current_prices = _yandex_current_prices(store_slug)
    reputation = _wb_current_reputation(rows) if marketplace == "WB" else {}

    result: list[dict] = []
    for source in rows:
        article = str(source["article"])
        if marketplace == "WB":
            before = source.get("list_price")
            after = source.get("buyer_price")
            if after is None:
                after = source.get("discounted_price")
            spp = source.get("spp_percent")
        else:
            before, after = current_prices.get(article, (None, None))
            spp = None
        stock = _integer(source.get("stock_units"))
        velocity = round(velocities.get(article, 0.0), 2)
        turnover = round(stock / velocity, 2) if velocity > 0 else None
        depletion = (
            (snapshot_day + timedelta(days=max(0, math.ceil(turnover)))).isoformat()
            if turnover is not None
            else None
        )
        rating, reviews = reputation.get(article, (None, None))
        old = previous.get(article, {})
        reviews_delta = None
        if reviews is not None and old.get("reviews_count") is not None:
            reviews_delta = reviews - _integer(old.get("reviews_count"))
        item = {
            "article": article,
            "day": snapshot_day.isoformat(),
            "price_before_spp": round(_number(before), 2) if before is not None else None,
            "price_after_spp": round(_number(after), 2) if after is not None else None,
            "spp_percent": round(_number(spp), 2) if spp is not None else None,
            "stock_units": stock,
            "stock_value": round(stock * _number(before if before is not None else after), 2)
            if before is not None or after is not None
            else None,
            "stock_total": stock,
            "stock_velocity_7d": velocity,
            "stock_turnover_days": turnover,
            "stock_depletion_date": depletion,
            "stock_to_client": None,
            "stock_from_client": None,
            "stock_regions": json.dumps(regions.get(article, {}), ensure_ascii=False, sort_keys=True),
            "rating": rating,
            "reviews_count": reviews,
            "reviews_delta": reviews_delta,
            "reviews_1": None,
            "reviews_2": None,
        }
        for column in PLAN_COLUMNS:
            item[column] = old.get(column)
        result.append(item)
    return result


def _sync_daily_snapshots(
    store_slug: str, marketplace: str, date_from: date, date_to: date, articles: list[str] | None
) -> int:
    history = _history_price_rows(store_slug, marketplace, date_from, date_to, articles)
    stored = _upsert(store_slug, marketplace, history, PRICE_COLUMNS, "snapshot_synced_at")
    today = datetime.now(MOSCOW).date()
    if date_from <= today <= date_to:
        current = _current_snapshot_rows(store_slug, marketplace, today, articles)
        stored += _upsert(
            store_slug,
            marketplace,
            current,
            SNAPSHOT_COLUMNS,
            "snapshot_synced_at",
            preserve_nulls=True,
        )
    return stored


def sync_store(
    store_slug: str,
    marketplace: str,
    date_from: date,
    date_to: date,
    force: bool = False,
    articles: list[str] | None = None,
) -> dict:
    init_schema()
    marketplace = str(marketplace or "").upper()
    if store_slug not in STORES:
        raise ValueError("Неизвестный магазин")
    sources: list[tuple[str, object]] = [
        ("snapshot", lambda: _sync_daily_snapshots(store_slug, marketplace, date_from, date_to, articles)),
    ]
    configured = True
    if marketplace == "WB":
        configured = wb_tokens.has_token(store_slug)
        if configured:
            sources.extend(
                (
                    ("funnel", lambda: _sync_wb_funnel(store_slug, date_from, date_to, articles)),
                    ("advertising", lambda: _sync_wb_advertising(store_slug, date_from, date_to)),
                )
            )
    elif marketplace == "OZON":
        configured = ozon_tokens.has_credentials(store_slug)
        if configured:
            sources.append(("funnel", lambda: _sync_ozon_funnel(store_slug, date_from, date_to)))
    elif marketplace == "YANDEX MARKET":
        configured = yandex_tokens.has_credentials(store_slug)
        if configured:
            sources.append(("funnel", lambda: _sync_yandex_funnel(store_slug, date_from, date_to)))
    else:
        raise ValueError("Неизвестный маркетплейс")
    results = [
        _run_source(store_slug, marketplace, source, date_from, date_to, loader, force)
        for source, loader in sources
    ]
    return {"store": store_slug, "marketplace": marketplace, "configured": configured, "results": results}


def sync_current(force: bool = False) -> dict:
    today = datetime.now(MOSCOW).date()
    date_from = today.replace(day=1)
    report: dict[str, dict] = {}
    for marketplace in ("WB", "OZON", "YANDEX MARKET"):
        report[marketplace] = {}
        for store_slug in STORES:
            result = sync_store(store_slug, marketplace, date_from, today, force)
            if result.get("results"):
                report[marketplace][store_slug] = result
    return report


def get_daily(
    store_slug: str, marketplace: str, date_from: str, date_to: str, articles: list[str] | None = None
) -> list[dict]:
    init_schema()
    params: list = [store_slug, marketplace, date_from, date_to]
    article_sql = ""
    if articles is not None:
        if not articles:
            return []
        article_sql = " AND article IN (" + ",".join("?" for _ in articles) + ")"
        params.extend(articles)
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM rnp_daily_metrics WHERE store_slug=? AND marketplace=? "
        "AND day>=? AND day<?" + article_sql + " ORDER BY article, day",
        params,
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_states(store_slug: str, marketplace: str) -> list[dict]:
    init_schema()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM rnp_metric_sync_state WHERE store_slug=? AND marketplace=? ORDER BY source",
        (store_slug, marketplace),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
