from sqlalchemy import select

from app.core.constants import Marketplace
from app.dto.orders import OzonArticleIndex, OzonCatalogArticle
from app.dto.ozon_related import OzonRelatedItem, OzonRelatedProducts
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.orm import StockItemRecord


class AsyncCatalogRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def ozon_products(self, store_slug: str) -> OzonRelatedProducts:
        table = table_for(StockItemRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table.c.article, table.c.name)
                .where(
                    table.c.store_slug == store_slug,
                    table.c.marketplace == Marketplace.OZON,
                    table.c.is_service == 0,
                    table.c.article != "",
                )
                .order_by(table.c.name, table.c.article)
            )
            return OzonRelatedProducts(
                items=tuple(OzonRelatedItem.model_validate(row) for row in rows.mappings())
            )

    async def get_ozon_articles(self, store_slug: str) -> OzonArticleIndex:
        table = table_for(StockItemRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table.c.mp_sku, table.c.article).where(
                    table.c.store_slug == store_slug,
                    table.c.marketplace == Marketplace.OZON.value,
                    table.c.mp_sku.is_not(None),
                )
            )
            return OzonArticleIndex(
                items=tuple(OzonCatalogArticle(sku=row.mp_sku, article=row.article) for row in rows)
            )
