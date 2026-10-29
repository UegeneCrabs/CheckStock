from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.constants import TIME, DatabaseDialect, Marketplace
from app.core.stores import STORES
from app.dto.ozon_economics import OzonProductsQuery
from app.dto.ozon_reports import (
    OzonProfitQuery,
    OzonReportCatalogItem,
    OzonReportEvidence,
    OzonReportIdentity,
    OzonTargetConflict,
    OzonTargetRecord,
    OzonTargetSave,
)
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.async_turnover_repository import AsyncTurnoverRepository
from app.infrastructure.orm import (
    FulfillmentStockDailyHistoryRecord,
    MarketplaceStockDailyHistoryRecord,
    StockItemRecord,
    UnitEconomics1CProductCategoryRecord,
    UnitEconomics1CProductSettingRecord,
    UnitEconomics1CSourceValueRecord,
)
from app.infrastructure.ozon_economics_repository import OzonEconomicsRepository


class OzonReportsRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def catalog(self, stores: tuple[str, ...]) -> tuple[OzonReportCatalogItem, ...]:
        catalog = table_for(StockItemRecord)
        category = table_for(UnitEconomics1CProductCategoryRecord)
        source = table_for(UnitEconomics1CSourceValueRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(
                    catalog.c.store_slug,
                    catalog.c.article,
                    catalog.c.name,
                    catalog.c.image_url,
                    category.c.category.label("subject"),
                    source.c.manager,
                )
                .select_from(
                    catalog.outerjoin(category, category.c.stock_item_id == catalog.c.id).outerjoin(
                        source, source.c.stock_item_id == catalog.c.id
                    )
                )
                .where(
                    catalog.c.marketplace == Marketplace.OZON,
                    catalog.c.store_slug.in_(stores),
                    catalog.c.is_service == 0,
                )
            )
            return tuple(
                OzonReportCatalogItem(**row, store_name=str(STORES[row["store_slug"]]["name"]))
                for row in rows.mappings()
            )

    async def has_product(self, store: str, article: str) -> bool:
        table = table_for(StockItemRecord)
        async with self.database.transaction() as connection:
            return bool(
                await connection.scalar(
                    select(table.c.id)
                    .where(
                        table.c.marketplace == Marketplace.OZON,
                        table.c.store_slug == store,
                        table.c.article == article,
                        table.c.is_service == 0,
                    )
                    .limit(1)
                )
            )

    async def evidence(self, query: OzonProfitQuery, stores: tuple[str, ...]) -> OzonReportEvidence:
        request = OzonProductsQuery(stores=stores, period_from=query.date_from, period_to=query.date_to)
        today = datetime.now(TIME.moscow_timezone).date()
        products = await OzonEconomicsRepository(self.database).products(
            request, last_complete_day=today - timedelta(days=1), custom=True
        )
        turnover = await AsyncTurnoverRepository(self.database).load(request)
        catalog = table_for(StockItemRecord)
        category = table_for(UnitEconomics1CProductCategoryRecord)
        async with self.database.transaction() as connection:
            identities = tuple(
                OzonReportIdentity.model_validate(row)
                for row in (
                    await connection.execute(
                        select(catalog.c.store_slug, catalog.c.article, category.c.category.label("subject"))
                        .select_from(catalog.outerjoin(category, category.c.stock_item_id == catalog.c.id))
                        .where(catalog.c.marketplace == Marketplace.OZON, catalog.c.store_slug.in_(stores))
                    )
                ).mappings()
            )
            stocked: set[tuple[str, str]] = set()
            for model in (MarketplaceStockDailyHistoryRecord, FulfillmentStockDailyHistoryRecord):
                table = table_for(model)
                stocked.update(
                    (row.store_slug, row.article)
                    for row in await connection.execute(
                        select(table.c.store_slug, table.c.article)
                        .where(
                            table.c.marketplace == Marketplace.OZON,
                            table.c.store_slug.in_(stores),
                            table.c.day.between(query.date_from.isoformat(), query.date_to.isoformat()),
                            table.c.quantity > 0,
                        )
                        .distinct()
                    )
                )
        return OzonReportEvidence(
            products=products.products, turnover=turnover, identities=identities, stocked=frozenset(stocked)
        )

    async def targets(self, stores: tuple[str, ...]) -> dict[tuple[str, str], OzonTargetRecord]:
        table = table_for(UnitEconomics1CProductSettingRecord)
        async with self.database.transaction() as connection:
            return {
                (row.store_slug, row.article): OzonTargetRecord(
                    target_drr_percent=row.target_drr_percent,
                    target_roi_percent=row.target_roi_percent,
                    revision=row.updated_at,
                )
                for row in await connection.execute(
                    select(table).where(
                        table.c.marketplace == Marketplace.OZON, table.c.store_slug.in_(stores)
                    )
                )
            }

    async def save_target(self, command: OzonTargetSave) -> OzonTargetRecord:
        table = table_for(UnitEconomics1CProductSettingRecord)
        change = command.change
        revision = command.saved_at.isoformat()
        values = {
            "target_drr_percent": change.target_drr_percent,
            "target_roi_percent": change.target_roi_percent,
            "updated_at": revision,
            "updated_by_user_id": command.user_id,
            "updated_by_name": command.user_name,
        }
        identity = {
            "store_slug": command.store_slug,
            "marketplace": Marketplace.OZON,
            "article": change.article,
        }
        async with self.database.transaction(write=True) as connection:
            if not change.revision:
                insert = (
                    postgres_insert if self.database.dialect == DatabaseDialect.POSTGRESQL else sqlite_insert
                )
                result = await connection.execute(
                    insert(table).values({**identity, **values}).on_conflict_do_nothing()
                )
            else:
                result = await connection.execute(
                    update(table)
                    .where(
                        table.c.store_slug == command.store_slug,
                        table.c.marketplace == Marketplace.OZON,
                        table.c.article == change.article,
                        table.c.updated_at == change.revision,
                    )
                    .values(values)
                )
            if result.rowcount != 1:
                raise OzonTargetConflict()
        return OzonTargetRecord(
            target_drr_percent=change.target_drr_percent,
            target_roi_percent=change.target_roi_percent,
            revision=revision,
        )
