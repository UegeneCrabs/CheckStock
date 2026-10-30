from datetime import date, datetime, timedelta
from decimal import Decimal

from app.core.constants import OZON_MARGIN, OZON_SCENARIO, TIME, OzonPriceState
from app.dto.ozon_advertising import AdvertisingSpendDay
from app.dto.ozon_cabinet import OzonEffectiveParameters
from app.dto.ozon_calculator import OzonCalculatorValues
from app.dto.ozon_commissions import OzonCommissionData
from app.dto.ozon_economics import (
    OzonCoverage,
    OzonCurrentEconomics,
    OzonPeriodEconomics,
    OzonTurnoverData,
    OzonTurnoverValue,
)
from app.dto.ozon_logistics import LogisticsData
from app.dto.ozon_margin import OzonMarginDay, OzonMarginInputs, OzonMarginSnapshot, OzonMarginSource
from app.dto.ozon_prices import PriceValues
from app.dto.ozon_storage import StorageData
from app.economics.ozon_calculator import recalculate
from app.ozon.margin_normalizer import day_margin, money


def snapshot(
    source: OzonMarginSource,
    prices: PriceValues,
    commission: OzonCommissionData,
    logistics: LogisticsData,
    storage: StorageData,
    cabinet: OzonEffectiveParameters,
    now: datetime,
) -> OzonMarginSnapshot:
    values = recalculate(
        OzonCalculatorValues(
            seller_price=prices.seller_price.amount
            if prices.seller_price.state != OzonPriceState.CONFLICT
            else None,
            buyer_price=prices.black_price.amount
            if prices.black_price.state != OzonPriceState.CONFLICT
            else None,
            wallet_price=prices.green_price.amount
            if prices.green_price.state != OzonPriceState.CONFLICT
            else None,
            purchase=source.purchase_price,
            fulfillment=source.fulfillment_cost,
            commission_amount=commission.amount,
            logistics_amount=money(logistics.snapshot.actual_per_unit) if logistics.snapshot else None,
            storage_amount=money(storage.snapshot.per_unit) if storage.snapshot else None,
            acquiring_percent=Decimal(str(cabinet.expenses.acquiring_percent))
            if cabinet.expenses.acquiring_percent is not None
            else None,
            team_percent=source.team_commission_percent,
            unallocated_percent=Decimal(str(cabinet.expenses.unallocated_percent)),
            vat_percent=Decimal(str(cabinet.expenses.vat_percent))
            if cabinet.expenses.vat_percent is not None
            else None,
            usn_percent=Decimal(str(cabinet.expenses.usn_percent))
            if cabinet.expenses.usn_percent is not None
            else None,
        )
    )
    issues = []
    if any(
        (
            prices.seller_price.stale,
            prices.black_price.stale,
            prices.green_price.stale,
            commission.stale,
            logistics.stale,
            storage.stale,
            cabinet.buyout.stale,
        )
    ):
        issues.append(OZON_MARGIN.sources_stale)
    if logistics.snapshot and logistics.snapshot.issues:
        issues.append(OZON_MARGIN.logistics_partial)
    if storage.snapshot and storage.snapshot.issues:
        issues.append(OZON_MARGIN.storage_partial)
    if cabinet.buyout.default_applied:
        issues.append(OZON_MARGIN.buyout_default)
    return OzonMarginSnapshot(
        store_slug=source.store_slug,
        article=source.article,
        day=now.astimezone(TIME.moscow_timezone).date(),
        observed_at=now,
        inputs=OzonMarginInputs.model_validate(
            {key: getattr(values, key) for key in OzonMarginInputs.model_fields}
        ),
        buyout_percent=Decimal(str(cabinet.buyout.percent)) if cabinet.buyout.percent is not None else None,
        issues=tuple(issues),
    )


def calculate_day(
    store: str,
    article: str,
    day: date,
    saved: OzonMarginSnapshot | None,
    orders: OzonTurnoverData,
    advertising: AdvertisingSpendDay | None,
) -> OzonMarginDay:
    known = day in (
        orders.eligible_days.get(store, frozenset()) | orders.confirmed_days.get(store, frozenset())
    )
    rows = tuple(row for row in orders.products.get((store, article), ()) if row.day == day)
    count = sum(row.ordered + row.cancelled for row in rows) if known else None
    issues = []
    if store in orders.stale_stores:
        issues.append(OZON_MARGIN.orders_partial)
    if advertising and (not advertising.complete or advertising.stale):
        issues.append(OZON_MARGIN.advertising_partial)
    return day_margin(
        day,
        saved,
        count,
        advertising.amount if advertising and advertising.complete else None,
        issues=tuple(issues),
    )


def current(value: OzonMarginDay, turnover: OzonTurnoverValue) -> OzonCurrentEconomics:
    complete = value.profit is not None and not value.missing and not value.issues
    return OzonCurrentEconomics(
        turnover=float(turnover.amount) if turnover.amount is not None else None,
        turnover_coverage=turnover.coverage,
        margin=float(value.margin) if value.margin is not None else None,
        roi=float(value.roi) if value.roi is not None else None,
        orders=value.orders,
        buyout_percent=float(value.buyout_percent) if value.buyout_percent is not None else None,
        advertising_spend=float(value.advertising_spend) if value.advertising_spend is not None else None,
        period_to=value.day.isoformat(),
        day_profit=value.profit,
        expected_buyouts=value.expected_buyouts,
        day_purchase_value=value.purchase_value,
        purchase_value=value.inputs.purchase,
        complete=complete,
        daily_complete=complete,
        calculation=value,
    )


def period(
    original: OzonPeriodEconomics,
    days: tuple[OzonMarginDay, ...],
    start: date,
    end: date,
) -> OzonPeriodEconomics:
    dates = tuple((start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1))
    included = tuple(day for day in days if day.profit is not None)
    available = tuple(day.day.isoformat() for day in included)
    partial = tuple(day.day.isoformat() for day in included if day.missing or day.issues)
    coverage = OzonCoverage(
        dates=available,
        days=len(available),
        expected_days=len(dates),
        complete=len(available) == len(dates) and not partial,
        missing_dates=tuple(day for day in dates if day not in available),
        partial_dates=partial,
    )
    profit = sum((day.profit for day in included if day.profit is not None), Decimal(0)) if included else None
    purchase = (
        sum((day.purchase_value for day in included if day.purchase_value is not None), Decimal(0))
        if included and all(day.purchase_value is not None for day in included)
        else None
    )
    roi = (
        money(profit / purchase * OZON_SCENARIO.percent_factor)
        if profit is not None and purchase is not None and purchase > 0
        else None
    )
    return original.model_copy(
        update={
            "margin": float(profit) if profit is not None else None,
            "roi": float(roi) if roi is not None else None,
            "purchase_value": purchase,
            "roi_purchase_value": purchase,
            "margin_coverage": coverage,
            "roi_coverage": coverage,
            "complete": coverage.complete,
            "daily": days,
        }
    )
