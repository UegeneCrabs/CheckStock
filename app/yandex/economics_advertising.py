"""Calculator DRR over matching advertising/order days in the last closed week."""

from collections import defaultdict
from datetime import timedelta

from app.repositories import unit_economics_yandex as metrics


def weekly_history(store, today):
    start, end = (today - timedelta(days=7)).isoformat(), (today - timedelta(days=1)).isoformat()
    ads, ad_days = metrics.get_history(store, "advertising", start, end)
    orders, order_days = metrics.get_history(store, "orders", start, end)
    expected = metrics.days_between(start, end)
    available = [day for day in expected if day in ad_days and day in order_days]
    advertising_days = [day for day in expected if day in ad_days]
    totals = defaultdict(
        lambda: {"spend": 0.0, "advertising_spend": 0.0, "orders_amount": 0.0, "orders_count": 0}
    )
    for row in ads:
        if row["day"] in advertising_days:
            totals[row["article"]]["advertising_spend"] += float(row.get("spend") or 0)
        if row["day"] in available:
            totals[row["article"]]["spend"] += float(row.get("spend") or 0)
    for row in orders:
        if row["day"] in available:
            totals[row["article"]]["orders_amount"] += float(row.get("orders_amount") or 0)
            totals[row["article"]]["orders_count"] += int(row.get("orders_count") or 0)
    return {
        "period_from": start,
        "period_to": end,
        "days": len(available),
        "expected_days": 7,
        "dates": available,
        "complete": len(available) == 7,
        "advertising_days": len(advertising_days),
        "advertising_complete": len(advertising_days) == 7,
        "missing_dates": [day for day in expected if day not in available],
        "missing_orders_dates": [day for day in expected if day not in order_days],
        "missing_advertising_dates": [day for day in expected if day not in ad_days],
        "products": dict(totals),
    }


def product_drr(history, article, buyout_percent):
    # YM advertising is reported per offer, without an FBY/FBS split. Use the
    # same offer's combined order turnover, as in the marketplace DRR column.
    totals = history["products"].get(article, {})
    spend, amount = (round(totals.get(key, 0.0), 2) for key in ("spend", "orders_amount"))
    drr, message = None, ""
    if not history["days"]:
        message = "Нет совместных данных заказов и рекламы за последние 7 завершённых дней."
    elif spend == 0:
        drr = 0.0
    elif amount <= 0:
        message = "Есть расходы на рекламу, но нет оборота заказов: ДРР не определён."
    elif buyout_percent is None or buyout_percent <= 0:
        message = "Для ДРР нужен положительный процент выкупа."
    else:
        drr = round(spend / amount / (buyout_percent / 100) * 100, 2)
    return {
        **{key: value for key, value in history.items() if key != "products"},
        "spend": spend,
        "advertising_spend": round(totals.get("advertising_spend", 0.0), 2)
        if history["advertising_days"]
        else None,
        "orders_amount": amount,
        "orders_count": totals.get("orders_count", 0),
        "buyout_percent": buyout_percent,
        "drr": drr,
        "message": message,
    }


def apply_calculator_drr(values, origins, weekly, scenario):
    """The calculator distributes ads across bought units on matching days."""
    scenario = scenario or {}
    per_unit = scenario.get("advertising_per_buyout")
    spend = scenario.get("advertising_spend")
    manual_drr = scenario.get("plan_drr") is not None and per_unit is None and spend is None
    manual = per_unit is not None or spend is not None or manual_drr
    price = values.get("seller_price")
    bought = (weekly.get("orders_count") or 0) * (values.get("buyout_percent") or 0) / 100
    if manual_drr:
        per_unit = price * scenario["plan_drr"] / 100 if price is not None else None
    elif per_unit is None:
        if spend is None and weekly.get("days"):
            spend = weekly.get("spend")
        per_unit = spend / bought if spend is not None and bought > 0 else 0.0 if spend == 0 else None
    drr = (
        scenario["plan_drr"]
        if manual_drr
        else per_unit / price * 100
        if per_unit is not None and price and price > 0
        else 0.0
        if per_unit == 0
        else None
    )
    if not manual:
        drr = weekly["drr"]
    values.update(
        advertising_mode="plan" if manual else "weekly",
        advertising_basis="drr" if manual_drr else "per_buyout",
        plan_drr=drr,
        advertising_per_buyout=per_unit,
        advertising_spend=per_unit * bought if per_unit is not None else None,
    )
    origin = (
        "Сценарий"
        if manual
        else f"Последние 7 завершённых дней: совместные данные за {weekly['days']} из 7 дней"
    )
    for key in ("plan_drr", "advertising_per_buyout", "advertising_spend", "advertising_mode"):
        origins[key] = origin
