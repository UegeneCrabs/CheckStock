from __future__ import annotations

import re
from calendar import monthrange
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from math import fsum
from typing import Protocol

from app.core.domain import MOSCOW_TIMEZONE
from app.dto.spp_report import (
    SppCatalogItem as SppCatalogItem,
)
from app.dto.spp_report import (
    SppDailyPoint as SppDailyPoint,
)
from app.dto.spp_report import SppDirection, SppDirectionFilter
from app.dto.spp_report import (
    SppFilters as SppFilters,
)
from app.dto.spp_report import (
    SppHistoryPeriod as SppHistoryPeriod,
)
from app.dto.spp_report import (
    SppHourlyHistory as SppHourlyHistory,
)
from app.dto.spp_report import (
    SppHourlyPoint as SppHourlyPoint,
)
from app.dto.spp_report import (
    SppOptions as SppOptions,
)
from app.dto.spp_report import (
    SppPeriod as SppPeriod,
)
from app.dto.spp_report import (
    SppProduct as SppProduct,
)
from app.dto.spp_report import (
    SppReportResult as SppReportResult,
)
from app.dto.spp_report import (
    SppSortField as SppSortField,
)
from app.dto.spp_report import (
    SppSortOrder as SppSortOrder,
)
from app.dto.spp_report import (
    SppSummary as SppSummary,
)
from app.dto.spp_report import (
    SppTurnover as SppTurnover,
)
from app.dto.spp_report import (
    _DailyPrice as _DailyPrice,
)
from app.dto.spp_report import (
    _HourlySnapshot as _HourlySnapshot,
)


def _weighted_average(values: list[tuple[float | None, float | None]]) -> float | None:
    available = [(value, weight) for value, weight in values if value is not None]
    weighted = [(value, weight) for value, weight in available if weight is not None and weight > 0]
    if weighted:
        return fsum(value * weight for value, weight in weighted) / fsum(weight for _, weight in weighted)
    return fsum(value for value, _ in available) / len(available) if available else None


def previous_period(date_from: date, date_to: date) -> tuple[date, date]:
    duration = date_to - date_from + timedelta(days=1)
    return date_from - duration, date_from - timedelta(days=1)


def history_dates(period: SppHistoryPeriod, today: date) -> tuple[date, date]:
    if period is SppHistoryPeriod.DAYS_7:
        return today - timedelta(days=6), today
    if period is SppHistoryPeriod.DAYS_30:
        return today - timedelta(days=29), today
    months = 6 if period is SppHistoryPeriod.MONTHS_6 else 12
    month_index = today.year * 12 + today.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    matching_day = date(year, month, min(today.day, monthrange(year, month)[1]))
    return matching_day + timedelta(days=1), today


def _utc_boundary(day: date) -> str:
    return datetime.combine(day, time.min, MOSCOW_TIMEZONE).astimezone(UTC).isoformat()


def _identity(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def _article_key(article: str) -> tuple[tuple[int, int | str], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in re.findall(r"\d+|\D+", article.casefold().replace("ё", "е"))
    )


def _sort_products(products: list[SppProduct], field: SppSortField, order: SppSortOrder) -> None:
    direction_order = {
        SppDirection.UNCHANGED: 0,
        SppDirection.NO_DATA: 1,
        SppDirection.INCREASE: 2,
        SppDirection.DECREASE: 3,
    }

    def value(product: SppProduct):
        if field is SppSortField.ARTICLE:
            return _article_key(product.article)
        if field is SppSortField.NAME:
            return product.name.casefold().replace("ё", "е")
        if field is SppSortField.STATUS:
            return direction_order[product.direction]
        return getattr(product, field.value)

    products.sort(key=lambda product: (product.store_slug.casefold(), _article_key(product.article)))
    known = [product for product in products if value(product) is not None]
    missing = [product for product in products if value(product) is None]
    known.sort(key=value, reverse=order is SppSortOrder.DESC)
    products[:] = [*known, *missing]


class SppSource(Protocol):
    def _catalog(self, stores: tuple[str, ...]) -> tuple[SppCatalogItem, ...]: ...
    def _daily_prices(
        self, stores: tuple[str, ...], date_from: date, date_to: date
    ) -> tuple[_DailyPrice, ...]: ...
    def _hourly_snapshots(self, stores: tuple[str, ...], day: date) -> tuple[_HourlySnapshot, ...]: ...
    def _open_days(self, stores: tuple[str, ...], date_from: date, date_to: date) -> tuple[date, ...]: ...
    def _turnover(self, stores: tuple[str, ...], date_from: str, date_to: str) -> tuple[SppTurnover, ...]: ...
    def _turnover_key(self, article: str) -> str: ...


def hourly_history(store: str, article: str, day: date, *, source: SppSource) -> SppHourlyHistory:
    snapshots = tuple(row for row in source._hourly_snapshots((store,), day) if row.article == article)
    by_hour = {
        datetime.fromisoformat(row.observed_at).astimezone(MOSCOW_TIMEZONE).hour: row for row in snapshots
    }
    return SppHourlyHistory(
        day=day.isoformat(),
        hours=tuple(
            SppHourlyPoint(
                hour=datetime.combine(day, time(hour=hour), MOSCOW_TIMEZONE).isoformat(),
                observed_at=row.observed_at if (row := by_hour.get(hour)) is not None else None,
                spp_percent=row.spp_percent if row is not None else None,
                customer_price_with_spp=row.customer_price_with_spp if row is not None else None,
                orders_turnover_rub=row.orders_turnover_rub if row is not None else None,
            )
            for hour in range(24)
        ),
        average_spp=_weighted_average([(row.spp_percent, row.orders_turnover_rub) for row in snapshots]),
        average_price=_weighted_average(
            [(row.customer_price_with_spp, row.orders_turnover_rub) for row in snapshots]
        ),
    )


def load(
    stores: tuple[str, ...],
    date_from: date,
    date_to: date,
    filters: SppFilters,
    *,
    source: SppSource,
    manager_scope: str | None = None,
    tolerance: float = 0,
    page: int = 1,
    page_size: int = 50,
    sort_by: SppSortField = SppSortField.TURNOVER,
    sort_order: SppSortOrder = SppSortOrder.DESC,
) -> SppReportResult:
    catalog = list(source._catalog(stores))
    if manager_scope is not None:
        catalog = [row for row in catalog if _identity(str(row.manager or "")) == _identity(manager_scope)]

    def facet(field: str) -> list[str]:
        selected = {
            "store_slug": filters.store,
            "brand": filters.brand,
            "manager": filters.manager,
            "subject": filters.subject,
        }
        matching = [
            row
            for row in catalog
            if all(
                not value or key == field or str(getattr(row, key) or "").casefold() == value.casefold()
                for key, value in selected.items()
            )
        ]
        return sorted({str(getattr(row, field)) for row in matching if getattr(row, field)}, key=str.casefold)

    options = SppOptions(
        stores=facet("store_slug"),
        brands=facet("brand"),
        managers=facet("manager"),
        subjects=facet("subject"),
        articles=facet("article"),
    )
    if filters.store:
        catalog = [row for row in catalog if row.store_slug == filters.store]
    for key, selected in (
        ("brand", filters.brand),
        ("manager", filters.manager),
        ("subject", filters.subject),
    ):
        if selected:
            catalog = [
                row for row in catalog if str(getattr(row, key) or "").casefold() == selected.casefold()
            ]
    if filters.article:
        catalog = [row for row in catalog if str(row.article).casefold() == filters.article.casefold()]
    if filters.search:
        term = filters.search.casefold()
        catalog = [
            row for row in catalog if term in str(row.article).casefold() or term in str(row.name).casefold()
        ]

    active_stores = (filters.store,) if filters.store and filters.store in stores else stores
    previous_from, previous_to = previous_period(date_from, date_to)
    open_current = set(source._open_days(active_stores, date_from, date_to))
    open_previous = set(source._open_days(active_stores, previous_from, previous_to))
    today_only = date_from == date_to == datetime.now(MOSCOW_TIMEZONE).date()
    hourly_today = today_only and date_from in open_current
    if today_only:
        open_current.discard(date_from)

    turnover_by_day = {
        (str(row.store_slug), str(row.article), row.day): float(row.net_orders_amount)
        for row in source._turnover(active_stores, previous_from.isoformat(), date_to.isoformat())
        if row.net_orders_amount is not None
    }
    period_values: dict[tuple[str, str, int], list[tuple[float | None, float | None]]] = defaultdict(list)
    for row in source._daily_prices(active_stores, previous_from, date_to):
        if row.day in open_current or row.day in open_previous:
            continue
        if today_only and row.day == date_from:
            continue
        period = 1 if date_from <= row.day <= date_to else 0
        period_values[(row.store_slug, row.article, period)].append(
            (row.spp_percent, turnover_by_day.get((row.store_slug, row.nm_id, row.day)))
        )
    today_snapshots = source._hourly_snapshots(active_stores, date_from) if today_only else ()
    if today_only:
        for row in today_snapshots:
            period_values[(row.store_slug, row.article, 1)].append((row.spp_percent, row.orders_turnover_rub))
    spp = {key: _weighted_average(values) for key, values in period_values.items()}
    turnovers: dict[tuple[str, str], float] = defaultdict(float)
    for (store_slug, nm_id, day), amount in turnover_by_day.items():
        if date_from <= day <= date_to and day not in open_current:
            turnovers[(store_slug, nm_id)] += amount
    if today_only:
        latest_hourly_turnover: dict[tuple[str, str], float] = {}
        for row in today_snapshots:
            if row.orders_turnover_rub is not None:
                latest_hourly_turnover[(row.store_slug, source._turnover_key(row.article))] = (
                    row.orders_turnover_rub
                )
        for key, amount in latest_hourly_turnover.items():
            turnovers.setdefault(key, amount)
    products: list[SppProduct] = []
    for row in catalog:
        key = (str(row.store_slug), str(row.article))
        current, previous = spp.get((*key, 1)), spp.get((*key, 0))
        nm_id = source._turnover_key(key[1])
        status = (
            "no_data"
            if current is None or previous is None
            else "problem"
            if current < previous - tolerance
            else "healthy"
        )
        direction = (
            SppDirection.NO_DATA
            if current is None or previous is None
            else SppDirection.INCREASE
            if current > previous
            else SppDirection.DECREASE
            if current < previous
            else SppDirection.UNCHANGED
        )
        if filters.direction != SppDirectionFilter.ALL and direction.value != filters.direction.value:
            continue
        products.append(
            SppProduct(
                store_slug=key[0],
                article=key[1],
                name=str(row.name),
                brand=row.brand,
                subject=row.subject,
                manager=row.manager,
                turnover=turnovers.get((key[0], nm_id)),
                current_spp=current,
                previous_spp=previous,
                status=status,
                direction=direction,
            )
        )
    _sort_products(products, sort_by, sort_order)
    problem = sum(item.status == "problem" for item in products)
    healthy = sum(item.status == "healthy" for item in products)
    comparable = problem + healthy
    return SppReportResult(
        rows=tuple(products[(page - 1) * page_size : page * page_size]),
        total=len(products),
        page=page,
        page_size=page_size,
        summary=SppSummary(
            problem=problem,
            healthy=healthy,
            no_data=len(products) - comparable,
            comparable=comparable,
            health_percent=round(healthy / comparable * 100) if comparable else None,
            increase=sum(item.direction == SppDirection.INCREASE for item in products),
            decrease=sum(item.direction == SppDirection.DECREASE for item in products),
            unchanged=sum(item.direction == SppDirection.UNCHANGED for item in products),
        ),
        period=SppPeriod(
            date_from=date_from.isoformat(),
            date_to=date_to.isoformat(),
            previous_from=previous_from.isoformat(),
            previous_to=previous_to.isoformat(),
            excluded_days=tuple(day.isoformat() for day in sorted(open_current)),
            previous_excluded_days=tuple(day.isoformat() for day in sorted(open_previous)),
            hourly_today=hourly_today,
        ),
        options=options,
        sort_by=sort_by,
        sort_order=sort_order,
    )
