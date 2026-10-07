"""WB analyzer: the complete permitted catalog, with dated, nullable metrics.

This module only reads saved sources. Opening the analyzer never starts API jobs.
"""

import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from app import db
from app.access.access_control import restricts_unit_economics_to_manager
from app.access.sections import has_access
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.identity import SectionAccessLevel, SectionName, coerce_user
from app.dto.unit_economics_1c import UnitEconomics1CProductSettings
from app.economics.completeness import aggregate_days
from app.economics.wb import calculations, history
from app.repositories import analyzer as repository
from app.repositories.economics_coverage import wb_days


def permitted(manager, user) -> bool:
    user = coerce_user(user)
    if user is None:
        return False
    if not restricts_unit_economics_to_manager(user):
        return True

    def identity(value):
        return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", str(value or "").casefold().replace("ё", "е")).split())

    return identity(manager) == identity(user.full_name)


def index(rows, article="article"):
    return {(str(row["store_slug"]), str(row[article])): row for row in rows}


def daily_index(rows, article="article"):
    result = defaultdict(dict)
    for row in rows:
        result[(str(row["store_slug"]), str(row[article]))][str(row["day"])] = row
    return result


def total(values):
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def ratio(numerator, denominator):
    return round(numerator / denominator * 100, 2) if numerator is not None and denominator else None


def load(stores: tuple[str, ...], week: date, user, *, today: date | None = None) -> dict:
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    week = week - timedelta(days=week.weekday())
    days = [(week + timedelta(days=i)).isoformat() for i in range(7)]
    anchor = min(today, week + timedelta(days=6))
    traffic_days = [(anchor - timedelta(days=i)).isoformat() for i in range(3, -1, -1)]
    previous = [(week - timedelta(days=i)).isoformat() for i in range(7, 0, -1)]
    stock_week = today - timedelta(days=today.weekday() + 7)
    stock_days = [(stock_week + timedelta(days=i)).isoformat() for i in range(7)]
    period = [day for day in days if day <= today.isoformat()]
    start, end = previous[0], anchor.isoformat()
    references = index(db.get_unit_economics_1c_product_reference_rows(stores))
    prices = index(db.get_unit_economics_1c_latest_daily_prices(stores))
    reputations = index(db.get_unit_economics_1c_latest_product_reputation(stores))
    orders = daily_index(db.get_unit_economics_1c_funnel_daily_order_rows(stores, start, end))
    advertising = daily_index(db.get_unit_economics_1c_daily_advertising(stores, start, end), "nm_id")
    rnp = daily_index(repository.daily_metrics(stores, start, end))
    snapshots = daily_index(
        db.get_unit_economics_1c_daily_margin_snapshots(
            stores, min(week, anchor - timedelta(days=1)).isoformat(), end, inputs_only=True
        )
    )
    coverage = wb_days(stores, start, end)
    # Stock is current, and turnover is today's saved funnel value even when
    # the user inspects an older week. Do not read all intervening weeks.
    if end < today.isoformat():
        current_orders = daily_index(
            db.get_unit_economics_1c_funnel_daily_order_rows(stores, stock_days[0], today.isoformat())
        )
        for key, values in current_orders.items():
            orders[key].update(values)
        current_coverage = wb_days(stores, stock_days[0], today.isoformat())
        for store in stores:
            coverage[store]["orders"].update(current_coverage[store]["orders"])
    cabinets = {row.store_slug: row for row in db.list_unit_economics_1c_cabinet_settings(stores)}
    settings = {
        (row.store_slug, row.article): row for row in db.list_unit_economics_1c_product_settings(stores)
    }
    current_metrics = (
        calculations.load_product_metrics(stores, period_days=1, today=today)
        if today.isoformat() in period
        else {}
    )
    work = defaultdict(lambda: defaultdict(list))
    can_edit = has_access(user, SectionName.ANALYZER, SectionAccessLevel.WRITE)
    for row in repository.notes(stores, days[0], today.isoformat()):
        row["can_edit"] = can_edit and row["action_date"] == today.isoformat()
        work[(row["store_slug"], row["article"])][row["action_date"]].append(row)

    rows = []
    for store in stores:
        for product in db.get_stock_items(store, "WB"):
            article = str(product["article"])
            key = (store, article)
            metric_key = (store, article.partition(" / ")[0].strip())
            reference = references.get(key, {})
            if not permitted(reference.get("manager"), user):
                continue
            price = prices.get(key, {})
            product_orders, product_ads = orders[metric_key], advertising[metric_key]
            product_rnp = rnp.get(key) or rnp[metric_key]
            source = coverage[store]

            def order_value(
                day, field, product_orders=product_orders, source=source, product_rnp=product_rnp
            ):
                if day > today.isoformat():
                    return None
                row = product_orders.get(day)
                if row is not None:
                    return row.get(field)
                if day in source["orders"]:
                    return 0
                if field == "orders_count":
                    return product_rnp.get(day, {}).get("traffic_orders")
                return None

            def ad_value(day, field, product_ads=product_ads, source=source, product_rnp=product_rnp):
                if day > today.isoformat():
                    return None
                row = product_ads.get(day)
                if row is not None and row.get(field) is not None:
                    return row[field]
                if day in source["advertising"]:
                    return 0
                return product_rnp.get(day, {}).get(
                    {"impressions": "ad_impressions", "clicks": "ad_clicks", "spend": "ad_spend"}[field]
                )

            product_snapshots = snapshots[key]
            if today.isoformat() in period and today.isoformat() not in product_snapshots:
                cabinet = cabinets[store]
                product_snapshots[today.isoformat()] = history.calculate_snapshot_row(
                    snapshot_day=today,
                    store_slug=store,
                    article=article,
                    price_snapshot=price,
                    product_metrics=current_metrics.get(metric_key)
                    or calculations.empty_product_metrics(period_days=1, today=today),
                    product_settings=settings.get(key)
                    or UnitEconomics1CProductSettings(store_slug=store, article=article),
                    product_reference=reference,
                    cabinet=cabinet,
                    captured_at=datetime.now(UTC).isoformat(),
                )
            daily_economics = [
                history.report_day(
                    day,
                    product_snapshots.get(day),
                    product_orders.get(day, {}),
                    ad_value(day, "spend"),
                    orders_known=day in source["orders"] or day in product_orders,
                    ads_known=ad_value(day, "spend") is not None,
                    ignore_deferred_cost_warnings=True,
                )
                for day in period
            ]
            economics = aggregate_days(daily_economics, period, calculations.money)

            def drr_parts(
                selected_days,
                product_snapshots=product_snapshots,
                product_orders=product_orders,
                ad_value=ad_value,
                order_value=order_value,
            ):
                # Same expected-buyout denominator as WB economics, on matching days only.
                pairs = []
                for day in selected_days:
                    spend, amount = ad_value(day, "spend"), order_value(day, "orders_amount")
                    buyout = history.snapshot_buyout_percent(product_snapshots.get(day))
                    if buyout is None:
                        buyout = product_orders.get(day, {}).get("buyout_percent")
                    if spend is not None and amount is not None and buyout is not None:
                        pairs.append((spend, amount * buyout / 100))
                return total(p[0] for p in pairs), total(p[1] for p in pairs), len(pairs)

            spend, bought_amount, drr_days = drr_parts(period)
            yesterday = (anchor - timedelta(days=1)).isoformat()
            yesterday_spend, yesterday_amount, _ = drr_parts([yesterday])
            previous_pairs = [
                (order_value(day, "orders_count"), order_value(day, "orders_amount")) for day in previous
            ]
            previous_pairs = [
                (count, amount)
                for count, amount in previous_pairs
                if count is not None and amount is not None
            ]
            previous_count = total(count for count, _ in previous_pairs)
            previous_amount = total(amount for _, amount in previous_pairs)
            previous_price = round(previous_amount / previous_count, 2) if previous_count else None
            ad_pairs = [(ad_value(day, "impressions"), ad_value(day, "clicks")) for day in period]
            ad_pairs = [
                (impressions, clicks)
                for impressions, clicks in ad_pairs
                if impressions is not None and clicks is not None
            ]
            impressions, clicks = total(p[0] for p in ad_pairs), total(p[1] for p in ad_pairs)
            retail, customer = price.get("retail_price"), price.get("customer_price_with_spp")
            goal, day_goal = reference.get("goal_week"), reference.get("goal_day")
            if day_goal is None and goal is not None:
                day_goal = goal / 7
            stock_values = [product.get(field) for field in ("fbo_stock", "fbs_stock", "ff_available")]
            stock = total(stock_values)
            stock_orders = [
                product_orders[day].get("orders_count")
                if day in product_orders
                else 0
                if day in source["orders"]
                else None
                for day in stock_days
            ]
            stock_orders_complete = all(count is not None for count in stock_orders)
            stock_daily_orders = sum(stock_orders) / 7 if stock_orders_complete else None
            fact = (
                product_orders[today.isoformat()].get("orders_amount")
                if today.isoformat() in product_orders
                else 0
                if today.isoformat() in source["orders"]
                else None
            )
            plan = (
                round(day_goal * previous_price, 2)
                if day_goal is not None and previous_price is not None
                else None
            )
            forecast = fact  # D is fixed to 1 until its calculation is specified.
            difference = round(plan - forecast, 2) if plan is not None and forecast is not None else None
            row = {
                "id": f"{store}:{article}",
                "store_slug": store,
                "project": STORES[store]["name"],
                "article": article,
                "barcode": product.get("barcode") or "",
                "barcodes": product.get("barcodes", []),
                "name": product.get("name") or article,
                "image": product.get("image_url") or "",
                "manager": reference.get("manager") or "",
                "code": reference.get("abc_code") or "",
                "stock": stock,
                "stockPartial": stock is not None and any(v is None for v in stock_values),
                "stockDays": round(stock / stock_daily_orders, 1)
                if stock is not None and stock_daily_orders
                else None,
                "stockDailyOrders": stock_daily_orders,
                "stockOrdersComplete": stock_orders_complete,
                "stockStatus": reference.get("stock_status"),
                "stockEnds": reference.get("stock_end_week"),
                "roi": economics.get("roi"),
                "drr": ratio(spend, bought_amount) if bought_amount else 0.0 if spend == 0 else None,
                "previousPrice": previous_price,
                "impressions": [ad_value(day, "impressions") for day in traffic_days],
                "clicks": [ad_value(day, "clicks") for day in traffic_days],
                "ctr": [ratio(ad_value(day, "clicks"), ad_value(day, "impressions")) for day in traffic_days],
                "carts": [product_rnp.get(day, {}).get("traffic_carts") for day in traffic_days],
                "spend": ad_value(yesterday, "spend"),
                "yesterdayDrr": ratio(yesterday_spend, yesterday_amount)
                if yesterday_amount
                else 0.0
                if yesterday_spend == 0
                else None,
                "totalCtr": ratio(clicks, impressions),
                "rating": reputations.get(metric_key, {}).get("rating"),
                "week": [order_value(day, "orders_count") for day in days],
                "goal": goal,
                "dayGoal": day_goal,
                "spp": ratio(retail - customer, retail) if retail and customer is not None else None,
                "wallet": price.get("customer_price_with_wallet"),
                "price": retail,
                "coeff": 1,
                "fact": fact,
                "forecast": forecast,
                "difference": difference,
                "deviation": ratio(difference, plan),
                "plan": plan,
                "notes": [work[key][day] for day in days],
                "noteHistory": [note for entries in work[key].values() for note in entries],
                "updated": {
                    "prices": price.get("updated_at") or price.get("day"),
                    "reference": reference.get("source_synced_at"),
                    "stock": product.get("mp_updated_at"),
                    "orders": max(
                        (str(r.get("updated_at") or "") for r in product_orders.values()), default=""
                    ),
                    "advertising": max(
                        (str(r.get("synced_at") or "") for r in product_ads.values()), default=""
                    ),
                },
                "coverage": {
                    "orders": sum(order_value(day, "orders_count") is not None for day in period),
                    "advertising": sum(ad_value(day, "spend") is not None for day in period),
                    "expected": len(period),
                    "drr": drr_days,
                    "roiComplete": economics.get("complete", False),
                    "roiMessages": economics.get("messages", []),
                },
                "weights": {
                    "spend": spend,
                    "boughtAmount": bought_amount,
                    "yesterdaySpend": yesterday_spend,
                    "yesterdayAmount": yesterday_amount,
                    "impressions": impressions,
                    "clicks": clicks,
                    "profit": economics.get("margin"),
                    "purchase": economics.get("roi_purchase_value"),
                    "previousCount": previous_count,
                    "previousAmount": previous_amount,
                },
            }
            rows.append(row)
    rows.sort(key=lambda row: (row["project"], row["name"].casefold(), row["article"]))
    return {
        "ok": True,
        "rows": rows,
        "week": days,
        "traffic_days": traffic_days,
        "today": today.isoformat(),
        "anchor": anchor.isoformat(),
        "period_from": days[0],
        "period_to": end,
        "previous_from": previous[0],
        "previous_to": previous[-1],
        "stockPreviousFrom": stock_days[0],
        "stockPreviousTo": stock_days[-1],
        "turnover_day": today.isoformat(),
        "loaded_at": datetime.now(UTC).isoformat(),
    }
