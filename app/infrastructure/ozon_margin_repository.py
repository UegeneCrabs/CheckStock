from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.engine import ScalarResult

from app.core.constants import OZON_MARGIN, TIME, Marketplace
from app.dto.ozon_cabinet import OzonProductGoals
from app.dto.ozon_logistics import LogisticsData
from app.dto.ozon_margin import OzonMarginSnapshot, OzonMarginSource
from app.dto.ozon_prices import PriceValues
from app.dto.ozon_storage import StorageData
from app.economics import ozon_commissions, ozon_margin
from app.infrastructure.async_commissions_repository import AsyncCommissionsRepository
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.async_logistics_repository import AsyncLogisticsRepository
from app.infrastructure.async_prices_repository import AsyncPricesRepository
from app.infrastructure.async_storage_repository import AsyncStorageRepository
from app.infrastructure.orm import StockItemRecord, UnitEconomics1CSourceValueRecord
from app.infrastructure.ozon_cabinet_repository import OzonCabinetRepository
from app.infrastructure.ozon_calendar_repository import OzonCalendarRepository
from app.infrastructure.ozon_margin_orm import OzonMarginDailyRecord
from app.ozon.cabinet_normalizer import effective_parameters
from app.ozon.price_normalizer import price_freshness


class OzonMarginRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def current(self, stores: tuple[str, ...], now: datetime) -> tuple[OzonMarginSnapshot, ...]:
        stock = table_for(StockItemRecord)
        source = table_for(UnitEconomics1CSourceValueRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(
                    stock.c.store_slug,
                    stock.c.article,
                    stock.c.mp_product_id,
                    source.c.purchase_price,
                    source.c.fulfillment_cost,
                    source.c.team_commission_percent,
                )
                .select_from(stock.outerjoin(source, stock.c.id == source.c.stock_item_id))
                .where(
                    stock.c.store_slug.in_(stores),
                    stock.c.marketplace == Marketplace.OZON,
                    stock.c.is_service == 0,
                )
            )
            sources = tuple(OzonMarginSource.model_validate(row) for row in rows.mappings())
        day = now.astimezone(TIME.moscow_timezone).date()
        prices = {
            (v.store_slug, str(v.product_id)): price_freshness(v.prices, now)
            for v in await AsyncPricesRepository(self.database).load(stores)
        }
        commissions = {
            (v.store_slug, str(v.product_id)): v.value
            for v in await AsyncCommissionsRepository(self.database).load(stores)
        }
        logistics = {
            (v.store_slug, p.article): LogisticsData(
                snapshot=p, stale=p.period.day != day or bool(v.error), error=v.error
            )
            for v in await AsyncLogisticsRepository(self.database).latest(stores, day)
            for p in v.products
        }
        storage = {
            (v.store_slug, p.article): StorageData(
                snapshot=p, stale=p.period.day != day or bool(v.error), error=v.error
            )
            for v in await AsyncStorageRepository(self.database).latest(stores, day)
            for p in v.products
        }
        cabinets = OzonCabinetRepository(self.database)
        settings = {v.store_slug: v.values for v in await cabinets.settings(stores)}
        buyouts = {v.store_slug: v for v in await cabinets.buyouts(stores, day)}
        return tuple(
            ozon_margin.snapshot(
                item,
                price,
                ozon_commissions.snapshot(
                    commissions.get((item.store_slug, item.mp_product_id or "")), price.seller_price, now
                ),
                logistics.get((item.store_slug, item.article), LogisticsData()),
                storage.get((item.store_slug, item.article), StorageData()),
                effective_parameters(
                    settings[item.store_slug],
                    item.article,
                    None,
                    OzonProductGoals(),
                    buyouts.get(item.store_slug),
                    now,
                ),
                now,
            )
            for item in sources
            for price in (prices.get((item.store_slug, item.mp_product_id or ""), PriceValues()),)
        )

    async def history(
        self, stores: tuple[str, ...], start: date, end: date
    ) -> tuple[OzonMarginSnapshot, ...]:
        table = table_for(OzonMarginDailyRecord)
        async with self.database.transaction() as connection:
            payloads: ScalarResult[str] = (
                await connection.execute(
                    select(table.c.payload).where(
                        table.c.store_slug.in_(stores),
                        table.c.day.between(start.isoformat(), end.isoformat()),
                    )
                )
            ).scalars()
            snapshots = tuple(
                value
                for payload in payloads
                if (value := OzonMarginSnapshot.model_validate_json(payload)).version == OZON_MARGIN.version
            )
        return await OzonCalendarRepository(self.database).apply(snapshots)

    async def save(self, snapshots: tuple[OzonMarginSnapshot, ...], now: datetime) -> None:
        table = table_for(OzonMarginDailyRecord)
        day = now.astimezone(TIME.moscow_timezone).date()
        if any(
            item.day != day or item.observed_at.astimezone(TIME.moscow_timezone).date() != day
            for item in snapshots
        ):
            raise ValueError("Only current-day margin inputs may be saved")
        async with self.database.transaction(write=True) as connection:
            for item in snapshots:
                saved = (
                    await connection.execute(
                        select(table.c.observed_at).where(
                            table.c.store_slug == item.store_slug,
                            table.c.article == item.article,
                            table.c.day == day.isoformat(),
                        )
                    )
                ).scalar()
                if saved and datetime.fromisoformat(saved) >= item.observed_at:
                    continue
                await connection.execute(
                    upsert(
                        connection,
                        table,
                        {
                            "store_slug": item.store_slug,
                            "article": item.article,
                            "day": day.isoformat(),
                            "observed_at": item.observed_at.isoformat(),
                            "payload": item.model_dump_json(),
                        },
                        ("store_slug", "article", "day"),
                    )
                )
                await OzonCalendarRepository.observe(connection, item)
