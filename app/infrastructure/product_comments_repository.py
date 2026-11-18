from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.constants import DatabaseDialect, Marketplace
from app.dto.product_comments import ProductComment, ProductCommentKey
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.orm import ProductCommentRecord, StockItemRecord

COMMENTS = table_for(ProductCommentRecord)
PRODUCTS = table_for(StockItemRecord)
COMMENT_KEY = ("marketplace", "store_slug", "article")


class ProductCommentsRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self.database = database

    async def load(self, marketplace: Marketplace, stores: tuple[str, ...]) -> tuple[ProductComment, ...]:
        statement = select(COMMENTS.c.store_slug, COMMENTS.c.article, COMMENTS.c.text).where(
            COMMENTS.c.marketplace == marketplace, COMMENTS.c.store_slug.in_(stores)
        )
        async with self.database.transaction() as connection:
            rows = await connection.execute(statement)
            return tuple(ProductComment.from_row(row) for row in rows)

    async def save(self, marketplace: Marketplace, comment: ProductComment) -> bool:
        product = select(PRODUCTS.c.id).where(
            PRODUCTS.c.marketplace == marketplace,
            PRODUCTS.c.store_slug == comment.store_slug,
            PRODUCTS.c.article == comment.article,
            PRODUCTS.c.is_service == 0,
        )
        async with self.database.transaction(write=True) as connection:
            if await connection.scalar(product) is None:
                return False
            await connection.execute(
                upsert(
                    connection, COMMENTS, {"marketplace": marketplace, **comment.model_dump()}, COMMENT_KEY
                )
            )
        return True

    async def import_missing(
        self, marketplace: Marketplace, comments: tuple[ProductComment, ...]
    ) -> tuple[ProductCommentKey, ...]:
        candidates = {(comment.store_slug, comment.article): comment for comment in comments if comment.text}
        if not candidates:
            return ()
        products = select(PRODUCTS.c.store_slug, PRODUCTS.c.article).where(
            PRODUCTS.c.marketplace == marketplace,
            PRODUCTS.c.is_service == 0,
            tuple_(PRODUCTS.c.store_slug, PRODUCTS.c.article).in_(tuple(candidates)),
        )
        async with self.database.transaction(write=True) as connection:
            rows = await connection.execute(products)
            found = tuple(ProductCommentKey.from_row(row) for row in rows)
            if not found:
                return ()
            insert = (
                postgres_insert if connection.dialect.name == DatabaseDialect.POSTGRESQL else sqlite_insert
            )
            await connection.execute(
                insert(COMMENTS).on_conflict_do_nothing(index_elements=COMMENT_KEY),
                [
                    {"marketplace": marketplace, **candidates[key.store_slug, key.article].model_dump()}
                    for key in found
                ],
            )
            current = await connection.execute(
                select(COMMENTS.c.store_slug, COMMENTS.c.article, COMMENTS.c.text).where(
                    COMMENTS.c.marketplace == marketplace,
                    tuple_(COMMENTS.c.store_slug, COMMENTS.c.article).in_(
                        tuple((key.store_slug, key.article) for key in found)
                    ),
                )
            )
            return tuple(
                ProductCommentKey(store_slug=store, article=article)
                for store, article, text in current
                if text == candidates[store, article].text
            )
