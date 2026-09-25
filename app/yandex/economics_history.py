"""Daily YM series for the shared WB-style 14-day chart (plus a comparison week)."""

from datetime import datetime, timedelta

from app.core.domain import MOSCOW_TIMEZONE
from app.economics.wb.calculations import calculate_drr_percent
from app.repositories import catalog, stock_history
from app.repositories import unit_economics_yandex as metrics
from app.repositories import yandex_economics as repository
from app.yandex import economics
from app.yandex import economics_shared as shared
from app.yandex.economics_calculation import VERSION
from app.yandex.economics_days import resolve_day


def product_history(store, article, scheme, *, today=None):
    """Read local history only; opening the chart never loads APIs or saves snapshots."""
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    start, end = (today - timedelta(days=20)).isoformat(), today.isoformat()
    history = shared.history(
        repository.history(
            store, (today - timedelta(days=30)).isoformat(), today.isoformat()
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
    live = {"inputs": current["values"], "calculation_version": VERSION}
    chart = []
    for day in metrics.days_between(start, end):
        saved = closed.get(day, {})
        snapshot = (
            cache["sources"].get((article, "day-input:" + day + ":" + shared.SCHEME), {}).get("values", {})
        )
        if snapshot.get("basis") not in {"today_prices", "today_observations"}:
            snapshot = {}
        baseline = live if day == end else {"inputs": snapshot.get("values") or {}, "calculation_version": snapshot.get("version") or VERSION}
        resolved = resolve_day(day, saved or baseline, orders.get(day, {}), ads.get(day, {}), day in order_days, day in ads_days)
        inputs = resolved["inputs"]
        buyout = inputs.get("buyout_percent")
        count, spend, profit = resolved["orders_count"], resolved["advertising_spend"], resolved["profit"]
        amount = float(orders.get(day, {}).get("orders_amount") or 0) if day in order_days else None

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
                "margin_complete": resolved["complete"], "messages": resolved["messages"],
                **quantities,
                "stock_units": sum(available_stock) if available_stock else None,
            }
        )
    return {"history": history, "chart": chart, "today": end, "scheme": scheme}
