from datetime import date

from sqlalchemy import case, func, select

from app.core.constants import Marketplace, StockScheme
from app.dto.ozon_advertising import AdvertisingPeriod
from app.dto.ozon_cabinet import OzonBuyoutSnapshot
from app.dto.ozon_economics import OzonProductsQuery
from app.dto.ozon_history import OzonHistoryEvidence, OzonHistoryQuery, OzonPriceDay, OzonStockDay
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.async_prices_repository import stored_price_values
from app.infrastructure.async_turnover_repository import AsyncTurnoverRepository
from app.infrastructure.orm import (
    FulfillmentStockDailyHistoryRecord,
    MarketplaceStockDailyHistoryRecord,
    StockItemRecord,
)
from app.infrastructure.ozon_cabinet_orm import OzonBuyoutDailyRecord
from app.infrastructure.ozon_prices_orm import OzonPriceDailyRecord


class OzonHistoryRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def load(self, query: OzonHistoryQuery, start: date, end: date) -> OzonHistoryEvidence:
        catalog = table_for(StockItemRecord)
        stocks = table_for(MarketplaceStockDailyHistoryRecord)
        fulfillment = table_for(FulfillmentStockDailyHistoryRecord)
        prices = table_for(OzonPriceDailyRecord)
        buyouts = table_for(OzonBuyoutDailyRecord)
        async with self.database.transaction() as connection:
            product = (
                await connection.execute(
                    select(catalog.c.mp_product_id).where(
                        catalog.c.store_slug == query.store,
                        catalog.c.marketplace == Marketplace.OZON,
                        catalog.c.article == query.article,
                        catalog.c.is_service == 0,
                    )
                )
            ).first()
            if product is None:
                raise LookupError("Product not found")
            stock_days = {
                value.day: value
                for row in (
                    await connection.execute(
                        select(
                            stocks.c.day,
                            func.sum(case((stocks.c.scheme == StockScheme.FBO, stocks.c.quantity))).label(
                                "fbo"
                            ),
                            func.sum(case((stocks.c.scheme == StockScheme.FBS, stocks.c.quantity))).label(
                                "fbs"
                            ),
                        )
                        .where(
                            stocks.c.store_slug == query.store,
                            stocks.c.marketplace == Marketplace.OZON,
                            stocks.c.article == query.article,
                            stocks.c.day.between(start.isoformat(), end.isoformat()),
                            stocks.c.scheme.in_((StockScheme.FBO, StockScheme.FBS)),
                        )
                        .group_by(stocks.c.day)
                    )
                ).mappings()
                for value in (OzonStockDay.model_validate(row),)
            }
            ff_rows = (
                await connection.execute(
                    select(fulfillment.c.day, func.sum(fulfillment.c.quantity).label("fulfillment"))
                    .where(
                        fulfillment.c.store_slug == query.store,
                        fulfillment.c.marketplace == Marketplace.OZON,
                        fulfillment.c.article == query.article,
                        fulfillment.c.day.between(start.isoformat(), end.isoformat()),
                    )
                    .group_by(fulfillment.c.day)
                )
            ).mappings()
            for row in ff_rows:
                value = OzonStockDay.model_validate(row)
                stock_days[value.day] = stock_days.get(value.day, value).model_copy(
                    update={"fulfillment": value.fulfillment}
                )
            price_days = tuple(
                OzonPriceDay(day=row.day, value=stored_price_values(row.last_payload).black_price)
                for row in await connection.execute(
                    select(prices.c.day, prices.c.last_payload).where(
                        prices.c.store_slug == query.store,
                        prices.c.product_id == product.mp_product_id,
                        prices.c.offer_id == query.article,
                        prices.c.day.between(start.isoformat(), end.isoformat()),
                    )
                )
            )
            buyout_days = tuple(
                OzonBuyoutSnapshot.model_validate_json(payload)
                for payload in await connection.scalars(
                    select(buyouts.c.payload).where(
                        buyouts.c.store_slug == query.store,
                        buyouts.c.day.between(start.isoformat(), end.isoformat()),
                    )
                )
            )
        turnover = await AsyncTurnoverRepository(self.database).load(
            OzonProductsQuery(stores=(query.store,), article=query.article, period_from=start, period_to=end)
        )
        advertising = await AsyncAdvertisingRepository(self.database).overlapping(
            (query.store,), AdvertisingPeriod(start=start, end=end)
        )
        return OzonHistoryEvidence(
            turnover=turnover,
            advertising=advertising,
            stocks=tuple(stock_days.values()),
            prices=price_days,
            buyouts=buyout_days,
        )
