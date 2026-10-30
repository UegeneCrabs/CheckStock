from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.core.constants import YANDEX_ANALYTICS
from app.dto.yandex_analytics import (
    CartSource,
    Comment,
    Coverage,
    CurrentEconomics,
    DailyMetric,
    DayNote,
    DSettings,
    EconomicsDay,
    MetricQuality,
    Prices,
    Product,
    RatioParts,
    Reference,
    WeeklyStock,
)


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def days(start: date, end: date) -> tuple[str, ...]:
    return tuple((start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1))


def total(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def ratio(numerator: float | None, denominator: float | None, *, zero: bool = False) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return 0.0 if zero and numerator == 0 else None
    return round(numerator / denominator * 100, 2)


def coverage(expected: tuple[str, ...], known: tuple[str, ...], *, complete: bool = True) -> Coverage:
    return Coverage(
        expected=len(expected),
        known=known,
        missing=tuple(d for d in expected if d not in known),
        complete=complete and len(known) == len(expected),
    )


def metric_quality(
    expected: tuple[str, ...],
    known: tuple[str, ...],
    *,
    complete: bool = True,
    stale: bool = False,
) -> MetricQuality:
    complete = complete and all(day in known for day in expected)
    return MetricQuality(
        complete=complete,
        stale=stale,
        reasons=(("partial",) if known else ("source_missing",)) if not complete else (),
        expected_dates=expected,
        known_dates=known,
    )


@dataclass(slots=True, kw_only=True)
class ProductSources:
    product: Product
    project: str
    reference: Reference = field(default_factory=Reference)
    category: str = ""
    prices: Prices = field(default_factory=Prices)
    rating: float | None = None
    orders: dict[str, DailyMetric] = field(default_factory=dict)
    ads: dict[str, DailyMetric] = field(default_factory=dict)
    funnel: dict[str, DailyMetric] = field(default_factory=dict)
    economy: dict[str, EconomicsDay] = field(default_factory=dict)
    current: CurrentEconomics = field(default_factory=CurrentEconomics)
    notes: tuple[DayNote, ...] = ()


@dataclass(slots=True, kw_only=True)
class StoreSources:
    slug: str
    products: dict[str, ProductSources] = field(default_factory=dict)
    orders_loaded: set[str] = field(default_factory=set)
    ads_loaded: set[str] = field(default_factory=set)
    funnel_loaded: set[str] = field(default_factory=set)
    funnel_metadata: dict[str, CartSource] = field(default_factory=dict)
    funnel_error: str | None = None
    orders_error: str | None = None
    ads_error: str | None = None
    coefficients: DSettings = field(default_factory=DSettings)
    stock: dict[str, WeeklyStock] = field(default_factory=dict)
    comments: tuple[Comment, ...] = ()
    first_day: str | None = None
    last_day: str | None = None
    orders_updated_at: str | None = None


@dataclass(slots=True, kw_only=True)
class Sources:
    stores: tuple[StoreSources, ...]
    today: date


def order_value(
    source: ProductSources, store: StoreSources, day: str, field: str, today: date
) -> float | None:
    if day > today.isoformat():
        return None
    row = source.orders.get(day)
    if row is not None:
        value = getattr(row, field)
        return float(value) if value is not None else None
    return 0.0 if day in store.orders_loaded else None


def ad_value(source: ProductSources, store: StoreSources, day: str, field: str, today: date) -> float | None:
    if day > today.isoformat():
        return None
    row = source.ads.get(day)
    if row is not None:
        value = getattr(row, field)
        return float(value) if value is not None else None
    return 0.0 if day in store.ads_loaded else None


def cart_source(source: ProductSources, store: StoreSources, day: str) -> CartSource:
    metric = source.funnel.get(day)
    saved = store.funnel_metadata.get(day)
    verified = (
        metric is not None and metric.source_scope == "report_date" and metric.source_date_origin is not None
    )
    known = verified and metric is not None and metric.cart_count is not None
    return CartSource(
        source_date=date.fromisoformat(day),
        received_at=metric.received_at if metric is not None else saved.received_at if saved else None,
        known=known,
        stale=bool(store.funnel_error),
        reason="source_error"
        if store.funnel_error
        else "source_date_unverified"
        if metric is not None and not verified
        else "field_missing"
        if metric is not None and not known
        else "source_missing"
        if metric is None
        else "source_timezone_unknown",
        source_date_origin=metric.source_date_origin
        if metric is not None
        else saved.source_date_origin
        if saved
        else None,
        source_date_parse_mode=metric.source_date_parse_mode if metric else None,
        source_date_components=metric.source_date_components if metric else None,
    )


def drr_parts(
    source: ProductSources, store: StoreSources, selected: tuple[str, ...], today: date
) -> RatioParts:
    numerators: list[float | None] = []
    denominators: list[float | None] = []
    known: list[str] = []
    for day in selected:
        spend = ad_value(source, store, day, "spend", today)
        amount = order_value(source, store, day, "orders_amount", today)
        saved = source.economy.get(day)
        buyout = saved.buyout if saved else None
        if spend is not None and amount is not None and buyout is not None:
            numerators.append(spend)
            denominators.append(amount * buyout / 100)
            known.append(day)
    numerator, denominator = total(numerators), total(denominators)
    source_complete = all(day in store.orders_loaded and day in store.ads_loaded for day in selected)
    return RatioParts(
        numerator=numerator,
        denominator=denominator,
        value=ratio(numerator, denominator, zero=True),
        coverage=coverage(selected, tuple(known), complete=source_complete),
        quality=metric_quality(
            selected,
            tuple(known),
            complete=source_complete,
            stale=bool(store.orders_error or store.ads_error),
        ),
    )


def sort_key(project: str, name: str, article: str, value: float | None) -> tuple[bool, float, str, str, str]:
    return value is None, -(value or 0), project, name.casefold(), article


def week_end(week: date, today: date) -> date:
    return min(today, week + timedelta(days=YANDEX_ANALYTICS.week_days - 1))
