from datetime import datetime
from uuid import UUID

from sqlalchemy import case, delete, func, insert, select, update

from app.core.constants import OZON_PRICE_CHANGE_PENDING, OZON_PRICE_CHANGES, OzonPriceChangeState
from app.dto.ozon_price_changes import OzonPriceChangeOperation
from app.dto.ozon_promotion_entry import OzonEntryOperation
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.ozon_price_changes_orm import OzonPriceChangeRecord
from app.infrastructure.ozon_promotion_entry_orm import (
    OzonPromotionEntryRecord,
    OzonPromotionEntryReservation,
)


class OzonPriceChangeRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def create(self, operation: OzonPriceChangeOperation) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                insert(table_for(OzonPriceChangeRecord)).values(
                    id=str(operation.id),
                    store_slug=operation.request.store,
                    article=operation.request.article,
                    user_id=operation.user_id,
                    state=operation.state.value,
                    created_at=operation.created_at.isoformat(),
                    sent_at=None,
                    payload=operation.model_dump_json(),
                )
            )

    async def get(self, operation_id: UUID) -> OzonPriceChangeOperation:
        table = table_for(OzonPriceChangeRecord)
        async with self.database.transaction() as connection:
            payload = await connection.scalar(select(table.c.payload).where(table.c.id == str(operation_id)))
        if payload is None:
            raise LookupError("Price operation not found")
        return OzonPriceChangeOperation.model_validate_json(payload)

    async def history(self, store: str, article: str, user_id: int) -> tuple[OzonPriceChangeOperation, ...]:
        table = table_for(OzonPriceChangeRecord)
        async with self.database.transaction() as connection:
            rows = await connection.scalars(
                select(table.c.payload)
                .where(
                    table.c.store_slug == store,
                    table.c.article == article,
                    table.c.user_id == user_id,
                )
                .order_by(
                    case((table.c.state.in_(OZON_PRICE_CHANGE_PENDING), 0), else_=1),
                    table.c.created_at.desc(),
                )
                .limit(OZON_PRICE_CHANGES.history_limit)
            )
            return tuple(OzonPriceChangeOperation.model_validate_json(value) for value in rows)

    async def save(self, operation: OzonPriceChangeOperation, *, release: bool = False) -> None:
        table = table_for(OzonPriceChangeRecord)
        reservations = table_for(OzonPromotionEntryReservation)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                update(table)
                .where(table.c.id == str(operation.id))
                .values(
                    state=operation.state.value,
                    sent_at=operation.sent_at.isoformat() if operation.sent_at else None,
                    payload=operation.model_dump_json(),
                )
            )
            if release:
                await connection.execute(
                    delete(reservations).where(reservations.c.operation_id == str(operation.id))
                )

    async def claim(self, operation: OzonPriceChangeOperation) -> bool:
        table = table_for(OzonPriceChangeRecord)
        reservations = table_for(OzonPromotionEntryReservation)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    reservations,
                    {
                        "store_slug": operation.request.store,
                        "article": operation.request.article,
                        "operation_id": str(operation.id),
                    },
                    ("store_slug", "article"),
                    updates={"store_slug": operation.request.store},
                )
            )
            owner = await connection.scalar(
                select(reservations.c.operation_id).where(
                    reservations.c.store_slug == operation.request.store,
                    reservations.c.article == operation.request.article,
                )
            )
            if owner != str(operation.id):
                return False
            claimed = await connection.scalar(
                update(table)
                .where(
                    table.c.id == str(operation.id),
                    table.c.state == OzonPriceChangeState.PREPARED,
                )
                .values(state=operation.state.value, payload=operation.model_dump_json())
                .returning(table.c.id)
            )
            return claimed is not None

    async def attempts(self, store: str, article: str, since: datetime) -> int:
        table = table_for(OzonPriceChangeRecord)
        entries = table_for(OzonPromotionEntryRecord)
        async with self.database.transaction() as connection:
            count = await connection.scalar(
                select(func.count())
                .select_from(table)
                .where(
                    table.c.store_slug == store,
                    table.c.article == article,
                    table.c.sent_at >= since.isoformat(),
                )
            )
            rows = await connection.scalars(
                select(entries.c.payload).where(
                    entries.c.store_slug == store,
                    entries.c.article == article,
                    entries.c.created_at >= since.isoformat(),
                )
            )
            promotions = tuple(OzonEntryOperation.model_validate_json(value) for value in rows)
        return int(count or 0) + sum(
            item.sent_at is not None and item.sent_at >= since for item in promotions
        )
