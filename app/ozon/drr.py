from datetime import date, datetime, timedelta
from decimal import Decimal

from app.core.constants import OZON_ADVERTISING, OZON_SCENARIO, AdvertisingKind, AdvertisingState
from app.dto.ozon_advertising import AdvertisingPeriod, AdvertisingSourceDay, AdvertisingSpendSummary
from app.dto.ozon_cabinet import OzonEffectiveBuyout
from app.dto.ozon_calculator import OzonCalculatorAdvertisingWeek
from app.dto.ozon_economics import OzonCalculatorMetrics, OzonCoverage, OzonDrrMetrics, OzonTurnoverData
from app.ozon.advertising_normalizer import daily_expense, kind_source, period_days, spend_summary


def expected_turnover(turnover: Decimal | None, buyout: Decimal | None) -> Decimal | None:
    if turnover is None or turnover <= 0 or buyout is None or buyout <= 0:
        return None
    return turnover * buyout / OZON_SCENARIO.percent_factor


def advertising_drr(
    spend: Decimal | None, turnover: Decimal | None, buyout: Decimal | None
) -> Decimal | None:
    expected = expected_turnover(turnover, buyout)
    return spend / expected * OZON_SCENARIO.percent_factor if spend is not None and expected else None


def calculator_advertising_period(today: date) -> AdvertisingPeriod:
    return AdvertisingPeriod(
        start=today - timedelta(days=OZON_SCENARIO.advertising_days), end=today - timedelta(days=1)
    )


def calculator_advertising_week(
    today: date,
    turnover: OzonTurnoverData,
    sources: tuple[AdvertisingSourceDay, ...],
    *,
    store: str,
    article: str,
    credentials_present: bool,
) -> OzonCalculatorAdvertisingWeek:
    period = calculator_advertising_period(today)
    days = frozenset(period_days(period))
    summary = spend_summary(period, sources, article=article, credentials_present=credentials_present)
    missing_orders = days - turnover.eligible_days.get(store, frozenset())
    missing = tuple(sorted(missing_orders | set(summary.missing_dates)))
    partial = tuple(
        day.day for day in summary.days if day.day not in missing and (not day.complete or day.stale)
    )
    advertising_stamps = tuple(
        item.snapshot.synced_at for item in sources if item.day in days and item.snapshot
    )
    orders_stamps = tuple(
        turnover.updated_at[store, day] for day in days if (store, day) in turnover.updated_at
    )
    return OzonCalculatorAdvertisingWeek(
        period=period,
        spend=summary.amount if summary.complete and not summary.stale else None,
        orders_count=sum(
            row.ordered + row.cancelled
            for row in turnover.products.get((store, article), ())
            if row.day in days
        )
        if not missing_orders
        else None,
        complete=not missing and not partial,
        missing_dates=missing,
        partial_dates=partial,
        other_incomplete=any(
            expense.state != AdvertisingState.READY or expense.stale or expense.amount is None
            for day in summary.days
            for expense in day.breakdown
            if expense.kind not in OZON_ADVERTISING.required_spend_kinds
        ),
        advertising_updated_at=min(advertising_stamps) if advertising_stamps else None,
        orders_updated_at=min(orders_stamps) if orders_stamps else None,
    )


def metrics(
    period: AdvertisingPeriod,
    turnover: OzonTurnoverData,
    sources: tuple[AdvertisingSourceDay, ...],
    *,
    store: str,
    article: str,
    buyout: OzonEffectiveBuyout,
    kinds: tuple[AdvertisingKind, ...] = tuple(AdvertisingKind),
    allow_partial: bool = True,
    spending: AdvertisingSpendSummary | None = None,
) -> OzonDrrMetrics:
    orders = {item.day: item for item in turnover.products.get((store, article), ())}
    ads = {(item.day, item.source): item for item in sources}
    spending_days = {item.day: item.breakdown for item in spending.days} if spending else {}
    required_kinds = tuple(kind for kind in kinds if kind in OZON_ADVERTISING.required_spend_kinds)
    dates: list[str] = []
    partial: list[str] = []
    updated = []
    amount = spend = Decimal(0)
    for day in period_days(period):
        if day not in turnover.eligible_days.get(store, frozenset()):
            continue
        required = tuple(ads.get((day, source)) for source in {kind_source(kind) for kind in required_kinds})
        if any(
            item is None or item.snapshot is None or item.stale or item.state != AdvertisingState.READY
            for item in required
        ):
            continue
        expenses = spending_days.get(day) or tuple(
            daily_expense(
                kind,
                ads.get((day, kind_source(kind))),
                article=article,
                credentials_present=True,
            )
            for kind in kinds
        )
        if expenses and all(item.amount is None for item in expenses):
            continue
        incomplete = any(
            item.state != AdvertisingState.READY or item.amount is None
            for item in expenses
            if item.kind in required_kinds
        )
        if incomplete and not allow_partial:
            continue
        dates.append(day.isoformat())
        if incomplete:
            partial.append(day.isoformat())
        spend += sum((item.amount for item in expenses if item.amount is not None), Decimal(0))
        if order := orders.get(day):
            amount += order.amount
        if stamp := turnover.updated_at.get((store, day)):
            updated.append(stamp)
        for source in {kind_source(kind) for kind in required_kinds}:
            item = ads.get((day, source))
            if item and item.snapshot:
                updated.append(item.snapshot.synced_at)
    missing = tuple(day.isoformat() for day in period_days(period) if day.isoformat() not in dates)
    percentage = Decimal(str(buyout.percent)) if buyout.percent is not None else None
    if buyout.observed_at is not None:
        updated.append(buyout.observed_at)
    ratio = advertising_drr(spend if dates else None, amount if dates else None, percentage)
    return OzonDrrMetrics(
        buyout=buyout,
        turnover=amount if dates else None,
        spend=spend if dates else None,
        received=buyout.received,
        cancelled=buyout.cancelled,
        buyout_percent=buyout.percent,
        drr=float(ratio) if ratio is not None else None,
        coverage=OzonCoverage(
            dates=tuple(dates),
            days=len(dates),
            expected_days=len(period_days(period)),
            complete=not missing and not partial and percentage is not None and not buyout.stale,
            stale=buyout.stale,
            missing_dates=missing,
            partial_dates=tuple(partial),
        ),
        updated_at=min(updated) if updated else None,
    )


def calculator(
    today: date,
    turnover: OzonTurnoverData,
    sources: tuple[AdvertisingSourceDay, ...],
    *,
    store: str,
    article: str,
    buyout: OzonEffectiveBuyout,
    advertising_week: OzonCalculatorAdvertisingWeek | None = None,
) -> OzonCalculatorMetrics:
    period = AdvertisingPeriod(start=today, end=today)
    base = metrics(period, turnover, sources, store=store, article=article, buyout=buyout, kinds=())
    by_source = {item.source: item for item in sources if item.day == today}

    def expense(kinds: tuple[AdvertisingKind, ...]) -> Decimal | None:
        values = tuple(
            daily_expense(kind, by_source.get(kind_source(kind)), article=article, credentials_present=True)
            for kind in kinds
        )
        if any(
            value.state != AdvertisingState.READY or value.stale or value.amount is None for value in values
        ):
            return None
        return sum((value.amount for value in values if value.amount is not None), Decimal(0))

    def updated_at(kinds: tuple[AdvertisingKind, ...]) -> datetime | None:
        stamps = tuple(
            item.snapshot.synced_at
            for source in {kind_source(kind) for kind in kinds}
            if (item := by_source.get(source)) is not None and item.snapshot is not None
        )
        return min(stamps) if stamps else None

    def with_buyout(kinds: tuple[AdvertisingKind, ...]) -> OzonDrrMetrics:
        return metrics(
            period,
            turnover,
            sources,
            store=store,
            article=article,
            buyout=buyout,
            kinds=kinds,
            allow_partial=False,
        )

    other_breakdown = tuple(
        daily_expense(kind, by_source.get(kind_source(kind)), article=article, credentials_present=True)
        for kind in AdvertisingKind
        if kind not in OZON_ADVERTISING.required_spend_kinds
    )
    return OzonCalculatorMetrics(
        advertising_week=advertising_week,
        day=today,
        orders_count=sum(
            row.ordered + row.cancelled
            for row in turnover.products.get((store, article), ())
            if row.day == today
        )
        if today
        in (turnover.eligible_days.get(store, frozenset()) | turnover.confirmed_days.get(store, frozenset()))
        else None,
        other_spend=sum((value.amount for value in other_breakdown if value.amount is not None), Decimal(0)),
        other_breakdown=other_breakdown,
        turnover=base.turnover,
        buyout_percent=buyout.percent,
        buyout=buyout,
        updated_at=base.updated_at,
        clicks=with_buyout(OZON_ADVERTISING.click_kinds),
        orders=with_buyout(OZON_ADVERTISING.order_kinds),
        click_spend=expense(OZON_ADVERTISING.click_kinds),
        order_spend=expense(OZON_ADVERTISING.order_kinds),
        click_updated_at=updated_at(OZON_ADVERTISING.click_kinds),
        order_updated_at=updated_at(OZON_ADVERTISING.order_kinds),
    )
