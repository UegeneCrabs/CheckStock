from datetime import date, datetime
from decimal import Decimal

from app.core.constants import OZON_ADVERTISING, AdvertisingKind, AdvertisingState
from app.dto.ozon_advertising import AdvertisingPeriod, AdvertisingSourceDay, AdvertisingSpendSummary
from app.dto.ozon_cabinet import OzonEffectiveBuyout
from app.dto.ozon_economics import OzonCalculatorMetrics, OzonCoverage, OzonDrrMetrics, OzonTurnoverData
from app.ozon.advertising_normalizer import daily_expense, kind_source, period_days


def metrics(
    period: AdvertisingPeriod,
    turnover: OzonTurnoverData,
    sources: tuple[AdvertisingSourceDay, ...],
    *,
    store: str,
    article: str,
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
    received = cancelled = 0
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
            received += order.received
            cancelled += order.cancelled
        if stamp := turnover.updated_at.get((store, day)):
            updated.append(stamp)
        for source in {kind_source(kind) for kind in required_kinds}:
            item = ads.get((day, source))
            if item and item.snapshot:
                updated.append(item.snapshot.synced_at)
    missing = tuple(day.isoformat() for day in period_days(period) if day.isoformat() not in dates)
    buyout = Decimal(received) / (received + cancelled) if received + cancelled else None
    return OzonDrrMetrics(
        turnover=amount if dates else None,
        spend=spend if dates else None,
        received=received if dates else None,
        cancelled=cancelled if dates else None,
        buyout_percent=float(buyout * OZON_ADVERTISING.percent_factor) if buyout is not None else None,
        drr=float(spend / amount * buyout * OZON_ADVERTISING.percent_factor)
        if amount > 0 and buyout is not None
        else None,
        coverage=OzonCoverage(
            dates=tuple(dates),
            days=len(dates),
            expected_days=len(period_days(period)),
            complete=not missing and not partial,
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
) -> OzonCalculatorMetrics:
    period = AdvertisingPeriod(start=today, end=today)
    base = metrics(period, turnover, sources, store=store, article=article, kinds=())
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
        value = metrics(
            period, turnover, sources, store=store, article=article, kinds=kinds, allow_partial=False
        )
        percentage = Decimal(str(buyout.percent)) if buyout.percent is not None else None
        calculated = (
            float(value.spend / value.turnover * percentage)
            if value.spend is not None
            and value.turnover is not None
            and value.turnover > 0
            and percentage is not None
            else None
        )
        return value.model_copy(update={"buyout_percent": buyout.percent, "drr": calculated})

    other_breakdown = tuple(
        daily_expense(kind, by_source.get(kind_source(kind)), article=article, credentials_present=True)
        for kind in AdvertisingKind
        if kind not in OZON_ADVERTISING.required_spend_kinds
    )
    return OzonCalculatorMetrics(
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
