from datetime import date

from sqlalchemy import func, select

from app.core.constants import OZON_NEWNESS, Marketplace
from app.dto.ozon_newness import OzonActivityStart, OzonNewness
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.orm import MarketplaceStockDailyHistoryRecord, SalesOrderLineRecord
from app.ozon.newness import classify


class OzonNewnessRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def load(self, stores: tuple[str, ...], today: date) -> dict[tuple[str, str], OzonNewness]:
        if not stores:
            return {}
        orders = table_for(SalesOrderLineRecord)
        stocks = table_for(MarketplaceStockDailyHistoryRecord)
        order_day = func.substr(orders.c.ordered_at, 1, 10)
        order_starts = (
            select(orders.c.store_slug, orders.c.article, func.min(order_day).label("day"))
            .where(
                orders.c.marketplace == Marketplace.OZON,
                orders.c.store_slug.in_(stores),
                orders.c.article.is_not(None),
                orders.c.article != "",
                orders.c.quantity > 0,
                order_day <= today.isoformat(),
            )
            .group_by(orders.c.store_slug, orders.c.article)
        )
        stock_starts = (
            select(stocks.c.store_slug, stocks.c.article, func.min(stocks.c.day).label("day"))
            .where(
                stocks.c.marketplace == Marketplace.OZON,
                stocks.c.store_slug.in_(stores),
                stocks.c.article != "",
                stocks.c.scheme.in_(OZON_NEWNESS.stock_schemes),
                stocks.c.quantity > 0,
                stocks.c.day <= today.isoformat(),
            )
            .group_by(stocks.c.store_slug, stocks.c.article)
        )
        async with self.database.transaction() as connection:
            order_rows = tuple(
                OzonActivityStart.model_validate(row)
                for row in (await connection.execute(order_starts)).mappings()
            )
            stock_rows = tuple(
                OzonActivityStart.model_validate(row)
                for row in (await connection.execute(stock_starts)).mappings()
            )
        first_orders = {(row.store_slug, row.article): row.day for row in order_rows}
        first_stocks = {(row.store_slug, row.article): row.day for row in stock_rows}
        return {
            key: classify(today, first_orders.get(key), first_stocks.get(key))
            for key in first_orders.keys() | first_stocks.keys()
        }
