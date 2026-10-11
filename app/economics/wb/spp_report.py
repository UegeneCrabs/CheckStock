"""Read-only WB SPP comparison for adjacent calendar periods."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

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


@dataclass(frozen=True, slots=True)
class SppOptions:
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


def previous_period(date_from: date, date_to: date) -> tuple[date, date]:
    duration = date_to - date_from + timedelta(days=1)
    return date_from - duration, date_from - timedelta(days=1)


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


def _latest_spp(stores: tuple[str, ...], date_from: date, date_to: date) -> dict[tuple[str, str, int], float]:
    if not stores:
        return {}
    previous_from, _ = previous_period(date_from, date_to)
    start = _utc_boundary(previous_from)
    split = _utc_boundary(date_from)
    end = _utc_boundary(date_to + timedelta(days=1))
    placeholders = ",".join("?" for _ in stores)
    with db.get_connection() as connection:
        rows = connection.execute(
            f"""SELECT store_slug, article, period, spp_percent FROM (
                    SELECT store_slug, article, spp_percent,
                           CASE WHEN observed_at < ? THEN 0 ELSE 1 END AS period,
                           ROW_NUMBER() OVER (
                               PARTITION BY store_slug, article,
                                            CASE WHEN observed_at < ? THEN 0 ELSE 1 END
                               ORDER BY observed_at DESC
                           ) AS rank
                      FROM wb_hourly_prices
                     WHERE store_slug IN ({placeholders})
                       AND observed_at >= ? AND observed_at < ?
                       AND spp_percent IS NOT NULL
                ) ranked WHERE rank=1""",
            (split, split, *stores, start, end),
        ).fetchall()
    return {
        (str(row["store_slug"]), str(row["article"]), int(row["period"])): float(row["spp_percent"])
        for row in rows
    }


def load(
    stores: tuple[str, ...],
    date_from: date,
    date_to: date,
    filters: SppFilters,
    *,
    manager_scope: str | None = None,
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
    options = SppOptions(
        brands=sorted({str(row["brand"]) for row in catalog if row.get("brand")}, key=str.casefold),
        managers=sorted({str(row["manager"]) for row in catalog if row.get("manager")}, key=str.casefold),
        subjects=sorted({str(row["subject"]) for row in catalog if row.get("subject")}, key=str.casefold),
        articles=sorted({str(row["article"]) for row in catalog}, key=str.casefold),
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

    spp = _latest_spp(stores, date_from, date_to)
    turnovers = {
        (str(row["store_slug"]), str(row["article"])): float(row["net_orders_amount"])
        for row in db.get_unit_economics_1c_funnel_order_totals(
            stores, date_from.isoformat(), date_to.isoformat()
        )
        if row.get("net_orders_amount") is not None
    }
    products: list[SppProduct] = []
    for row in catalog:
        key = (str(row["store_slug"]), str(row["article"]))
        current, previous = spp.get((*key, 1)), spp.get((*key, 0))
        status = (
            "no_data"
            if current is None or previous is None
            else "problem"
            if current < previous
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
                turnover=turnovers.get(key),
                current_spp=current,
                previous_spp=previous,
                status=status,
            )
        )
    _sort_products(products, sort_by, sort_order)
    problem = sum(item.status == "problem" for item in products)
    healthy = sum(item.status == "healthy" for item in products)
    comparable = problem + healthy
    previous_from, previous_to = previous_period(date_from, date_to)
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
        ),
        options=options,
        sort_by=sort_by,
        sort_order=sort_order,
    )
