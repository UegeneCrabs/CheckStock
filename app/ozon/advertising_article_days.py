from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.core.constants import (
    OZON_ADVERTISING,
    AdvertisingAllocation,
    AdvertisingError,
    AdvertisingKind,
    AdvertisingSource,
)
from app.dto.ozon_advertising import (
    AdvertisingCorrection,
    AdvertisingFact,
    AdvertisingPeriod,
    AdvertisingSnapshot,
    AllProductsOrdersReport,
)
from app.ozon.advertising_normalizer import campaign_kind, period_days

ARTICLE_SOURCES = {
    AdvertisingSource.ALL_PRODUCTS: AdvertisingKind.CPO_ALL,
    AdvertisingSource.CPC: AdvertisingKind.CPC,
}


def kopecks(amount: Decimal) -> int:
    value = amount / Decimal(OZON_ADVERTISING.money_quantum)
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError(AdvertisingError.ALLOCATION_PRECISION)
    return int(value)


@dataclass(slots=True)
class ArticleAmounts:
    spend_kopecks: int = 0
    impressions: int = 0
    clicks: int = 0
    traffic_complete: bool = True

    def add(self, fact: AdvertisingFact, cents: int, collected: bool) -> None:
        self.spend_kopecks += cents
        if fact.kind == AdvertisingKind.CPC:
            self.traffic_complete &= collected and fact.impressions is not None and fact.clicks is not None
            self.impressions += fact.impressions or 0
            self.clicks += fact.clicks or 0


@dataclass(frozen=True, slots=True, kw_only=True)
class DayContext:
    period: AdvertisingPeriod
    traffic_collected: bool
    campaign_id: str | None
    finalized: bool
    corrections: tuple[AdvertisingCorrection, ...]


@dataclass(slots=True, kw_only=True)
class ArticleDay:
    day: date
    articles: dict[str, ArticleAmounts] = field(default_factory=dict)
    context: DayContext | None = None
    source_rows: int = 0
    unallocated_kopecks: int = 0
    exceptions: tuple[AdvertisingFact, ...] = ()


def article_days(snapshot: AdvertisingSnapshot) -> tuple[ArticleDay, ...]:
    expected = ARTICLE_SOURCES.get(snapshot.source)
    if expected is None or expected in snapshot.unavailable_kinds:
        raise ValueError(AdvertisingError.REPORT_INCOMPLETE)
    context = DayContext(
        period=snapshot.period,
        traffic_collected=snapshot.traffic_collected,
        campaign_id=next((c.id for c in snapshot.campaigns if campaign_kind(c) == AdvertisingKind.CPC), None),
        finalized=snapshot.finalized,
        corrections=snapshot.corrections,
    )
    days = {
        day: ArticleDay(
            day=day,
            context=context
            if not snapshot.over_expenses
            and not (snapshot.source == AdvertisingSource.ALL_PRODUCTS and snapshot.campaigns)
            else None,
        )
        for day in period_days(snapshot.period)
    }
    raw_type = (
        OZON_ADVERTISING.sku_type if snapshot.source == AdvertisingSource.CPC else OZON_ADVERTISING.all_type
    )
    exceptional: set[tuple[date, str | None]] = set()
    for fact in snapshot.facts:
        if fact.day not in days or fact.kind != expected:
            raise ValueError(AdvertisingError.INVALID_RESPONSE)
        day = days[fact.day]
        if (
            not fact.article
            or fact.allocation != AdvertisingAllocation.DIRECT
            or fact.raw_type != raw_type
            or (
                snapshot.source == AdvertisingSource.CPC and (fact.clicks is None or fact.impressions is None)
            )
        ):
            exceptional.add((fact.day, fact.article))
        day.source_rows += 1
        cents = kopecks(fact.amount)
        if fact.article:
            day.articles.setdefault(fact.article, ArticleAmounts()).add(
                fact, cents, snapshot.traffic_collected
            )
        else:
            day.unallocated_kopecks += cents
    if exceptional:
        groups: dict[
            tuple[date, str | None, str, AdvertisingAllocation, bool, bool, bool], AdvertisingFact
        ] = {}
        for fact in snapshot.facts:
            if (fact.day, fact.article) not in exceptional:
                continue
            key = (
                fact.day,
                fact.article,
                fact.raw_type,
                fact.allocation,
                fact.clicks is None,
                fact.impressions is None,
                fact.amount < 0,
            )
            previous = groups.get(key)
            groups[key] = (
                fact
                if previous is None
                else fact.model_copy(
                    update={
                        "amount": previous.amount + fact.amount,
                        "impressions": None
                        if fact.impressions is None
                        else (previous.impressions or 0) + fact.impressions,
                        "clicks": None if fact.clicks is None else (previous.clicks or 0) + fact.clicks,
                    }
                )
            )
        for day in days.values():
            day.exceptions = tuple(fact for fact in groups.values() if fact.day == day.day)
    return tuple(days.values())


def attach_report_articles(
    snapshot: AdvertisingSnapshot, report: AllProductsOrdersReport
) -> AdvertisingSnapshot:
    if snapshot.source != AdvertisingSource.ALL_PRODUCTS:
        raise ValueError(AdvertisingError.INVALID_RESPONSE)
    candidates: dict[str, set[str]] = {}
    for row in report.rows:
        if row.advertised_sku == row.purchased_sku and row.vendor_code and row.vendor_code.strip():
            candidates.setdefault(row.advertised_sku, set()).add(row.vendor_code.strip())
    for fact in snapshot.facts:
        if fact.advertised_sku and fact.article:
            candidates.setdefault(fact.advertised_sku, set()).add(fact.article)
    unique = {sku: next(iter(articles)) for sku, articles in candidates.items() if len(articles) == 1}
    facts = tuple(
        fact.model_copy(
            update={"article": unique[fact.advertised_sku], "allocation": AdvertisingAllocation.DIRECT}
        )
        if not fact.article
        and fact.advertised_sku is not None
        and fact.advertised_sku == fact.purchased_sku
        and fact.advertised_sku in unique
        else fact
        for fact in snapshot.facts
    )
    return snapshot.model_copy(update={"facts": facts})
