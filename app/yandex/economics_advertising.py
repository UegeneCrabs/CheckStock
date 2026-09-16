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
    scenario = scenario or {}
    spend = scenario.get("advertising_spend")
    manual_spend = spend is not None
    manual_drr = scenario.get("advertising_mode") == "plan" or (
        "advertising_mode" not in scenario and scenario.get("plan_drr") is not None
    )
    manual = manual_spend or manual_drr
    amount = weekly.get("orders_amount") or 0
    buyout = values.get("buyout_percent") or 0
    turnover = amount * buyout / 100
    drr = scenario.get("plan_drr") if manual_drr else weekly["drr"]
    if manual_spend:
        # A zero budget is valid even without sales; positive spend needs a base.
        drr = 0.0 if spend == 0 else round(spend / turnover * 100, 2) if turnover > 0 else None
    elif manual_drr:
        spend = round(turnover * drr / 100, 2) if drr is not None and turnover > 0 else None
        if drr == 0:
            spend = 0.0
    else:
        spend = weekly.get("advertising_spend")
    values["advertising_mode"] = "plan" if manual else "weekly"
    values["plan_drr"] = drr
    values["advertising_spend"] = spend
    origins["plan_drr"] = (
        "Сценарий" if manual else f"Последние 7 завершённых дней: данные за {weekly['days']} из 7 дней"
    )
    origins["advertising_spend"] = "Сценарий" if manual else "Реклама за последние 7 завершённых дней"
    origins["advertising_mode"] = "Сценарий" if manual else "ДРР за последние 7 завершённых дней"
