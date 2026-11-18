from datetime import date

from app import db
from app.dto.spp_report import SppCatalogItem, SppTurnover
from app.economics import spp_report as shared
from app.economics.spp_report import (
    SppDailyPoint as SppDailyPoint,
)
from app.economics.spp_report import (
    SppFilters as SppFilters,
)
from app.economics.spp_report import (
    SppHistoryPeriod as SppHistoryPeriod,
)
from app.economics.spp_report import (
    SppHourlyHistory as SppHourlyHistory,
)
from app.economics.spp_report import (
    SppHourlyPoint as SppHourlyPoint,
)
from app.economics.spp_report import (
    SppOptions as SppOptions,
)
from app.economics.spp_report import (
    SppPeriod as SppPeriod,
)
from app.economics.spp_report import (
    SppProduct as SppProduct,
)
from app.economics.spp_report import (
    SppReportResult as SppReportResult,
)
from app.economics.spp_report import (
    SppSortField as SppSortField,
)
from app.economics.spp_report import (
    SppSortOrder as SppSortOrder,
)
from app.economics.spp_report import (
    SppSummary as SppSummary,
)
from app.economics.spp_report import (
    _article_key as _article_key,
)
from app.economics.spp_report import (
    _DailyPrice as _DailyPrice,
)
from app.economics.spp_report import (
    _HourlySnapshot as _HourlySnapshot,
)
from app.economics.spp_report import (
    _identity as _identity,
)
from app.economics.spp_report import (
    _sort_products as _sort_products,
)
from app.economics.spp_report import (
    _utc_boundary as _utc_boundary,
)
from app.economics.spp_report import (
    _weighted_average as _weighted_average,
)
from app.economics.spp_report import (
    history_dates as history_dates,
)
from app.economics.spp_report import (
    previous_period as previous_period,
)
from app.infrastructure.spp_report_repository import SppHistoryReader


def _catalog(stores: tuple[str, ...]) -> tuple[SppCatalogItem, ...]:
    if not stores:
        return ()
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
    return tuple(SppCatalogItem(**dict(row)) for row in rows)


def _turnover(stores: tuple[str, ...], date_from: str, date_to: str) -> tuple[SppTurnover, ...]:
    return tuple(
        SppTurnover(
            str(row["store_slug"]),
            str(row["article"]),
            date.fromisoformat(row["day"]),
            row.get("net_orders_amount"),
        )
        for row in db.get_unit_economics_1c_funnel_daily_order_rows(stores, date_from, date_to)
    )


def _turnover_key(article: str) -> str:
    return article.partition(" / ")[0].strip()


def hourly_history(store: str, article: str, day: date) -> SppHourlyHistory:
    return shared.hourly_history(store, article, day, source=_source)


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
    return shared.load(
        stores,
        date_from,
        date_to,
        filters,
        source=_source,
        manager_scope=manager_scope,
        tolerance=tolerance,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )


_reader = SppHistoryReader(yandex=False, turnover=_turnover)
_daily_prices = _reader._daily_prices
_hourly_snapshots = _reader._hourly_snapshots
_open_days = _reader._open_days
daily_history = _reader.daily_history


class WbSppSource:
    _catalog = staticmethod(_catalog)
    _turnover = staticmethod(_turnover)
    _turnover_key = staticmethod(_turnover_key)
    _daily_prices = staticmethod(_daily_prices)
    _hourly_snapshots = staticmethod(_hourly_snapshots)
    _open_days = staticmethod(_open_days)


_source = WbSppSource()
