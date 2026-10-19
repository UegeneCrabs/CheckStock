"""Read-only WB SPP comparison for adjacent calendar periods."""

from __future__ import annotations

import re
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from math import fsum

from app import db
from app.core.domain import MOSCOW_TIMEZONE


@dataclass(frozen=True, slots=True)
class SppFilters:
    store: str = ""
    brand: str = ""
    manager: str = ""
    subject: str = ""
    article: str = ""
    search: str = ""


class SppSortField(StrEnum):
    ARTICLE = "article"
    NAME = "name"
    TURNOVER = "turnover"
    CURRENT_SPP = "current_spp"
    PREVIOUS_SPP = "previous_spp"
    STATUS = "status"


class SppSortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class SppHistoryPeriod(StrEnum):
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    MONTHS_6 = "6m"
    YEAR_1 = "1y"


@dataclass(frozen=True, slots=True)
class SppProduct:
    store_slug: str
    article: str
    name: str
    brand: str | None
    subject: str | None
    manager: str | None
    turnover: float | None
    current_spp: float | None
    previous_spp: float | None
    status: str


@dataclass(frozen=True, slots=True)
class SppSummary:
    problem: int
    healthy: int
    no_data: int
    comparable: int
    health_percent: int | None


@dataclass(frozen=True, slots=True)
class SppPeriod:
    date_from: str
    date_to: str
    previous_from: str
    previous_to: str
    excluded_days: tuple[str, ...] = ()
    previous_excluded_days: tuple[str, ...] = ()
    hourly_today: bool = False


@dataclass(frozen=True, slots=True)
class SppOptions:
    stores: list[str]
    brands: list[str]
    managers: list[str]
    subjects: list[str]
    articles: list[str]


@dataclass(frozen=True, slots=True)
class SppReportResult:
    rows: tuple[SppProduct, ...]
    total: int
    page: int
    page_size: int
    summary: SppSummary
    period: SppPeriod
    options: SppOptions
    sort_by: SppSortField
    sort_order: SppSortOrder


@dataclass(frozen=True, slots=True)
class SppDailyPoint:
    date: str
    average_spp: float | None
    observations: int
    average_price: float | None = None
    turnover_rub: float | None = None
    open: bool = False


@dataclass(frozen=True, slots=True)
class SppHourlyPoint:
    hour: str
    observed_at: str | None
    spp_percent: float | None
    customer_price_with_spp: float | None
    orders_turnover_rub: float | None


@dataclass(frozen=True, slots=True)
class SppHourlyHistory:
    day: str
    hours: tuple[SppHourlyPoint, ...]
    average_spp: float | None
    average_price: float | None


@dataclass(frozen=True, slots=True)
class _DailyPrice:
    store_slug: str
    article: str
    nm_id: str
    day: date
    spp_percent: float | None


@dataclass(frozen=True, slots=True)
class _HourlySnapshot:
    store_slug: str
    article: str
    observed_at: str
    spp_percent: float | None
    customer_price_with_spp: float
    orders_turnover_rub: float | None


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
    status_labels = {
        "healthy": "Без проблем",
        "no_data": "Нет данных",
        "problem": "Требует внимания",
    }

    def value(product: SppProduct):
        if field is SppSortField.ARTICLE:
            return _article_key(product.article)
        if field is SppSortField.NAME:
            return product.name.casefold().replace("ё", "е")
        if field is SppSortField.STATUS:
            return status_labels[product.status].casefold().replace("ё", "е")
        return getattr(product, field.value)

    products.sort(key=lambda product: (product.store_slug.casefold(), _article_key(product.article)))
    known = [product for product in products if value(product) is not None]
    missing = [product for product in products if value(product) is None]
    known.sort(key=value, reverse=order is SppSortOrder.DESC)
    products[:] = [*known, *missing]


def _catalog(stores: tuple[str, ...]) -> list[dict]:
    if not stores:
        return []
    placeholders = ",".join("?" for _ in stores)
    with db.get_connection() as connection:
        rows = connection.execute(
            f"""SELECT items.store_slug, items.article, items.name,
                       categories.category AS subject, categories.brand, source.manager
                  FROM stock_items items
                  LEFT JOIN unit_economics_1c_product_categories categories
                    ON categories.stock_item_id=items.id
                  LEFT JOIN unit_economics_1c_source_values source
                    ON source.stock_item_id=items.id
                 WHERE items.marketplace='WB' AND items.is_service=0
                   AND items.store_slug IN ({placeholders})""",
            stores,
        ).fetchall()
    return [dict(row) for row in rows]


def _daily_prices(stores: tuple[str, ...], date_from: date, date_to: date) -> tuple[_DailyPrice, ...]:
    if not stores:
        return ()
    placeholders = ",".join("?" for _ in stores)
    with db.get_connection() as connection:
        rows = connection.execute(
            f"""SELECT store_slug, article, nm_id, observed_day, spp_percent
                  FROM wb_daily_prices
                 WHERE store_slug IN ({placeholders})
                   AND observed_day>=? AND observed_day<=?""",
            (*stores, date_from.isoformat(), date_to.isoformat()),
        ).fetchall()
    return tuple(
        _DailyPrice(
            store_slug=str(row["store_slug"]),
            article=str(row["article"]),
            nm_id=str(row["nm_id"]),
            day=date.fromisoformat(str(row["observed_day"])),
            spp_percent=float(row["spp_percent"]) if row["spp_percent"] is not None else None,
        )
        for row in rows
    )


def _hourly_snapshots(stores: tuple[str, ...], day: date) -> tuple[_HourlySnapshot, ...]:
    if not stores:
        return ()
    placeholders = ",".join("?" for _ in stores)
    with db.get_connection() as connection:
        rows = connection.execute(
            f"""SELECT store_slug, article, observed_at, spp_percent,
                       customer_price_with_spp, orders_turnover_rub
                  FROM wb_hourly_prices
                 WHERE store_slug IN ({placeholders})
                   AND observed_at>=? AND observed_at<?
                 ORDER BY observed_at""",
            (*stores, _utc_boundary(day), _utc_boundary(day + timedelta(days=1))),
        ).fetchall()
    return tuple(
        _HourlySnapshot(
            store_slug=str(row["store_slug"]),
            article=str(row["article"]),
            observed_at=str(row["observed_at"]),
            spp_percent=float(row["spp_percent"]) if row["spp_percent"] is not None else None,
            customer_price_with_spp=float(row["customer_price_with_spp"]),
            orders_turnover_rub=(
                float(row["orders_turnover_rub"]) if row["orders_turnover_rub"] is not None else None
            ),
        )
        for row in rows
    )


def _open_days(stores: tuple[str, ...], date_from: date, date_to: date) -> tuple[date, ...]:
    if not stores:
        return ()
    placeholders = ",".join("?" for _ in stores)
    with db.get_connection() as connection:
        rows = connection.execute(
            f"""SELECT observed_at FROM wb_hourly_prices
                 WHERE store_slug IN ({placeholders}) AND observed_at>=? AND observed_at<?""",
            (*stores, _utc_boundary(date_from), _utc_boundary(date_to + timedelta(days=1))),
        ).fetchall()
    return tuple(
        sorted(
            {
                datetime.fromisoformat(str(row["observed_at"])).astimezone(MOSCOW_TIMEZONE).date()
                for row in rows
            }
        )
    )


def daily_history(store: str, article: str, date_from: date, date_to: date) -> tuple[SppDailyPoint, ...]:
    """Show finalized daily prices; hourly dates belong to the separate chart."""
    open_days = set(_open_days((store,), date_from, date_to))
    with db.get_connection() as connection:
        daily = connection.execute(
            """SELECT observed_day, nm_id, spp_percent, customer_price_with_spp FROM wb_daily_prices
                 WHERE store_slug=? AND article=? AND observed_day>=? AND observed_day<=?""",
            (store, article, date_from.isoformat(), date_to.isoformat()),
        ).fetchall()
    by_day = {date.fromisoformat(str(row["observed_day"])): row for row in daily}
    daily_turnover = {
        (date.fromisoformat(str(row["day"])), str(row["article"])): float(row["net_orders_amount"])
        for row in db.get_unit_economics_1c_funnel_daily_order_rows(
            (store,), date_from.isoformat(), date_to.isoformat()
        )
        if row.get("net_orders_amount") is not None
    }
    return tuple(
        SppDailyPoint(
            date=day.isoformat(),
            average_spp=(
                float(by_day[day]["spp_percent"])
                if day not in open_days and day in by_day and by_day[day]["spp_percent"] is not None
                else None
            ),
            observations=(
                1 if day not in open_days and day in by_day and by_day[day]["spp_percent"] is not None else 0
            ),
            average_price=(
                float(by_day[day]["customer_price_with_spp"])
                if day not in open_days and day in by_day
                else None
            ),
            turnover_rub=(
                daily_turnover.get((day, str(by_day[day]["nm_id"])))
                if day not in open_days and day in by_day
                else None
            ),
            open=day in open_days,
        )
        for day in (date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1))
    )


def hourly_history(store: str, article: str, day: date) -> SppHourlyHistory:
    snapshots = tuple(row for row in _hourly_snapshots((store,), day) if row.article == article)
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
    manager_scope: str | None = None,
    tolerance: float = 0,
    page: int = 1,
    page_size: int = 50,
    sort_by: SppSortField = SppSortField.TURNOVER,
    sort_order: SppSortOrder = SppSortOrder.DESC,
) -> SppReportResult:
    catalog = _catalog(stores)
    if manager_scope is not None:
        catalog = [
            row for row in catalog if _identity(str(row.get("manager") or "")) == _identity(manager_scope)
        ]

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
                not value or key == field or str(row.get(key) or "").casefold() == value.casefold()
                for key, value in selected.items()
            )
        ]
        return sorted({str(row[field]) for row in matching if row.get(field)}, key=str.casefold)

    options = SppOptions(
        stores=facet("store_slug"),
        brands=facet("brand"),
        managers=facet("manager"),
        subjects=facet("subject"),
        articles=facet("article"),
    )
    if filters.store:
        catalog = [row for row in catalog if row["store_slug"] == filters.store]
    for key, selected in (
        ("brand", filters.brand),
        ("manager", filters.manager),
        ("subject", filters.subject),
    ):
        if selected:
            catalog = [row for row in catalog if str(row.get(key) or "").casefold() == selected.casefold()]
    if filters.article:
        catalog = [row for row in catalog if str(row["article"]).casefold() == filters.article.casefold()]
    if filters.search:
        term = filters.search.casefold()
        catalog = [
            row
            for row in catalog
            if term in str(row["article"]).casefold() or term in str(row["name"]).casefold()
        ]

    active_stores = (filters.store,) if filters.store and filters.store in stores else stores
    previous_from, previous_to = previous_period(date_from, date_to)
    open_current = set(_open_days(active_stores, date_from, date_to))
    open_previous = set(_open_days(active_stores, previous_from, previous_to))
    today_only = date_from == date_to == datetime.now(MOSCOW_TIMEZONE).date()
    hourly_today = today_only and date_from in open_current
    if today_only:
        open_current.discard(date_from)

    turnover_by_day = {
        (str(row["store_slug"]), str(row["article"]), date.fromisoformat(str(row["day"]))): float(
            row["net_orders_amount"]
        )
        for row in db.get_unit_economics_1c_funnel_daily_order_rows(
            active_stores, previous_from.isoformat(), date_to.isoformat()
        )
        if row.get("net_orders_amount") is not None
    }
    period_values: dict[tuple[str, str, int], list[tuple[float | None, float | None]]] = defaultdict(list)
    for row in _daily_prices(active_stores, previous_from, date_to):
        if row.day in open_current or row.day in open_previous:
            continue
        if today_only and row.day == date_from:
            continue
        period = 1 if date_from <= row.day <= date_to else 0
        period_values[(row.store_slug, row.article, period)].append(
            (row.spp_percent, turnover_by_day.get((row.store_slug, row.nm_id, row.day)))
        )
    today_snapshots = _hourly_snapshots(active_stores, date_from) if today_only else ()
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
                latest_hourly_turnover[(row.store_slug, row.article.partition(" / ")[0].strip())] = (
                    row.orders_turnover_rub
                )
        for key, amount in latest_hourly_turnover.items():
            turnovers.setdefault(key, amount)
    products: list[SppProduct] = []
    for row in catalog:
        key = (str(row["store_slug"]), str(row["article"]))
        current, previous = spp.get((*key, 1)), spp.get((*key, 0))
        nm_id = key[1].partition(" / ")[0].strip()
        status = (
            "no_data"
            if current is None or previous is None
            else "problem"
            if current < previous - tolerance
            else "healthy"
        )
        products.append(
            SppProduct(
                store_slug=key[0],
                article=key[1],
                name=str(row["name"]),
                brand=row.get("brand"),
                subject=row.get("subject"),
                manager=row.get("manager"),
                turnover=turnovers.get((key[0], nm_id)),
                current_spp=current,
                previous_spp=previous,
                status=status,
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
