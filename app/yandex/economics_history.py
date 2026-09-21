"""Daily YM series for the shared WB-style 14-day chart (plus a comparison week)."""

from datetime import datetime, timedelta

from app.core.domain import MOSCOW_TIMEZONE
from app.economics.wb.calculations import calculate_drr_percent
from app.repositories import catalog, stock_history
from app.repositories import unit_economics_yandex as metrics
from app.repositories import yandex_economics as repository
from app.yandex import economics
from app.yandex import economics_shared as shared
from app.yandex.economics_calculation import calculate


def product_history(store, article, scheme, *, today=None):
    """Read local history only; opening the chart never loads APIs or saves snapshots."""
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    start, end = (today - timedelta(days=20)).isoformat(), today.isoformat()
    history = shared.history(
        repository.history(
            store, (today - timedelta(days=30)).isoformat(), (today - timedelta(days=1)).isoformat()
        ),
        article,
    )
    closed = {row["day"]: row for row in history}
    ads, ads_days = metrics.get_history(store, "advertising", start, end)
    orders, order_days = metrics.get_history(store, "orders", start, end)
    ads = {row["day"]: row for row in ads if row["article"] == article}
    orders = {row["day"]: row for row in orders if row["article"] == article}
    stocks = {
        row["day"]: row
        for row in stock_history.get_daily_stock_history((store,), metrics.MARKETPLACE, start, end)
        if row["article"] == article
    }
    current_stock = next(
        (
            row
            for row in catalog.get_stock_items(store, metrics.MARKETPLACE, ("fbs", "fbo"))
            if row["article"] == article
        ),
        {},
    )
    cache = economics.context(store)
    current = economics.current_inputs(store, article, scheme, today=today, state_cache=cache)
    live = {"values": current["values"], "result": calculate(current["values"], without_advertising=True)}
    chart = []
    for day in metrics.days_between(start, end):
        saved = closed.get(day, {})
        snapshot = (
            cache["sources"].get((article, "day-input:" + day + ":" + shared.SCHEME), {}).get("values", {})
        )
        if snapshot.get("basis") not in {"today_prices", "today_observations"}:
            snapshot = {}
        baseline = live if day == end else snapshot
        inputs = saved.get("inputs") or baseline.get("values") or {}
        buyout = inputs.get("buyout_percent")
        if buyout is None:
            buyout = current["values"].get("buyout_percent")

        order = orders.get(day, {})
        known_orders = day in order_days
        count = int(order.get("orders_count", 0)) if known_orders else None
        amount = float(order.get("orders_amount", 0)) if known_orders else None
        spend = None
        if day in ads_days:
            total_spend = float(ads.get(day, {}).get("spend", 0))
            spend = total_spend

        profit = None
        if saved:
            # Closed daily economics are immutable, including their order/ad allocation.
            profit = saved.get("profit")
            count = saved.get("orders_count")
            spend = saved.get("advertising_spend")
        elif count is not None and spend is not None:
            unit_margin = baseline.get("result", {}).get("margin")
            saved_buyout = baseline.get("values", {}).get("buyout_percent")
            if unit_margin is not None and saved_buyout is not None:
                profit = round(unit_margin * count * saved_buyout / 100 - spend, 2)
            elif count == 0:
                profit = round(-spend, 2)

        stock = stocks.get(day, {})
        quantities = {}
        for key, source_key in (("fbs", "fbs_stock"), ("fbo", "fbo_stock"), ("fulfillment", "ff_available")):
            value = stock.get(key)
            if value is None and day == end:
                value = current_stock.get(source_key)
            quantities[key + "_units"] = int(value) if value is not None else None
        available_stock = [value for value in quantities.values() if value is not None]
        chart.append(
            {
                "date": day,
                "label": day[8:10] + "." + day[5:7],
                "orders_count": count,
                "turnover_rub": amount,
                "buyout_percent": buyout,
                "advertising_rub": round(spend, 2) if spend is not None else None,
                "drr_percent": calculate_drr_percent(spend, amount, buyout)
                if spend is not None and amount is not None and (buyout is not None or amount == 0)
                else None,
                "margin_rub": profit,
                **quantities,
                "stock_units": sum(available_stock) if available_stock else None,
            }
        )
    return {"history": history, "chart": chart, "today": end, "scheme": scheme}
