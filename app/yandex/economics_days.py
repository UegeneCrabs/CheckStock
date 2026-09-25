"""Resolve dated YM inputs against local complete source days and manual overlays."""

from app.economics.completeness import describe
from app.economics.daily_calculation import calculate
from app.yandex.economics_calculation import VERSION


def resolve_day(day, saved, order, ad, orders_known, ads_known):
    inputs = dict(saved.get("inputs") or {})
    overrides = saved.get("overrides") or {}
    count = overrides.get("orders_count", int(order.get("orders_count") or 0) if orders_known else None)
    spend = overrides.get("advertising_spend", float(ad.get("spend") or 0) if ads_known else None)
    inputs.update(orders_count=count, advertising_spend=spend)
    version = saved.get("calculation_version") or VERSION
    values, result = calculate("YANDEX MARKET", inputs, version)
    missing = result["daily_missing"]
    return {**saved, "day": day, "inputs": values, "result": result,
            "orders_count": count, "advertising_spend": spend, "expected_buyouts": result["expected_buyouts"],
            "profit": result["day_profit"], "purchase_value": result["purchase_value"],
            "missing": missing, "complete": result["daily_complete"], "status": result["daily_status"],
            "messages": ["Не учтены / неизвестны: " + ", ".join(describe(missing, day))] if missing else [],
            "calculation_version": version}


def resolve_period(days, saved, orders, ads, order_days, ad_days):
    return {day: resolve_day(day, saved.get(day, {}), orders.get(day, {}), ads.get(day, {}),
                             day in order_days, day in ad_days) for day in days}
