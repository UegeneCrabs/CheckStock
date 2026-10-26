"""Read-only WB product rating comparison from daily snapshots."""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from app import db
from app.economics.wb.spp_report import _article_key, previous_period


class RatingSortField(StrEnum):
    ARTICLE = "article"
    NAME = "name"
    LATEST_RATING = "latest_rating"
    REVIEWS_COUNT = "reviews_count"
    CURRENT_RATING = "current_rating"
    PREVIOUS_RATING = "previous_rating"
    CHANGE = "change"
    STATUS = "status"


class RatingSortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True)
class RatingFilters:
    store: str = ""
    brand: str = ""
    manager: str = ""
    subject: str = ""
    article: str = ""
    search: str = ""


@dataclass(frozen=True, slots=True)
class RatingProduct:
    store_slug: str
    nm_id: str
    article: str
    name: str
    brand: str | None
    manager: str | None
    subject: str | None
    latest_rating: float | None
    reviews_count: int | None
    latest_observed_at: str | None
    current_rating: float | None
    previous_rating: float | None
    change: float | None
    status: str


@dataclass(frozen=True, slots=True)
class RatingSummary:
    problem: int
    healthy: int
    no_data: int


@dataclass(frozen=True, slots=True)
class RatingPeriod:
    date_from: str
    date_to: str
    previous_from: str
    previous_to: str


@dataclass(frozen=True, slots=True)
class RatingOptions:
    stores: list[str]
    brands: list[str]
    managers: list[str]
    subjects: list[str]
    articles: list[str]


@dataclass(frozen=True, slots=True)
class RatingResult:
    rows: tuple[RatingProduct, ...]
    total: int
    page: int
    page_size: int
    summary: RatingSummary
    period: RatingPeriod
    options: RatingOptions
    sort_by: RatingSortField
    sort_order: RatingSortOrder


@dataclass(frozen=True, slots=True)
class RatingDay:
    date: str
    rating: float | None
    reviews_count: int | None


@dataclass(frozen=True, slots=True)
class _CatalogProduct:
    store_slug: str
    article: str
    name: str
    subject: str | None
    brand: str | None
    manager: str | None


@dataclass(frozen=True, slots=True)
class _LatestRating:
    rating: float | None
    reviews_count: int | None
    observed_at: str | None


def _nm_id(article: str) -> str:
    return article.partition(" / ")[0].strip()


def _catalog(stores: tuple[str, ...]) -> list[_CatalogProduct]:
    if not stores:
        return []
    marks = ",".join("?" for _ in stores)
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
                   AND items.store_slug IN ({marks})""",
            stores,
        ).fetchall()
    return [
        _CatalogProduct(
            store_slug=str(row["store_slug"]),
            article=str(row["article"]),
            name=str(row["name"]),
            subject=str(row["subject"]) if row["subject"] is not None else None,
            brand=str(row["brand"]) if row["brand"] is not None else None,
            manager=str(row["manager"]) if row["manager"] is not None else None,
        )
        for row in rows
    ]


def _latest(stores: tuple[str, ...]) -> dict[tuple[str, str], _LatestRating]:
    if not stores:
        return {}
    marks = ",".join("?" for _ in stores)
    with db.get_connection() as connection:
        rows = connection.execute(
            f"""SELECT store_slug, nm_id, rating, reviews_count, synced_at
                  FROM unit_economics_1c_product_reputation
                 WHERE store_slug IN ({marks})""",
            stores,
        ).fetchall()
    return {
        (str(row["store_slug"]), str(row["nm_id"])): _LatestRating(
            float(row["rating"]) if row["rating"] is not None and float(row["rating"]) > 0 else None,
            int(row["reviews_count"]) if row["reviews_count"] is not None else None,
            str(row["synced_at"]),
        )
        for row in rows
    }


def load(
    stores: tuple[str, ...],
    date_from: date,
    date_to: date,
    filters: RatingFilters,
    *,
    tolerance: float = 0,
    page: int = 1,
    page_size: int = 50,
    sort_by: RatingSortField = RatingSortField.CHANGE,
    sort_order: RatingSortOrder = RatingSortOrder.ASC,
) -> RatingResult:
    catalog = _catalog(stores)

    def matching(row: _CatalogProduct, *, skip: str = "", include_article: bool = False) -> bool:
        selected = {
            "store_slug": filters.store,
            "brand": filters.brand,
            "manager": filters.manager,
            "subject": filters.subject,
            "article": filters.article if include_article else "",
        }
        return all(
            not value or key == skip or str(getattr(row, key) or "").casefold() == value.casefold()
            for key, value in selected.items()
        )

    def facet(field: str) -> list[str]:
        return sorted(
            {
                str(getattr(row, field))
                for row in catalog
                if getattr(row, field) and matching(row, skip=field)
            },
            key=str.casefold,
        )

    options = RatingOptions(
        facet("store_slug"), facet("brand"), facet("manager"), facet("subject"), facet("article")
    )
    catalog = [row for row in catalog if matching(row, include_article=True)]
    if filters.search:
        term = filters.search.casefold()
        catalog = [row for row in catalog if term in row.article.casefold() or term in row.name.casefold()]
    by_card: dict[tuple[str, str], _CatalogProduct] = {}
    for row in catalog:
        key = (row.store_slug, _nm_id(row.article))
        by_card.setdefault(key, row)
    prev_from, prev_to = previous_period(date_from, date_to)
    # Ordered daily rows replace earlier days within each period.
    ratings = _period_ratings(stores, prev_from, date_from, date_to)
    latest = _latest(stores)
    products = []
    for (store, nm_id), row in by_card.items():
        current = ratings.get((store, nm_id, 1))
        previous = ratings.get((store, nm_id, 0))
        change = round(current - previous, 2) if current is not None and previous is not None else None
        status = "no_data" if change is None else "problem" if change < -tolerance else "healthy"
        fresh = latest.get((store, nm_id), _LatestRating(None, None, None))
        products.append(
            RatingProduct(
                store,
                nm_id,
                row.article,
                row.name,
                row.brand,
                row.manager,
                row.subject,
                fresh.rating,
                fresh.reviews_count,
                fresh.observed_at,
                current,
                previous,
                change,
                status,
            )
        )

    def sort_value(item: RatingProduct):
        if sort_by is RatingSortField.ARTICLE:
            return _article_key(item.article)
        if sort_by is RatingSortField.STATUS:
            return {"healthy": "Без проблем", "no_data": "Нет данных", "problem": "Требует внимания"}[
                item.status
            ]
        return getattr(item, sort_by.value)

    products.sort(key=lambda item: (item.store_slug.casefold(), _article_key(item.article)))
    known = [item for item in products if sort_value(item) is not None]
    missing = [item for item in products if sort_value(item) is None]
    known.sort(key=sort_value, reverse=sort_order is RatingSortOrder.DESC)
    products = [*known, *missing]
    return RatingResult(
        rows=tuple(products[(page - 1) * page_size : page * page_size]),
        total=len(products),
        page=page,
        page_size=page_size,
        summary=RatingSummary(
            sum(item.status == "problem" for item in products),
            sum(item.status == "healthy" for item in products),
            sum(item.status == "no_data" for item in products),
        ),
        period=RatingPeriod(
            date_from.isoformat(), date_to.isoformat(), prev_from.isoformat(), prev_to.isoformat()
        ),
        options=options,
        sort_by=sort_by,
        sort_order=sort_order,
    )


def _period_ratings(
    stores: tuple[str, ...], first: date, current_from: date, last: date
) -> dict[tuple[str, str, int], float]:
    if not stores:
        return {}
    marks = ",".join("?" for _ in stores)
    with db.get_connection() as connection:
        rows = connection.execute(
            f"""SELECT store_slug, nm_id, day, rating FROM wb_product_rating_daily
                 WHERE store_slug IN ({marks}) AND day>=? AND day<=? ORDER BY day""",
            (*stores, first.isoformat(), last.isoformat()),
        ).fetchall()
    result = {}
    for row in rows:
        result[
            (str(row["store_slug"]), str(row["nm_id"]), int(str(row["day"]) >= current_from.isoformat()))
        ] = float(row["rating"])
    return result


def belongs_to_catalog(store: str, nm_id: str) -> bool:
    return any(_nm_id(row.article) == nm_id for row in _catalog((store,)))


def daily_history(store: str, nm_id: str, date_from: date, date_to: date) -> tuple[RatingDay, ...]:
    with db.get_connection() as connection:
        rows = connection.execute(
            """SELECT day, rating, reviews_count FROM wb_product_rating_daily
                 WHERE store_slug=? AND nm_id=? AND day>=? AND day<=?""",
            (store, nm_id, date_from.isoformat(), date_to.isoformat()),
        ).fetchall()
    by_day = {str(row["day"]): row for row in rows}
    return tuple(
        RatingDay(
            day.isoformat(),
            float(row["rating"]) if (row := by_day.get(day.isoformat())) is not None else None,
            int(row["reviews_count"]) if row is not None and row["reviews_count"] is not None else None,
        )
        for day in (date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1))
    )
