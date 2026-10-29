from uuid import UUID

from sqlalchemy import case, delete, insert, select, update

from app.core.constants import OZON_ENTRY_PENDING, OZON_PROMOTIONS, OzonEntryState
from app.dto.ozon_promotion_entry import OzonEntryOperation
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.ozon_promotion_entry_orm import (
    OzonPromotionEntryRecord,
    OzonPromotionEntryReservation,
)


class OzonPromotionEntryRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def create(self, operation: OzonEntryOperation) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                insert(table_for(OzonPromotionEntryRecord)).values(
                    id=str(operation.id),
                    store_slug=operation.request.store,
                    article=operation.request.article,
                    user_id=operation.user_id,
                    state=operation.state.value,
                    created_at=operation.created_at.isoformat(),
                    payload=operation.model_dump_json(),
                )
            )

    async def get(self, operation_id: UUID) -> OzonEntryOperation:
        table = table_for(OzonPromotionEntryRecord)
        async with self.database.transaction() as connection:
            payload = await connection.scalar(select(table.c.payload).where(table.c.id == str(operation_id)))
        if payload is None:
            raise LookupError("Entry operation not found")
        return OzonEntryOperation.model_validate_json(payload)

    async def history(self, store: str, article: str, user_id: int) -> tuple[OzonEntryOperation, ...]:
        table = table_for(OzonPromotionEntryRecord)
        async with self.database.transaction() as connection:
            rows = await connection.scalars(
                select(table.c.payload)
                .where(table.c.store_slug == store, table.c.article == article, table.c.user_id == user_id)
                .order_by(
                    case((table.c.state.in_(OZON_ENTRY_PENDING), 0), else_=1), table.c.created_at.desc()
                )
                .limit(OZON_PROMOTIONS.entry_history_limit)
            )
            return tuple(OzonEntryOperation.model_validate_json(payload) for payload in rows)

    async def save(self, operation: OzonEntryOperation, *, release: bool = False) -> None:
        table = table_for(OzonPromotionEntryRecord)
        reservations = table_for(OzonPromotionEntryReservation)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                update(table)
                .where(table.c.id == str(operation.id))
                .values(state=operation.state.value, payload=operation.model_dump_json())
            )
            if release:
                await connection.execute(
                    delete(reservations).where(reservations.c.operation_id == str(operation.id))
                )

    async def claim(self, operation: OzonEntryOperation) -> bool:
        table = table_for(OzonPromotionEntryRecord)
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
                .where(table.c.id == str(operation.id), table.c.state == OzonEntryState.PREPARED)
                .values(state=operation.state.value, payload=operation.model_dump_json())
                .returning(table.c.id)
            )
            return claimed is not None
