from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from app.core.constants import (
    OZON_ADVERTISING,
    TIME,
    AdvertisingAllocation,
    AdvertisingError,
    AdvertisingKind,
    AdvertisingSource,
    AdvertisingState,
)
from app.dto.ozon_advertising import (
    AdvertisingBreakdown,
    AdvertisingDailyExpense,
    AdvertisingDay,
    AdvertisingFact,
    AdvertisingImport,
    AdvertisingPeriod,
    AdvertisingSnapshot,
    AdvertisingSourceDay,
    AdvertisingSourceStatus,
    AdvertisingSpendDay,
    AdvertisingSpendSummary,
    AdvertisingWindowAdjustment,
    AllProductsOrdersReport,
    PerformanceCampaign,
    SelectedOrdersReport,
)
from app.dto.ozon_economics import OzonCoverage, OzonTrafficMetrics


def completed_period(now: datetime | None = None) -> AdvertisingPeriod:
    end = (now or datetime.now(UTC)).astimezone(TIME.moscow_timezone).date() - timedelta(days=1)
    return AdvertisingPeriod(start=end - timedelta(days=OZON_ADVERTISING.period_days - 1), end=end)


def period_days(period: AdvertisingPeriod) -> tuple[date, ...]:
    return tuple(
        period.start + timedelta(days=index) for index in range((period.end - period.start).days + 1)
    )


def campaign_kind(campaign: PerformanceCampaign) -> AdvertisingKind:
    kinds = {
        OZON_ADVERTISING.selected_type: AdvertisingKind.CPO_SELECTED,
        OZON_ADVERTISING.all_type: AdvertisingKind.CPO_ALL,
        OZON_ADVERTISING.banner_type: AdvertisingKind.BANNER,
        OZON_ADVERTISING.video_type: AdvertisingKind.VIDEO,
        OZON_ADVERTISING.brand_type: AdvertisingKind.BRAND_SHELF,
        OZON_ADVERTISING.blogger_type: AdvertisingKind.BLOGGER,
        OZON_ADVERTISING.vk_type: AdvertisingKind.VK,
    }
    if (
        campaign.adv_object_type == OZON_ADVERTISING.sku_type
        and campaign.payment_type == OZON_ADVERTISING.cpc_payment
    ):
        return AdvertisingKind.CPC
    return kinds.get(campaign.adv_object_type, AdvertisingKind.OTHER)


def kind_source(kind: AdvertisingKind) -> AdvertisingSource:
    if kind == AdvertisingKind.CPC:
        return AdvertisingSource.CPC
    if kind in (AdvertisingKind.CPO_SELECTED, AdvertisingKind.CPO_COMBO):
        return AdvertisingSource.SELECTED
    if kind == AdvertisingKind.CPO_ALL:
        return AdvertisingSource.ALL_PRODUCTS
    return AdvertisingSource.CAMPAIGNS


def selected_facts(report: SelectedOrdersReport) -> tuple[AdvertisingFact, ...]:
    result = []
    for row in report.rows:
        if row.orders_source == OZON_ADVERTISING.combo_source:
            kind = AdvertisingKind.CPO_COMBO
        elif OZON_ADVERTISING.selected_source in row.orders_source:
            kind = AdvertisingKind.CPO_SELECTED
        else:
            raise ValueError(AdvertisingError.UNKNOWN_CPO_SOURCE)
        result.append(
            AdvertisingFact(
                day=row.day,
                kind=kind,
                amount=row.amount,
                raw_type=row.orders_source,
                advertised_sku=row.advertised_sku,
                purchased_sku=row.purchased_sku,
                order_id=row.order_id,
            )
        )
    return tuple(result)


def all_product_facts(report: AllProductsOrdersReport) -> tuple[AdvertisingFact, ...]:
    return tuple(
        AdvertisingFact(
            day=row.day,
            kind=AdvertisingKind.CPO_ALL,
            amount=row.amount,
            raw_type=OZON_ADVERTISING.all_type,
            advertised_sku=row.advertised_sku,
            purchased_sku=row.purchased_sku,
            order_id=row.order_id,
        )
        for row in report.rows
    )


def attach_articles(snapshot: AdvertisingSnapshot, articles: dict[str, str]) -> AdvertisingSnapshot:
    facts = []
    for fact in snapshot.facts:
        if not snapshot.period.start <= fact.day <= snapshot.period.end:
            raise ValueError(AdvertisingError.DATE_OUTSIDE_PERIOD)
        article = articles.get(fact.advertised_sku or "")
        facts.append(
            fact.model_copy(
                update={
                    "article": article,
                    "allocation": (
                        AdvertisingAllocation.EQUAL
                        if fact.allocation == AdvertisingAllocation.EQUAL
                        else AdvertisingAllocation.DIRECT
                    )
                    if article
                    else AdvertisingAllocation.UNALLOCATED,
                }
            )
        )
    return snapshot.model_copy(update={"facts": tuple(facts)})


def allocate_equally(fact: AdvertisingFact, dated_members: tuple[str, ...]) -> tuple[AdvertisingFact, ...]:
    members = sorted(set(dated_members))
    if not members:
        return (fact,)
    quantum = Decimal(OZON_ADVERTISING.money_quantum)
    if fact.amount != fact.amount.quantize(quantum):
        raise ValueError(AdvertisingError.ALLOCATION_PRECISION)
    share = (fact.amount / len(members)).quantize(quantum, rounding=ROUND_DOWN)
    remainder = int((fact.amount - share * len(members)) / quantum)
    return tuple(
        fact.model_copy(
            update={
                "advertised_sku": sku,
                "article": None,
                "allocation": AdvertisingAllocation.EQUAL,
                "amount": share
                + (quantum.copy_sign(Decimal(remainder)) if index < abs(remainder) else Decimal(0)),
            }
        )
        for index, sku in enumerate(members)
    )


def source_days(
    period: AdvertisingPeriod, statuses: tuple[AdvertisingSourceStatus, ...]
) -> tuple[AdvertisingSourceDay, ...]:
    result = []
    for day in period_days(period):
        for source in AdvertisingSource:
            windows = tuple(
                status
                for status in statuses
                if (
                    status.source == source
                    or (
                        source == AdvertisingSource.CPC
                        and status.source == AdvertisingSource.CAMPAIGNS
                        and status.snapshot is not None
                        and status.snapshot.includes_cpc
                    )
                )
                and status.period.start <= day <= status.period.end
            )
            latest = max(windows, key=lambda status: status.attempted_at, default=None)
            snapshots = tuple(
                status.snapshot
                for status in windows
                if status.snapshot is not None
                and status.snapshot.period.start <= day <= status.snapshot.period.end
            )
            snapshot = max(
                snapshots,
                key=lambda item: (item.synced_at, item.period.start, item.period.end),
                default=None,
            )
            stale = bool(
                snapshot is not None
                and (
                    (
                        latest is not None
                        and latest.state != AdvertisingState.READY
                        and latest.attempted_at >= snapshot.synced_at
                    )
                    or (not snapshot.finalized and day <= completed_period().end)
                )
            )
            result.append(
                AdvertisingSourceDay(
                    day=day,
                    source=source,
                    state=AdvertisingState.READY
                    if snapshot is not None
                    else latest.state
                    if latest is not None
                    else AdvertisingState.MISSING,
                    snapshot=snapshot.model_copy(
                        update={
                            "source": source,
                            "facts": tuple(
                                fact
                                for fact in snapshot.facts
                                if fact.day == day and kind_source(fact.kind) == source
                            ),
                            "corrections": tuple(
                                item for item in snapshot.corrections if kind_source(item.kind) == source
                            ),
                        }
                    )
                    if snapshot is not None
                    else None,
                    stale=stale,
                )
            )
    return tuple(result)


def import_plan(
    period: AdvertisingPeriod, statuses: tuple[AdvertisingSourceStatus, ...], yesterday: date
) -> tuple[AdvertisingImport, ...]:
    missing = {
        (item.source, item.day)
        for item in source_days(period, statuses)
        if item.snapshot is None
        or item.stale
        or not item.snapshot.finalized
        or (item.source == AdvertisingSource.CPC and not item.snapshot.traffic_collected)
        or any(
            fact.kind in OZON_ADVERTISING.required_spend_kinds
            and fact.article is None
            and fact.advertised_sku
            and (fact.amount or fact.impressions or fact.clicks)
            for fact in item.snapshot.facts
        )
    }
    result: list[AdvertisingImport] = []
    for source in AdvertisingSource:
        if (source, yesterday) in missing:
            result.append(
                AdvertisingImport(source=source, period=AdvertisingPeriod(start=yesterday, end=yesterday))
            )
            missing.remove((source, yesterday))
    for source in AdvertisingSource:
        for status in sorted(statuses, key=lambda item: item.attempted_at):
            if (
                status.source != source
                or status.period.end >= yesterday
                or status.state == AdvertisingState.NO_CREDENTIALS
            ):
                continue
            pending_dates = set(period_days(status.period))
            if pending_dates and all((source, day) in missing for day in pending_dates):
                result.append(AdvertisingImport(source=source, period=status.period))
                missing.difference_update((source, day) for day in pending_dates)
        dates = sorted(day for candidate, day in missing if candidate == source)
        if not dates:
            continue
        start = last = dates[0]
        for day in dates[1:]:
            if day != last + timedelta(days=1):
                result.append(
                    AdvertisingImport(source=source, period=AdvertisingPeriod(start=start, end=last))
                )
                start = day
            last = day
        result.append(AdvertisingImport(source=source, period=AdvertisingPeriod(start=start, end=last)))
    return tuple(result)


def daily_expense(
    kind: AdvertisingKind,
    source_day: AdvertisingSourceDay | None,
    *,
    article: str | None,
    credentials_present: bool,
) -> AdvertisingDailyExpense:
    snapshot = source_day.snapshot if source_day else None
    if snapshot is None:
        state = source_day.state if source_day else AdvertisingState.MISSING
        if state == AdvertisingState.MISSING and not credentials_present:
            state = AdvertisingState.NO_CREDENTIALS
        return AdvertisingDailyExpense(kind=kind, amount=None, state=state)
    facts = tuple(fact for fact in snapshot.facts if fact.kind == kind and fact.article == article)
    unavailable = kind in snapshot.unavailable_kinds
    unallocated = article is not None and any(
        fact.kind == kind and fact.article is None and fact.amount for fact in snapshot.facts
    )
    incomplete = unavailable or unallocated
    amount = sum((fact.amount for fact in facts), Decimal(0)) if facts or not incomplete else None
    state = AdvertisingState.READY
    if incomplete:
        state = AdvertisingState.UNSUPPORTED if unavailable and amount is None else AdvertisingState.PARTIAL
    return AdvertisingDailyExpense(
        kind=kind,
        amount=amount,
        state=state,
        stale=source_day.stale if source_day else False,
        raw_types=tuple(
            sorted(
                {fact.raw_type for fact in facts}
                | {
                    campaign.adv_object_type
                    for campaign in snapshot.campaigns
                    if campaign_kind(campaign) == kind
                }
            )
        ),
    )


def spend_summary(
    period: AdvertisingPeriod,
    sources: tuple[AdvertisingSourceDay, ...],
    *,
    article: str,
    credentials_present: bool,
) -> AdvertisingSpendSummary:
    by_day = {(source.day, source.source): source for source in sources}
    days = []
    for day in period_days(period):
        expenses = tuple(
            daily_expense(
                kind,
                by_day.get((day, kind_source(kind))),
                article=article,
                credentials_present=credentials_present,
            )
            for kind in AdvertisingKind
        )
        known = tuple(expense.amount for expense in expenses if expense.amount is not None)
        required = tuple(
            expense for expense in expenses if expense.kind in OZON_ADVERTISING.required_spend_kinds
        )
        days.append(
            AdvertisingSpendDay(
                day=day,
                amount=sum(known, Decimal(0)) if known else None,
                breakdown=expenses,
                complete=all(
                    expense.state == AdvertisingState.READY and expense.amount is not None
                    for expense in required
                ),
                stale=any(expense.stale for expense in required),
            )
        )
    known_days = tuple(day.amount for day in days if day.amount is not None)
    return AdvertisingSpendSummary(
        period=period,
        amount=sum(known_days, Decimal(0)) if known_days else None,
        days=tuple(days),
        complete=all(day.complete for day in days),
        stale=any(day.stale for day in days),
        missing_dates=tuple(day.day for day in days if day.amount is None),
        partial_dates=tuple(day.day for day in days if day.amount is not None and not day.complete),
    )


def traffic_metrics(
    period: AdvertisingPeriod, sources: tuple[AdvertisingSourceDay, ...], *, article: str
) -> OzonTrafficMetrics:
    by_day = {source.day: source for source in sources if source.source == AdvertisingSource.CPC}
    dates: list[str] = []
    missing: list[str] = []
    partial: list[str] = []
    impressions = 0
    clicks = 0
    spend = Decimal(0)
    stale = False
    for day in period_days(period):
        source = by_day.get(day)
        snapshot = source.snapshot if source else None
        label = day.isoformat()
        if snapshot is None or not snapshot.traffic_collected:
            missing.append(label)
            continue
        facts = tuple(fact for fact in snapshot.facts if fact.kind == AdvertisingKind.CPC)
        matched = tuple(fact for fact in facts if fact.article == article)
        known = tuple(fact for fact in matched if fact.impressions is not None and fact.clicks is not None)
        unallocated = any(
            fact.article is None
            and (
                fact.amount
                or fact.impressions is None
                or fact.clicks is None
                or fact.impressions
                or fact.clicks
            )
            for fact in facts
        )
        incomplete = (
            len(known) != len(matched) or unallocated or AdvertisingKind.CPC in snapshot.unavailable_kinds
        )
        if not known and incomplete:
            missing.append(label)
            continue
        dates.append(label)
        if incomplete:
            partial.append(label)
        stale = stale or bool(source and source.stale)
        impressions += sum(fact.impressions or 0 for fact in known)
        clicks += sum(fact.clicks or 0 for fact in known)
        spend += sum((fact.amount for fact in known), Decimal(0))
    quantum = Decimal(OZON_ADVERTISING.money_quantum)
    return OzonTrafficMetrics(
        click_spend=spend if dates else None,
        impressions=impressions if dates else None,
        clicks=clicks if dates else None,
        ctr=float(
            (Decimal(clicks) / impressions * OZON_ADVERTISING.percent_factor).quantize(
                quantum, rounding=ROUND_HALF_UP
            )
        )
        if impressions
        else None,
        cpc=float((spend / clicks).quantize(quantum, rounding=ROUND_HALF_UP)) if clicks else None,
        coverage=OzonCoverage(
            dates=tuple(dates),
            days=len(dates),
            expected_days=len(period_days(period)),
            complete=not missing and not partial and not stale,
            missing_dates=tuple(missing),
            partial_dates=tuple(partial),
            stale=stale,
        ),
    )


def breakdown(
    kind: AdvertisingKind,
    period: AdvertisingPeriod,
    sources: tuple[AdvertisingSourceDay, ...],
    *,
    article: str | None,
    credentials_present: bool,
) -> AdvertisingBreakdown:
    by_day = {source.day: source for source in sources if source.source == kind_source(kind)}
    expenses = tuple(
        daily_expense(kind, by_day.get(day), article=article, credentials_present=credentials_present)
        for day in period_days(period)
    )
    known = tuple(value.amount for value in expenses if value.amount is not None)
    complete = all(value.state == AdvertisingState.READY for value in expenses)
    states = {value.state for value in expenses}
    state = AdvertisingState.READY if complete else AdvertisingState.PARTIAL
    if not known and len(states) == 1:
        state = next(iter(states))
    snapshots = tuple(source.snapshot for source in by_day.values() if source.snapshot is not None)
    return AdvertisingBreakdown(
        kind=kind,
        state=state,
        amount=sum(known, Decimal(0)) if known and (complete or article is None) else None,
        days=tuple(
            AdvertisingDay(day=day, amount=value.amount)
            for day, value in zip(period_days(period), expenses, strict=True)
        ),
        allocations=tuple(
            sorted(
                {
                    fact.allocation
                    for snapshot in snapshots
                    for fact in snapshot.facts
                    if fact.kind == kind and fact.article == article
                }
            )
        ),
        raw_types=tuple(sorted({raw for value in expenses for raw in value.raw_types})),
        synced_at=max((snapshot.synced_at for snapshot in snapshots), default=None),
        stale=any(value.stale for value in expenses),
    )


def window_adjustments(sources: tuple[AdvertisingSourceDay, ...]) -> tuple[AdvertisingWindowAdjustment, ...]:
    snapshots = {
        (source.source, source.snapshot.period.start, source.snapshot.period.end): source.snapshot
        for source in sources
        if source.snapshot is not None and (source.snapshot.corrections or source.snapshot.over_expenses)
    }
    return tuple(
        AdvertisingWindowAdjustment(
            source=snapshot.source,
            period=snapshot.period,
            corrections=snapshot.corrections,
            over_expenses=snapshot.over_expenses,
            synced_at=snapshot.synced_at,
        )
        for _, snapshot in sorted(snapshots.items())
    )
