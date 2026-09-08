"""Yandex metrics in the shared layout, isolated from the WB economics pipeline."""

from collections import defaultdict
from datetime import date, datetime, timedelta

from app import db, unit_economics_1c
from app.domain import MOSCOW_TIMEZONE
from app.repositories import unit_economics_yandex as repository
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
) -> dict:
    store = STORES[store_slug]
    article = str(product["article"])
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
        "is_new": None,
        "sales_days": None,
        "price": dict.fromkeys(("current", "with_spp", "with_wallet")),
        "current_economics": dict.fromkeys(
            ("margin", "roi", "orders", "buyout_percent", "advertising_spend", "period_to")
        ),
        "economics_7d": economics or dict.fromkeys(("turnover", "margin", "roi")),
        "advertising": advertising
        or dict.fromkeys(("drr", "spend", "ctr", "cpc", "orders_amount", "period_from", "period_to")),
        "tag_data": dict.fromkeys(("goal_week", "goal_day", "status", "ends", "code", "fact", "plan")),
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
        "details": dict.fromkeys(
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
        "history": None,
    }


def _covers(snapshot: dict, start: date, end: date) -> bool:
    return (
        snapshot.get("data") is not None
        and str(snapshot.get("period_from") or "9999") <= start.isoformat()
        and str(snapshot.get("period_to") or "") >= end.isoformat()
    )


def load_products(
    store_slugs: tuple[str, ...], *, article: str = "", today: date | None = None
) -> list[dict]:
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    start, end = today - timedelta(days=7), today - timedelta(days=1)
    stock_start = today - timedelta(days=unit_economics_1c.STOCK_COVERAGE_PERIOD_DAYS - 1)
    products = []
    for slug in store_slugs:
        catalog = db.get_catalog_items(slug, MARKETPLACE)
        stocks = {row["article"]: row for row in db.get_stock_items(slug, MARKETPLACE, ("fbs", "fbo"))}
        snapshots = repository.get_snapshots(slug)
        orders_snapshot = snapshots.get("orders") or {}
        if _covers(orders_snapshot, stock_start, today):
            orders = orders_snapshot["data"]
            orders_known = True
        else:
            orders = repository.get_daily_orders(
                slug, stock_start.isoformat(), (today + timedelta(days=1)).isoformat()
            )
            sync_states = db.get_sales_sync_states(MARKETPLACE, slug)
            orders_known = any(
                row.get("last_success_at")
                and int(row.get("lookback_days") or 0) >= 21
                and str(row["last_success_at"])[:10] >= today.isoformat()
                for row in sync_states
            )
            if _covers(orders_snapshot, start, end):
                cached = {(row["article"], row["day"]): row for row in orders_snapshot["data"]}
                cached.update(
                    {
                        (row["article"], row["day"]): row
                        for row in orders
                        if row["day"] > orders_snapshot["period_to"]
                    }
                )
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
            stock_known = orders_known or bool(rows)
            stock = {
                "total": total,
                "fbs": fbs,
                "fbo": fbo,
                "fulfillment": ff,
                "days": unit_economics_1c.calculate_stock_coverage_days(total, stock_orders)
                if stock_known
                else None,
                "orders_21d": stock_orders if stock_known else None,
                "average_daily_orders": round(stock_orders / 21, 2) if stock_known else None,
                "period_days": 21,
                "period_from": stock_start.isoformat(),
                "period_to": today.isoformat(),
                "state": None,
            }
            closed_rows = [row for row in rows if start.isoformat() <= row["day"] <= end.isoformat()]
            closed_known = orders_known or _covers(orders_snapshot, start, end) or bool(closed_rows)
            order_amount = round(sum(float(row.get("orders_amount") or 0) for row in closed_rows), 2)
            cancel_amount = round(sum(float(row.get("cancel_amount") or 0) for row in closed_rows), 2)
            sold = sum(int(row.get("sold_count") or 0) for row in closed_rows)
            cancelled = sum(int(row.get("cancel_count") or 0) for row in closed_rows)
            buyout_percent = round(sold / (sold + cancelled) * 100, 2) if sold + cancelled else 0.0
            economics = {
                "turnover": round(order_amount - cancel_amount, 2) if closed_known else None,
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
                )
            )
    return products
