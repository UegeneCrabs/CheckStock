from datetime import datetime, time
from decimal import Decimal

from app.core.constants import TIME, OzonHistoryField, OzonPriceState
from app.dto.ozon_advertising import AdvertisingPeriod
from app.dto.ozon_history import OzonHistoryDay, OzonHistoryEvidence, OzonHistoryQuery, OzonStockDay
from app.ozon.advertising_normalizer import period_days, source_days, spend_summary
from app.ozon.advertising_read_index import AdvertisingReadIndex
from app.ozon.cabinet_normalizer import effective_buyout
from app.ozon.drr import metrics


def history_days(
    query: OzonHistoryQuery, period: AdvertisingPeriod, evidence: OzonHistoryEvidence
) -> tuple[OzonHistoryDay, ...]:
    turnover = evidence.turnover
    orders = {row.day: row for row in turnover.products.get((query.store, query.article), ())}
    confirmed = turnover.confirmed_days.get(query.store, frozenset())
    eligible = turnover.eligible_days.get(query.store, frozenset())
    sources = AdvertisingReadIndex(source_days(period, evidence.advertising)).article(query.article)
    spending = spend_summary(period, sources, article=query.article, credentials_present=True)
    ads = {row.day: row for row in spending.days}
    stocks = {row.day: row for row in evidence.stocks}
    prices = {row.day: row.value for row in evidence.prices}
    buyouts = {row.day: row for row in evidence.buyouts if row.store_slug == query.store}
    result = []
    for day in period_days(period):
        partial = []
        stale = []
        order = orders.get(day)
        known_orders = order is not None or day in confirmed or day in eligible
        if known_orders and day not in confirmed:
            partial.append(OzonHistoryField.ORDERS)
        if known_orders and (day not in eligible or query.store in turnover.stale_stores):
            stale.append(OzonHistoryField.ORDERS)
        expense = ads[day]
        if expense.amount is not None and not expense.complete:
            partial.append(OzonHistoryField.ADS)
        if expense.amount is not None and expense.stale:
            stale.append(OzonHistoryField.ADS)
        saved_buyout = buyouts.get(day)
        buyout = effective_buyout(
            query.article,
            saved_buyout,
            saved_buyout.observed_at
            if saved_buyout
            else datetime.combine(day, time.min, TIME.moscow_timezone),
        )
        ratio = metrics(
            AdvertisingPeriod(start=day, end=day),
            turnover,
            sources,
            store=query.store,
            article=query.article,
            spending=spending,
            buyout=buyout,
        )
        if ratio.drr is not None and (not ratio.coverage.complete or day not in confirmed):
            partial.append(OzonHistoryField.DRR)
        if buyout.stale:
            stale.append(OzonHistoryField.DRR)
        stock = stocks.get(day, OzonStockDay(day=day))
        quantities = (stock.fbo, stock.fbs, stock.fulfillment)
        known_stock = tuple(value for value in quantities if value is not None)
        if known_stock and any(value is None for value in quantities):
            partial.append(OzonHistoryField.STOCK)
        price = prices.get(day)
        price_amount = (
            price.amount
            if price is not None
            and price.state != OzonPriceState.CONFLICT
            and price.received_at is not None
            and price.received_at.astimezone(TIME.moscow_timezone).date() == day
            else None
        )
        if (
            price_amount is not None
            and price is not None
            and (price.stale or price.state != OzonPriceState.AVAILABLE)
        ):
            stale.append(OzonHistoryField.PRICE)
        result.append(
            OzonHistoryDay(
                buyout=buyout,
                date=day,
                orders_count=order.ordered if order else 0 if known_orders else None,
                turnover_rub=order.amount if order else Decimal(0) if known_orders else None,
                advertising_rub=expense.amount,
                drr_percent=ratio.drr,
                buyout_percent=ratio.buyout_percent,
                stock_units=sum(known_stock) if known_stock else None,
                fbo_units=stock.fbo,
                fbs_units=stock.fbs,
                fulfillment_units=stock.fulfillment,
                price_with_spp_rub=price_amount,
                partial_fields=tuple(partial),
                stale_fields=tuple(stale),
            )
        )
    return tuple(result)
