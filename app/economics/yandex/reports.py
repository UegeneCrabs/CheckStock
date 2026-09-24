"""YM report rows from local daily records; reads never backfill history."""

from collections import defaultdict
from datetime import datetime

from app import db
from app.access.access_control import restricts_unit_economics_to_manager
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.economics.report_sources import ReportSources
from app.economics.yandex.calculations import MARKETPLACE, load_products
from app.repositories import unit_economics_yandex as metrics
from app.repositories import yandex_assortment, yandex_source_values
from app.repositories import yandex_economics as repository
from app.repositories.stock_history import get_products_with_stock_history
from app.yandex import economics
from app.yandex import economics_shared as shared
from app.yandex.economics_calculation import VERSION, aggregate, calculate, daily_profit


def manager_matches(manager, user):
    import re

    def key(value):
        return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", str(value or "").casefold().replace("ё", "е")).split())

    return user is not None and key(manager) == key(user.full_name)


def permitted(manager, user):
    return user is not None and (
        not restricts_unit_economics_to_manager(user) or manager_matches(manager, user)
    )


def catalog(stores, user, *, sources=None):
    """The same active assortment and manager scope as YM economics."""
    sources = sources or ReportSources()
    rows = []
    for store in stores:
        active = sources.read(yandex_assortment.active_articles, store)
        source = sources.read(yandex_source_values.get_values, store)
        saved = sources.read(repository.sources, store)
        for product in sources.read(db.get_catalog_items, store, MARKETPLACE):
            article = str(product["article"])
            reference = source.get(article, {})
            if article not in active or not permitted(reference.get("manager"), user):
                continue
            category = saved.get((article, "category"), {}).get("values", {})
            fallback = saved.get((article, "catalog"), {}).get("values", {})
            rows.append(
                {
                    **product,
                    "store_slug": store,
                    "store_name": STORES[store]["name"],
                    "manager": reference.get("manager") or None,
                    "subject": category.get("category_name")
                    or fallback.get("category_name")
                    or "Без предмета",
                    "code": str(reference.get("abc_code") or "").strip().upper(),
                }
            )
    return rows


def filter_options(rows, user):
    return {
        "filters": {
            "subjects": sorted({row["subject"] for row in rows}),
            "managers": sorted({row["manager"] for row in rows if row.get("manager")}),
            "articles": [
                {key: row.get(key) for key in ("article", "name", "store_slug", "store_name", "image_url")}
                for row in sorted(
                    rows, key=lambda row: (str(row.get("name") or "").casefold(), row["article"])
                )
            ],
        },
        "manager_scope": {"restricted": restricts_unit_economics_to_manager(user), "matched": bool(rows)},
    }


def daily_row(day, saved, order, ad, orders_known, ads_known, fallback_buyout):
    """Expose original inputs and the version of each closed day's calculation."""
    values = saved.get("inputs") or {}
    buyout = values.get("buyout_percent", fallback_buyout)
    count = (
        saved.get("orders_count") if saved else int(order.get("orders_count") or 0) if orders_known else None
    )
    spend = saved.get("advertising_spend") if saved else float(ad.get("spend") or 0) if ads_known else None
    bought = (
        saved.get("expected_buyouts")
        if saved
        else count * buyout / 100
        if count is not None and buyout is not None
        else None
    )
    result = (
        calculate(
            {**values, "advertising_mode": "actual"},
            advertising_spend=spend,
            orders_count=count,
            version=saved.get("calculation_version", VERSION),
        )
        if values
        else {}
    )
    costs = result.get("costs", {})
    row = {
        "date": day,
        "available": saved.get("profit") is not None,
        "snapshot_available": bool(values),
        "orders_count": count,
        "net_orders_count": max(count - int(order.get("cancel_count") or 0), 0)
        if count is not None
        else None,
        "advertising_spend": spend,
        "buyout_percent": buyout,
        "expected_buyouts": bought,
        "advertising_per_unit": round(spend / bought, 2)
        if bought and spend is not None
        else 0.0
        if spend == 0
        else None,
        "net_profit": result.get("margin"),
        "day_profit": saved.get("profit"),
        "day_purchase_value": saved.get("purchase_value"),
        "net_revenue": None
        if result.get("margin") is None
        else round(result["margin"] + (values.get("purchase_price") or 0), 2),
        "logistics": result.get("logistics", {}).get("total"),
        "vat_value": costs.get("vat"),
        "usn_value": costs.get("usn"),
        "calculation_version": saved.get("calculation_version"),
        "payment_acceptance": values.get("payment_acceptance"),
        "acquiring_value": costs.get("acquiring"),
        "commission_value": costs.get("commission"),
        "team_commission_value": costs.get("company_commission"),
        "fulfillment_expense": costs.get("fulfillment", 0.0 if costs else None),
        "total_cost": result.get("total_cost"),
        "loss": costs.get("loss"),
        "disposal": costs.get("disposal"),
    }
    for key, source in {
        "customer_price": "buyer_price",
        "retail_price": "seller_price",
        "pay_price": "pay_price",
        "team_commission_percent": "company_commission_percent",
        **{
            key: key
            for key in (
                "vat_percent",
                "usn_percent",
                "acquiring_percent",
                "commission_percent",
                "fulfillment_cost",
                "purchase_price",
                "loss_percent",
                "disposal_cost",
            )
        },
    }.items():
        row[key] = values.get(source)
    return row


def load_rows(
    stores,
    user,
    start,
    end,
    *,
    today=None,
    article="",
    include_details=False,
    selected_keys=None,
    for_target_price=False,
    sources=None,
    filters=None,
    collected=None,
):
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    sources = sources or ReportSources()
    filters = filters or {}
    identities = {(row["store_slug"], row["article"]): row for row in catalog(stores, user, sources=sources)}
    products = load_products(
        stores,
        date_from=start,
        date_to=end,
        today=today,
        include_economics=False,
        article=article,
        include_inactive=True,
        allow_current_day=True,
        sources=sources,
    )
    days = metrics.days_between(start.isoformat(), end.isoformat())
    stocked_in_period = (
        get_products_with_stock_history(stores, MARKETPLACE, days[0], days[-1])
        if not for_target_price
        else set()
    )
    caches = {}
    for store in stores:
        orders, order_days = sources.read(metrics.get_history, store, "orders", days[0], days[-1])
        ads, ad_days = sources.read(metrics.get_history, store, "advertising", days[0], days[-1])
        by_order, by_ad = defaultdict(dict), defaultdict(dict)
        for row in orders:
            by_order[row["article"]][row["day"]] = row
        for row in ads:
            by_ad[row["article"]][row["day"]] = row
        by_history = defaultdict(list)
        for row in repository.history(store, days[0], days[-1]):
            by_history[row["article"]].append(row)
        caches[store] = (
            economics.context(store, sources=sources),
            by_history,
            by_order,
            order_days,
            by_ad,
            ad_days,
        )
    rows = []
    for product in products:
        store, article = product["store_slug"], product["article"]
        identity = identities.get((store, article))
        if identity is None or (selected_keys is not None and (store, article) not in selected_keys):
            continue
        context, history, orders, order_days, ads, ad_days = caches[store]
        stocked = any((product["stock"].get(key) or 0) > 0 for key in ("fbs", "fbo", "fulfillment"))
        if for_target_price:
            turnover = sum(float(row.get("orders_amount") or 0) for row in orders[article].values())
            if not (stocked or (product["stock"].get("inbound") or 0) > 0 or turnover > 0):
                continue
        else:
            activity = [
                *orders[article].values(),
                *ads[article].values(),
                *(row["data"] for row in history[article]),
            ]
            has_orders_or_ads = any(
                (row.get("orders_count") or 0) > 0
                or (row.get("spend") or row.get("advertising_spend") or 0) > 0
                for row in activity
            )
            has_live_stock = start <= today <= end and stocked
            if not (has_orders_or_ads or has_live_stock or (store, article) in stocked_in_period):
                continue
        if collected is not None:
            collected.setdefault("eligible", []).append(identity)
        if any(
            filters.get(key) and str(identity.get(field) or "").casefold() not in filters[key]
            for key, field in (("subjects", "subject"), ("managers", "manager"), ("articles", "article"))
        ):
            continue
        state = economics.effective(store, article, "FBY", state_cache=context, estimate_tariff=True)
        if collected is not None and for_target_price:
            collected.setdefault("states", {})[(store, article)] = state
        values = state["values"]
        q = values.get("buyout_percent")
        saved = {row["day"]: row for row in shared.history(history[article], article)}
        if today.isoformat() in days:
            key = today.isoformat()
            live = economics.current_inputs(store, article, "FBY", today=today, state_cache=context)
            inputs = live["values"]
            if key in order_days and key in ad_days:
                count = int(orders[article].get(key, {}).get("orders_count") or 0)
                spend = float(ads[article].get(key, {}).get("spend") or 0)
                unit = calculate(inputs, without_advertising=True)
                bought = (
                    count * inputs["buyout_percent"] / 100
                    if inputs.get("buyout_percent") is not None
                    else None
                )
                saved[key] = {
                    "day": key,
                    "inputs": inputs,
                    "orders_count": count,
                    "advertising_spend": spend,
                    "expected_buyouts": bought,
                    "calculation_version": VERSION,
                    "profit": daily_profit(inputs, count, spend, unit, VERSION),
                    "purchase_value": round(inputs["purchase_price"] * bought, 2)
                    if inputs.get("purchase_price") is not None and bought is not None
                    else None,
                }
        period = aggregate(list(saved.values()), days)
        detail_context = (days, saved, orders[article], ads[article], order_days, ad_days, q)
        if collected is not None and not for_target_price:
            collected.setdefault("details", {})[(store, article)] = detail_context
        daily = daily_details(detail_context) if include_details else []
        known_orders = [row for day, row in orders[article].items() if day in order_days]
        order_totals = {
            key: round(sum(float(row.get(key) or 0) for row in known_orders), 2) if order_days else None
            for key in ("orders_count", "orders_amount", "cancel_count", "cancel_amount")
        }
        expected_amount = sum(
            float(orders[article].get(day, {}).get("orders_amount") or 0)
            * (saved.get(day, {}).get("inputs", {}).get("buyout_percent", q) or 0)
            / 100
            for day in days
            if day in order_days
        )
        stock, ad = product["stock"], product["advertising"]
        spend = (
            round(sum(float(row.get("spend") or 0) for day, row in ads[article].items() if day in ad_days), 2)
            if ad_days
            else None
        )
        bought = sum(
            float(row.get("expected_buyouts") or 0) for row in saved.values() if row.get("profit") is not None
        )
        current = calculate(values, without_advertising=True)
        costs = current.get("costs", {})
        row = {
            **identity,
            **order_totals,
            "marketplace": MARKETPLACE,
            "net_orders_count": max(order_totals["orders_count"] - order_totals["cancel_count"], 0)
            if order_days
            else None,
            "net_orders_amount": round(order_totals["orders_amount"] - order_totals["cancel_amount"], 2)
            if order_days
            else None,
            "buyout_percent": q,
            "buyout_orders_count": order_totals["orders_count"],
            "expected_buyout_amount": round(expected_amount, 2) if q is not None and order_days else None,
            "stock": stock["total"],
            "stock_fbs": stock["fbs"],
            "stock_fbo": stock["fbo"],
            "stock_fulfillment": stock["fulfillment"],
            "stock_days": stock["days"],
            "stock_average_daily_orders": stock["average_daily_orders"],
            **{key: ad.get(key) for key in ("impressions", "clicks", "ctr", "cpc")},
            "advertising_spend": spend,
            "advertising_per_unit": round(spend / bought, 2)
            if bought and spend is not None
            else 0.0
            if spend == 0
            else None,
            "drr": round(spend / expected_amount * 100, 2)
            if expected_amount and spend is not None
            else 100.0
            if spend
            else 0.0
            if spend == 0 and order_days
            else None,
            "margin": period["margin"],
            "purchase_value": period["purchase_value"],
            "roi": period["roi"],
            "margin_orders_count": round(bought, 2),
            "margin_complete": period["complete"],
            "margin_missing_days": period["coverage"]["missing_dates"],
            "daily_calculations": daily,
            "funnel_period_from": days[0],
            "funnel_period_to": days[-1],
            "orders_missing_days": sorted(set(days) - order_days),
            "ads_missing_days": sorted(set(days) - ad_days),
            "retail_price": values.get("seller_price"),
            "customer_price": values.get("buyer_price"),
            "pay_price": values.get("pay_price"),
            "purchase_cost": values.get("purchase_price"),
            "fulfillment_cost": values.get("fulfillment_cost"),
            "commission_percent": values.get("commission_percent"),
            "delivery_with_returns": current.get("logistics", {}).get("total"),
            "acquiring_percent": values.get("acquiring_percent"),
            "payment_acceptance": values.get("payment_acceptance"),
            "vat_percent": values.get("vat_percent"),
            "usn_percent": values.get("usn_percent"),
            "team_commission_percent": values.get("company_commission_percent"),
            **{
                key + "_value": costs.get(source)
                for key, source in [
                    ("commission", "commission"),
                    ("acquiring", "acquiring"),
                    ("vat", "vat"),
                    ("usn", "usn"),
                ]
            },
        }
        if not order_days or (q is None and spend != 0):
            row["drr"] = None
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (-(row["orders_amount"] or 0), str(row.get("name") or "").casefold(), row["article"]),
    )


def daily_details(context):
    """Expand only the displayed products from an already prepared report."""
    days, saved, orders, ads, order_days, ad_days, buyout = context
    return [
        daily_row(day, saved.get(day, {}), orders.get(day, {}), ads.get(day, {}),
                  day in order_days, day in ad_days, buyout)
        for day in days
    ]


def totals(rows):
    """Share WB weighted formulas while retaining unknown YM source values."""
    from app.economics.reporting import _unit_profit_report_totals

    result = _unit_profit_report_totals(rows)
    for keys in (
        (
            "orders_count",
            "orders_amount",
            "cancel_count",
            "cancel_amount",
            "net_orders_count",
            "net_orders_amount",
        ),
        ("advertising_spend", "impressions", "clicks", "ctr", "cpc"),
        ("stock_average_daily_orders", "stock_days"),
    ):
        if rows and all(row.get(keys[0]) is None for row in rows):
            for key in keys:
                result[key] = None
    if (
        result["orders_amount"] is None
        or result["advertising_spend"] is None
        or any(row.get("orders_amount") and row.get("expected_buyout_amount") is None for row in rows)
    ):
        result["drr"] = None
    result["orders_missing_days"] = sorted(
        {day for row in rows for day in row.get("orders_missing_days", [])}
    )
    result["ads_missing_days"] = sorted({day for row in rows for day in row.get("ads_missing_days", [])})
    return result


def categories(rows):
    from app.economics.reporting import _unit_profit_category_rows

    grouped = defaultdict(list)
    for row in rows:
        grouped[row["subject"]].append(row)
    return [{**row, **totals(grouped[row["subject"]])} for row in _unit_profit_category_rows(rows)]
