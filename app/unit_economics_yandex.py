"""Yandex metrics in the shared layout, isolated from the WB economics pipeline."""

from collections import defaultdict
from datetime import date, datetime, timedelta

from app import db, unit_economics_1c
from app.domain import MOSCOW_TIMEZONE
from app.repositories import unit_economics_yandex as repository
from app.repositories import (
    yandex_assortment,
    yandex_product_statuses,
    yandex_source_values,
)
from app.stores import STORES

MARKETPLACE = "YANDEX MARKET"


def catalog_product(
    store_slug: str,
    product: dict,
    *,
    stock: dict | None = None,
    reputation: dict | None = None,
    economics: dict | None = None,
    advertising: dict | None = None,
    is_new: bool | None = None,
    source_values: dict | None = None,
) -> dict:
    store = STORES[store_slug]
    article = str(product["article"])
    source_values = source_values or {}
    return {
        "id": f"yandex:{store_slug}:{article}",
        "marketplace": MARKETPLACE,
        "store_slug": store_slug,
        "store_name": store["name"],
        "article": article,
        "barcode": product.get("barcode") or None,
        "name": product.get("name") or article,
        "image_url": product.get("image_url") or None,
        "mp_sku": product.get("mp_sku") or None,
        "mp_product_id": product.get("mp_product_id") or None,
        "rating": (reputation or {}).get("rating"),
        "reviews_count": (reputation or {}).get("reviews_count"),
        "is_new": is_new,
        "sales_days": None,
        "price": dict.fromkeys(("current", "with_spp", "with_wallet")),
        "current_economics": dict.fromkeys(
            ("margin", "roi", "orders", "buyout_percent", "advertising_spend", "period_to")
        ),
        "economics_7d": economics or dict.fromkeys(("turnover", "margin", "roi")),
        "advertising": advertising
        or dict.fromkeys(("drr", "spend", "ctr", "cpc", "orders_amount", "period_from", "period_to")),
        "tag_data": {
            key: source_values.get(field)
            for key, field in (
                ("goal_week", "goal_week"),
                ("goal_day", "goal_day"),
                ("status", "stock_status"),
                ("ends", "stock_end_week"),
                ("code", "abc_code"),
                ("fact", "fact_sales"),
                ("plan", "plan_sales"),
            )
        },
        "stock": stock
        or dict.fromkeys(
            (
                "total",
                "fbs",
                "fbo",
                "fulfillment",
                "days",
                "state",
                "period_days",
                "orders_21d",
                "average_daily_orders",
            )
        ),
        "details": {
            **dict.fromkeys(
                (
                    "retail_price",
                    "customer_price",
                    "commission_percent",
                    "logistics",
                    "drr",
                    "buyout_percent",
                    "purchase_cost",
                    "fulfillment_cost",
                    "acquiring",
                    "storage",
                    "tax",
                    "volume_l",
                    "weight_kg",
                    "category",
                )
            ),
            "purchase_cost": source_values.get("purchase_price"),
            "fulfillment_cost": source_values.get("fulfillment_cost"),
        },
        "history": None,
    }


def _covers(snapshot: dict, start: date, end: date) -> bool:
    return (
        snapshot.get("data") is not None
        and str(snapshot.get("period_from") or "9999") <= start.isoformat()
        and str(snapshot.get("period_to") or "") >= end.isoformat()
    )


def _orders_cover(snapshot: dict, sync_states: list[dict], start: date, end: date) -> bool:
    """Check complete history using the cached window and successful local order loads."""
    periods = []
    if snapshot.get("data") is not None:
        periods.append(
            (date.fromisoformat(snapshot["period_from"]), date.fromisoformat(snapshot["period_to"]))
        )
    for state in sync_states:
        days = int(state.get("lookback_days") or 0)
        if state.get("last_success_at") and days > 0:
            last_day = date.fromisoformat(str(state["last_success_at"])[:10])
            periods.append((last_day - timedelta(days=days - 1), last_day))
    covered_to = start - timedelta(days=1)
    for period_from, period_to in sorted(periods):
        if period_from > covered_to + timedelta(days=1):
            break
        covered_to = max(covered_to, period_to)
    return covered_to >= end


def load_products(
    store_slugs: tuple[str, ...], *, article: str = "", today: date | None = None
) -> list[dict]:
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    start, end = today - timedelta(days=7), today - timedelta(days=1)
    stock_start = today - timedelta(days=unit_economics_1c.STOCK_COVERAGE_PERIOD_DAYS - 1)
    products = []
    for slug in store_slugs:
        active = yandex_assortment.active_articles(slug)
        catalog = [row for row in db.get_catalog_items(slug, MARKETPLACE) if row["article"] in active]
        stocks = {row["article"]: row for row in db.get_stock_items(slug, MARKETPLACE, ("fbs", "fbo"))}
        snapshots = repository.get_snapshots(slug)
        product_statuses = yandex_product_statuses.get_statuses(slug)
        source_values = yandex_source_values.get_values(slug)
        orders_snapshot = snapshots.get("orders") or {}
        if _covers(orders_snapshot, stock_start, today):
            orders = orders_snapshot["data"]
            orders_known = True
            closed_known = True
        else:
            orders = repository.get_daily_orders(
                slug, stock_start.isoformat(), (today + timedelta(days=1)).isoformat()
            )
            sync_states = db.get_sales_sync_states(MARKETPLACE, slug)
            orders_known = _orders_cover(orders_snapshot, sync_states, stock_start, today)
            closed_known = _orders_cover(orders_snapshot, sync_states, start, end)
            if orders_snapshot.get("data") is not None:
                cached = {
                    (row["article"], row["day"]): row
                    for row in orders
                    if not orders_snapshot["period_from"] <= row["day"] <= orders_snapshot["period_to"]
                }
                cached.update({(row["article"], row["day"]): row for row in orders_snapshot["data"]})
                orders = list(cached.values())
        by_article = defaultdict(list)
        for row in orders:
            if stock_start.isoformat() <= row["day"] <= today.isoformat():
                by_article[row["article"]].append(row)
        reputation = {str(row["sku"]): row for row in (snapshots.get("reputation") or {}).get("data") or []}
        ads_snapshot = snapshots.get("advertising") or {}
        ads_known = _covers(ads_snapshot, start, end) and (
            ads_snapshot["period_from"] == start.isoformat() and ads_snapshot["period_to"] == end.isoformat()
        )
        ads = {row["article"]: row for row in ads_snapshot.get("data") or []} if ads_known else {}
        period = {"period_from": start.isoformat(), "period_to": end.isoformat(), "period_days": 7}

        turnover_coverage = (
            {
                "dates": [(start + timedelta(days=offset)).isoformat() for offset in range(7)],
                "days": 7,
                "expected_days": 7,
                "complete": True,
                "period_from": start.isoformat(),
                "period_to": end.isoformat(),
                "missing_dates": [],
            }
            if closed_known
            else None
        )
        for product in catalog:
            sku = str(product["article"])
            if article and sku != article:
                continue
            source_stock = stocks.get(sku) or {}
            fbs = max(int(source_stock.get("fbs_stock") or 0), 0)
            fbo = max(int(source_stock.get("fbo_stock") or 0), 0)
            ff = max(int(source_stock.get("ff_available") or 0), 0)
            total = fbs + fbo + ff
            rows = by_article[sku]
            stock_orders = sum(int(row.get("orders_count") or 0) for row in rows)
            stock = {
                "total": total,
                "fbs": fbs,
                "fbo": fbo,
                "fulfillment": ff,
                "days": unit_economics_1c.calculate_stock_coverage_days(total, stock_orders)
                if orders_known
                else None,
                "orders_21d": stock_orders if orders_known else None,
                "average_daily_orders": round(stock_orders / 21, 2) if orders_known else None,
                "period_days": 21,
                "period_from": stock_start.isoformat(),
                "period_to": today.isoformat(),
                "state": None,
            }
            closed_rows = [row for row in rows if start.isoformat() <= row["day"] <= end.isoformat()]
            order_amount = round(sum(float(row.get("orders_amount") or 0) for row in closed_rows), 2)
            cancel_amount = round(sum(float(row.get("cancel_amount") or 0) for row in closed_rows), 2)
            sold = sum(int(row.get("sold_count") or 0) for row in closed_rows)
            cancelled = sum(int(row.get("cancel_count") or 0) for row in closed_rows)
            buyout_percent = round(sold / (sold + cancelled) * 100, 2) if sold + cancelled else 0.0
            economics = {
                "turnover": round(order_amount - cancel_amount, 2) if closed_known else None,
                "turnover_coverage": turnover_coverage,
                "margin": None,
                "roi": None,
                **period,
            }
            ad = {
                **dict.fromkeys(("drr", "spend", "ctr", "cpc")),
                **period,
                "orders_amount": order_amount if closed_known else None,
                "buyout_percent": buyout_percent if closed_known else None,
            }
            if ads_known:
                values = ads.get(sku) or {}
                spend = float(values.get("spend") or 0)
                impressions = int(values.get("impressions") or 0)
                clicks = int(values.get("clicks") or 0)
                ad.update(
                    {
                        "spend": spend,
                        "impressions": impressions,
                        "clicks": clicks,
                        "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
                        "cpc": round(spend / clicks, 2) if clicks else 0.0,
                        "drr": unit_economics_1c.calculate_drr_percent(spend, order_amount, buyout_percent)
                        if closed_known
                        else None,
                    }
                )
            products.append(
                catalog_product(
                    slug,
                    product,
                    stock=stock,
                    economics=economics,
                    advertising=ad,
                    reputation=reputation.get(sku) or reputation.get(str(product.get("mp_sku") or "")),
                    is_new=product_statuses[sku]["status"] == "new" if sku in product_statuses else None,
                    source_values=source_values.get(sku),
                )
            )
    return products
