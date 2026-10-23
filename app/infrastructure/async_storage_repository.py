from datetime import UTC, date, datetime

from sqlalchemy import func, select

from app.core.constants import Marketplace
from app.dto.ozon_storage import StorageReportTicket, StorageSnapshot, StorageStoreData, StorageStoreSnapshot
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.orm import StockItemRecord
from app.infrastructure.ozon_storage_orm import (
    OzonStorageDailyRecord,
    OzonStorageReportRecord,
    OzonStorageRunRecord,
)


class AsyncStorageRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def ticket(self, store: str, day: date) -> StorageReportTicket:
        table = table_for(OzonStorageReportRecord)
        async with self.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(table).where(table.c.store_slug == store, table.c.day == day.isoformat())
                    )
                )
                .mappings()
                .one_or_none()
            )
            return (
                StorageReportTicket(code=row["code"], attempts=row["attempts"])
                if row
                else StorageReportTicket()
            )

    async def save_ticket(self, store: str, day: date, ticket: StorageReportTicket) -> None:
        table = table_for(OzonStorageReportRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    table,
                    {
                        "store_slug": store,
                        "day": day.isoformat(),
                        "code": ticket.code,
                        "attempts": ticket.attempts,
                    },
                    ("store_slug", "day"),
                )
            )

    async def completed(self, store: str, day: date) -> bool:
        table = table_for(OzonStorageRunRecord)
        async with self.database.transaction() as connection:
            value = await connection.scalar(
                select(table.c.completed_at).where(
                    table.c.store_slug == store, table.c.day == day.isoformat()
                )
            )
            return value is not None

    async def articles(self, store: str) -> tuple[str, ...]:
        table = table_for(StockItemRecord)
        async with self.database.transaction() as connection:
            values = await connection.scalars(
                select(table.c.article)
                .distinct()
                .where(table.c.store_slug == store, table.c.marketplace == Marketplace.OZON)
            )
            return tuple(values)

    async def commit(self, snapshot: StorageStoreSnapshot) -> None:
        daily = table_for(OzonStorageDailyRecord)
        runs = table_for(OzonStorageRunRecord)
        async with self.database.transaction(write=True) as connection:
            previous = await connection.scalar(
                select(runs.c.completed_at).where(
                    runs.c.store_slug == snapshot.store_slug, runs.c.day == snapshot.period.day.isoformat()
                )
            )
            if previous is not None:
                return
            for product in snapshot.products:
                if product.store_slug != snapshot.store_slug or product.period != snapshot.period:
                    raise ValueError("Inconsistent storage snapshot identity")
                await connection.execute(
                    upsert(
                        connection,
                        daily,
                        {
                            "store_slug": snapshot.store_slug,
                            "day": snapshot.period.day.isoformat(),
                            "article": product.article,
                            "period_from": snapshot.period.start.isoformat(),
                            "period_to": snapshot.period.end.isoformat(),
                            "observed_at": snapshot.observed_at.isoformat(),
                            "payload": product.model_dump_json(),
                        },
                        ("store_slug", "day", "article"),
                    )
                )
            await connection.execute(
                upsert(
                    connection,
                    runs,
                    {
                        "store_slug": snapshot.store_slug,
                        "day": snapshot.period.day.isoformat(),
                        "attempted_at": snapshot.observed_at.isoformat(),
                        "completed_at": snapshot.observed_at.isoformat(),
                        "error": None,
                    },
                    ("store_slug", "day"),
                )
            )

    async def fail(self, store: str, day: date, error: str) -> None:
        table = table_for(OzonStorageRunRecord)
        async with self.database.transaction(write=True) as connection:
            previous = await connection.scalar(
                select(table.c.completed_at).where(
                    table.c.store_slug == store, table.c.day == day.isoformat()
                )
            )
            if previous is None:
                await connection.execute(
                    upsert(
                        connection,
                        table,
                        {
                            "store_slug": store,
                            "day": day.isoformat(),
                            "attempted_at": datetime.now(UTC).isoformat(),
                            "completed_at": None,
                            "error": error,
                        },
                        ("store_slug", "day"),
                    )
                )

    async def history(
        self, stores: tuple[str, ...], start: date, end: date, article: str = ""
    ) -> tuple[StorageSnapshot, ...]:
        table = table_for(OzonStorageDailyRecord)
        query = select(table.c.payload).where(
            table.c.store_slug.in_(stores), table.c.day >= start.isoformat(), table.c.day <= end.isoformat()
        )
        if article:
            query = query.where(table.c.article == article)
        async with self.database.transaction() as connection:
            rows = await connection.scalars(query.order_by(table.c.day, table.c.store_slug, table.c.article))
            return tuple(StorageSnapshot.model_validate_json(row) for row in rows)

    async def latest(self, stores: tuple[str, ...], day: date) -> tuple[StorageStoreData, ...]:
        table = table_for(OzonStorageDailyRecord)
        runs = table_for(OzonStorageRunRecord)
        async with self.database.transaction() as connection:
            latest = (
                select(runs.c.store_slug, func.max(runs.c.day).label("day"))
                .where(
                    runs.c.store_slug.in_(stores),
                    runs.c.day <= day.isoformat(),
                    runs.c.completed_at.is_not(None),
                )
                .group_by(runs.c.store_slug)
                .subquery()
            )
            rows = await connection.execute(
                select(table.c.store_slug, table.c.payload).join(
                    latest, (table.c.store_slug == latest.c.store_slug) & (table.c.day == latest.c.day)
                )
            )
            products: dict[str, list[StorageSnapshot]] = {store: [] for store in stores}
            for row in rows:
                products[row.store_slug].append(StorageSnapshot.model_validate_json(row.payload))
            attempts = {
                row.store_slug: row
                for row in await connection.execute(
                    select(runs).where(runs.c.store_slug.in_(stores), runs.c.day == day.isoformat())
                )
            }
            return tuple(
                StorageStoreData(
                    store_slug=store,
                    products=tuple(products[store]),
                    attempted_at=datetime.fromisoformat(attempts[store].attempted_at)
                    if store in attempts
                    else None,
                    error=attempts[store].error if store in attempts else None,
                )
                for store in stores
            )
