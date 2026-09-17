"""Yandex metrics in the shared layout, isolated from the WB economics pipeline."""

from collections import defaultdict
from datetime import date, datetime, timedelta

from app import db
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.economics.wb import calculations as unit_economics_1c
from app.exports import stock_sheet_inbound
from app.repositories import unit_economics_yandex as repository
from app.repositories import (
    yandex_assortment,
    yandex_product_statuses,
    yandex_source_values,
    yandex_storefront,
)

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
    prices: dict | None = None,
) -> dict:
    store = STORES[store_slug]
    article = str(product["article"])
    source_values = source_values or {}
    prices = prices or {}
    pricing = yandex_storefront.resolved_prices(prices)
    seller_price, buyer_price = pricing["seller_price"], pricing["buyer_price"]
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
        "price": {"current": seller_price, "with_spp": buyer_price, "with_wallet": pricing["pay_price"]},
        "price_check": {
            **pricing,
            "status": prices.get("status", "pending"),
            "checked_at": prices.get("checked_at"),
            "price_checked_at": prices.get("price_checked_at"),
            "last_known_buyer_price": prices.get("buyer_price"),
            "is_stale": prices.get("buyer_price") is not None and buyer_price is None,
            "message": prices.get("message"),
        },
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
            "retail_price": seller_price,
            "customer_price": buyer_price,
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
    store_slugs: tuple[str, ...],
    *,
    article: str = "",
    today: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    end = date_to or today - timedelta(days=1)
    start = date_from or end - timedelta(days=6)
    if start > end or end >= today or (end - start).days >= 366:
        raise ValueError("Некорректный период юнит-экономики")
    period_days = (end - start).days + 1
    stock_start = today - timedelta(days=unit_economics_1c.STOCK_COVERAGE_PERIOD_DAYS - 1)
    history_start = min(start, stock_start)
    products = []
    for slug in store_slugs:
        active = yandex_assortment.active_articles(slug)
        catalog = [row for row in db.get_catalog_items(slug, MARKETPLACE) if row["article"] in active]
        inbound = stock_sheet_inbound.load(slug, MARKETPLACE, catalog, include_yandex_approved=True)
        archived = yandex_assortment.archived_articles(slug)
        catalog = [row for row in inbound.catalog if row["article"] not in archived]
        stocks = {row["article"]: row for row in db.get_stock_items(slug, MARKETPLACE, ("fbs", "fbo"))}
        snapshots = repository.get_snapshots(slug)
        product_statuses = yandex_product_statuses.get_statuses(slug)
        source_values = yandex_source_values.get_values(slug)
        prices = yandex_storefront.get_prices(slug)
        orders_snapshot = snapshots.get("orders") or {}
        daily_orders, loaded_orders = repository.get_history(
            slug, "orders", history_start.isoformat(), today.isoformat()
        )
        legacy_orders = repository.get_daily_orders(
            slug, history_start.isoformat(), (today + timedelta(days=1)).isoformat()
        )
        sync_states = db.get_sales_sync_states(MARKETPLACE, slug)
        cached = {
            (row["article"], row["day"]): row
            for row in legacy_orders
            if row["day"] not in loaded_orders
            and not (
                orders_snapshot.get("data") is not None
                and orders_snapshot["period_from"] <= row["day"] <= orders_snapshot["period_to"]
            )
        }
        cached.update(
            {
                (row["article"], row["day"]): row
                for row in orders_snapshot.get("data") or []
                if row["day"] not in loaded_orders
            }
        )
        cached.update({(row["article"], row["day"]): row for row in daily_orders})
        orders = list(cached.values())
        loaded_orders |= {
            day
            for day in repository.days_between(history_start.isoformat(), today.isoformat())
            if _orders_cover(orders_snapshot, sync_states, date.fromisoformat(day), date.fromisoformat(day))
        }
        orders_known = (
            set(repository.days_between(stock_start.isoformat(), today.isoformat())) <= loaded_orders
        )
        turnover_coverage = _coverage(loaded_orders, start, end)
        by_article = defaultdict(list)
        for row in orders:
            if history_start.isoformat() <= row["day"] <= today.isoformat():
                by_article[row["article"]].append(row)
        reputation = {str(row["sku"]): row for row in (snapshots.get("reputation") or {}).get("data") or []}
        ads_snapshot = snapshots.get("advertising") or {}
        daily_ads, loaded_ads = repository.get_history(
            slug, "advertising", start.isoformat(), end.isoformat()
        )
        ads_coverage = _coverage(loaded_ads, start, end)
        legacy_ads = (
            not ads_coverage["complete"]
            and _covers(ads_snapshot, start, end)
            and (
                ads_snapshot["period_from"] == start.isoformat()
                and ads_snapshot["period_to"] == end.isoformat()
            )
            and not any("day" in row for row in ads_snapshot.get("data") or [])
        )
        if legacy_ads:
            daily_ads = ads_snapshot["data"]
            ads_coverage = _coverage(
                set(repository.days_between(start.isoformat(), end.isoformat())), start, end
            )
        matching_days = loaded_orders & (set(ads_coverage["dates"]) if legacy_ads else loaded_ads)
        # Aggregated legacy advertising cannot be apportioned over missing order days.
        if legacy_ads and not turnover_coverage["complete"]:
            matching_days = set()
        drr_coverage = _coverage(matching_days, start, end)
        drr_spend = defaultdict(float)
        ads = defaultdict(lambda: {"spend": 0.0, "impressions": 0, "clicks": 0})
        for row in daily_ads:
            if (legacy_ads and drr_coverage["complete"]) or row.get("day") in matching_days:
                drr_spend[row["article"]] += float(row.get("spend") or 0)
            for key in ("spend", "impressions", "clicks"):
                ads[row["article"]][key] += row.get(key) or 0
        buyout_settings = repository.get_buyout_settings(slug)
        buyout_snapshot = snapshots.get("buyout") or {}
        buyout_days = int(buyout_settings["buyout_period_days"])
        buyout_available = (
            buyout_snapshot.get("data") is not None
            and (
                date.fromisoformat(buyout_snapshot["period_to"])
                - date.fromisoformat(buyout_snapshot["period_from"])
            ).days
            + 1
            == buyout_days
        )
        buyouts = (
            {row["article"]: row for row in buyout_snapshot.get("data") or []} if buyout_available else {}
        )
        period = {"period_from": start.isoformat(), "period_to": end.isoformat(), "period_days": period_days}
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
            stock_orders = sum(
                int(row.get("orders_count") or 0) for row in rows if row["day"] >= stock_start.isoformat()
            )
            inbound_quantity = inbound.quantities.get(sku)
            inbound_partial = inbound_quantity is None
            if inbound_partial:
                inbound_quantity = inbound.confirmed_quantities.get(sku) or None
            inbound_message = "Утверждённые заявки и ещё не принятые товары в отправленных поставках."
            if not inbound.available:
                inbound_message = "Нет полного свежего снимка поставок: количество в пути не подтверждено."
            elif inbound_partial:
                inbound_message = (
                    f"Не менее {inbound_quantity} шт. По части поставок количество ещё не принятых товаров неизвестно."
                    if inbound_quantity is not None
                    else "Количество ещё не принятых товаров в поставках не подтверждено."
                )
            stock = {
                "total": total,
                "fbs": fbs,
                "fbo": fbo,
                "fulfillment": ff,
                "inbound": inbound_quantity,
                "inbound_partial": inbound_partial,
                "inbound_message": inbound_message,
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
            if not article and not (
                total > 0 or (stock["inbound"] or 0) > 0 or order_amount - cancel_amount != 0
            ):
                continue
            drr_amount = round(
                sum(
                    float(row.get("orders_amount") or 0) for row in closed_rows if row["day"] in matching_days
                ),
                2,
            )
            raw_buyout = (buyouts.get(sku) or {}).get("buyout_percent", 0.0) if buyout_available else None
            buyout_percent = unit_economics_1c.resolve_buyout_percent(
                raw_buyout, buyout_settings["default_buyout_percent"]
            )
            economics = {
                "turnover": round(order_amount - cancel_amount, 2) if turnover_coverage["days"] else None,
                "turnover_coverage": turnover_coverage if turnover_coverage["days"] else None,
                "margin": None,
                "roi": None,
                **period,
            }
            ad = {
                **dict.fromkeys(("drr", "spend", "ctr", "cpc")),
                **period,
                "orders_amount": drr_amount if drr_coverage["days"] else None,
                "drr_spend": round(drr_spend[sku], 2) if drr_coverage["days"] else None,
                "drr_coverage": drr_coverage,
                "buyout_percent": buyout_percent,
                "raw_buyout_percent": raw_buyout,
                "buyout_default_applied": not raw_buyout and buyout_percent > 0,
                "buyout_period_from": buyout_snapshot.get("period_from") if buyout_available else None,
                "buyout_period_to": buyout_snapshot.get("period_to") if buyout_available else None,
                "coverage": ads_coverage,
            }
            if ads_coverage["days"]:
                values = ads.get(sku) or {}
                spend = float(values.get("spend") or 0)
                impressions = int(values.get("impressions") or 0)
                clicks = int(values.get("clicks") or 0)
                ad.update(
                    {
                        "spend": round(spend, 2),
                        "impressions": impressions,
                        "clicks": clicks,
                        "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
                        "cpc": round(spend / clicks, 2) if clicks else 0.0,
                        "drr": unit_economics_1c.calculate_drr_percent(
                            drr_spend[sku], drr_amount, buyout_percent
                        )
                        if drr_coverage["days"]
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
                    prices=prices.get(sku),
                )
            )
            products[-1]["details"]["buyout_percent"] = buyout_percent
            products[-1]["details"]["drr"] = ad["drr"]
    from app.yandex.economics import attach

    return attach(products, start, end, today)


def _coverage(loaded: set[str], start: date, end: date) -> dict:
    expected = repository.days_between(start.isoformat(), end.isoformat())
    dates = [day for day in expected if day in loaded]
    return {
        "dates": dates,
        "days": len(dates),
        "expected_days": len(expected),
        "complete": len(dates) == len(expected),
        "period_from": start.isoformat(),
        "period_to": end.isoformat(),
        "missing_dates": [day for day in expected if day not in loaded],
    }
