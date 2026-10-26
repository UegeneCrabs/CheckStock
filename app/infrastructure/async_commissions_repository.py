from datetime import date, datetime

from sqlalchemy import select, update

from app.core.constants import TIME, OzonCommissionState
from app.dto.ozon_commissions import (
    DailyFboCommission,
    FboCommissionObservation,
    FboCommissionValue,
    StoredFboCommission,
)
from app.dto.ozon_prices import PriceIdentity
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.ozon_commissions_orm import (
    OzonFboCommissionCurrentRecord,
    OzonFboCommissionDailyRecord,
)


class AsyncCommissionsRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def load(self, stores: tuple[str, ...]) -> tuple[StoredFboCommission, ...]:
        table = table_for(OzonFboCommissionCurrentRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(select(table).where(table.c.store_slug.in_(stores)))
            return tuple(
                StoredFboCommission(
                    store_slug=row.store_slug,
                    product_id=int(row.product_id),
                    offer_id=row.offer_id,
                    value=FboCommissionValue.model_validate_json(row.payload),
                )
                for row in rows
            )

    async def load_daily(
        self, stores: tuple[str, ...], period_from: date, period_to: date
    ) -> tuple[DailyFboCommission, ...]:
        table = table_for(OzonFboCommissionDailyRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table)
                .where(
                    table.c.store_slug.in_(stores),
                    table.c.day >= period_from.isoformat(),
                    table.c.day <= period_to.isoformat(),
                )
                .order_by(table.c.store_slug, table.c.product_id, table.c.day)
            )
            return tuple(
                DailyFboCommission(
                    store_slug=row.store_slug,
                    product_id=int(row.product_id),
                    offer_id=row.offer_id,
                    day=date.fromisoformat(row.day),
                    first=FboCommissionValue.model_validate_json(row.first_payload),
                    last=FboCommissionValue.model_validate_json(row.last_payload),
                    closed_at=datetime.fromisoformat(row.closed_at) if row.closed_at else None,
                )
                for row in rows
            )

    async def recorded_products(self, store: str, day: date) -> frozenset[int]:
        table = table_for(OzonFboCommissionDailyRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table.c.product_id, table.c.last_payload).where(
                    table.c.store_slug == store, table.c.day == day.isoformat()
                )
            )
            return frozenset(
                int(row.product_id)
                for row in rows
                if (value := FboCommissionValue.model_validate_json(row.last_payload)).state
                == OzonCommissionState.AVAILABLE
                and value.percent is not None
            )

    async def save(self, store: str, observations: tuple[FboCommissionObservation, ...]) -> int:
        recorded: dict[date, frozenset[int]] = {}
        pending: list[FboCommissionObservation] = []
        for item in observations:
            received = item.value.received_at
            if received is None:
                raise ValueError("Commission observations require acquisition time")
            day = received.astimezone(TIME.moscow_timezone).date()
            if day not in recorded:
                recorded[day] = await self.recorded_products(store, day)
            if item.product_id not in recorded[day]:
                pending.append(item)
        if not pending:
            return 0
        current = table_for(OzonFboCommissionCurrentRecord)
        daily = table_for(OzonFboCommissionDailyRecord)
        saved_count = 0
        async with self.database.transaction(write=True) as connection:
            for item in pending:
                received = item.value.received_at
                if received is None:
                    raise ValueError("Commission observations require acquisition time")
                key = {"store_slug": store, "product_id": str(item.product_id)}
                day_key = received.astimezone(TIME.moscow_timezone).date().isoformat()
                row = (
                    await connection.execute(
                        select(daily).where(
                            daily.c.store_slug == store,
                            daily.c.product_id == str(item.product_id),
                            daily.c.day == day_key,
                        )
                    )
                ).first()
                last = FboCommissionValue.model_validate_json(row.last_payload) if row else item.value
                if (
                    row is not None
                    and last.state == OzonCommissionState.AVAILABLE
                    and last.percent is not None
                ):
                    continue
                if row is not None and row.closed_at is not None:
                    raise ValueError("A closed Ozon commission day cannot be changed")
                saved = (
                    await connection.execute(
                        select(current.c.payload).where(
                            current.c.store_slug == store,
                            current.c.product_id == str(item.product_id),
                        )
                    )
                ).scalar()
                value = FboCommissionValue.model_validate_json(saved) if saved else FboCommissionValue()
                if value.received_at is None or received >= value.received_at:
                    await connection.execute(
                        upsert(
                            connection,
                            current,
                            {**key, "offer_id": item.offer_id, "payload": item.value.model_dump_json()},
                            ("store_slug", "product_id"),
                        )
                    )
                first = FboCommissionValue.model_validate_json(row.first_payload) if row else item.value
                if last.received_at is None or received >= last.received_at:
                    last = item.value
                await connection.execute(
                    upsert(
                        connection,
                        daily,
                        {
                            **key,
                            "day": day_key,
                            "offer_id": item.offer_id,
                            "first_payload": first.model_dump_json(),
                            "last_payload": last.model_dump_json(),
                            "closed_at": None,
                        },
                        ("store_slug", "product_id", "day"),
                    )
                )
                if last.state == OzonCommissionState.AVAILABLE and last.percent is not None:
                    saved_count += 1
        return saved_count

    async def fail(self, store: str, identities: tuple[PriceIdentity, ...], now: datetime) -> None:
        recorded = await self.recorded_products(store, now.astimezone(TIME.moscow_timezone).date())
        pending = tuple(item for item in identities if item.product_id not in recorded)
        if not pending:
            return
        table = table_for(OzonFboCommissionCurrentRecord)
        async with self.database.transaction(write=True) as connection:
            for item in pending:
                saved = (
                    await connection.execute(
                        select(table.c.payload).where(
                            table.c.store_slug == store,
                            table.c.product_id == str(item.product_id),
                        )
                    )
                ).scalar()
                value = FboCommissionValue.model_validate_json(saved) if saved else FboCommissionValue()
                value = value.model_copy(update={"state": OzonCommissionState.ERROR, "attempted_at": now})
                await connection.execute(
                    upsert(
                        connection,
                        table,
                        {
                            "store_slug": store,
                            "product_id": str(item.product_id),
                            "offer_id": item.offer_id,
                            "payload": value.model_dump_json(),
                        },
                        ("store_slug", "product_id"),
                    )
                )

    async def pending_stores(self, now: datetime) -> tuple[str, ...]:
        table = table_for(OzonFboCommissionDailyRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table.c.store_slug)
                .distinct()
                .where(
                    table.c.day < now.astimezone(TIME.moscow_timezone).date().isoformat(),
                    table.c.closed_at.is_(None),
                )
            )
            return tuple(rows.scalars())

    async def close_days(self, store: str, now: datetime) -> int:
        table = table_for(OzonFboCommissionDailyRecord)
        async with self.database.transaction(write=True) as connection:
            result = await connection.execute(
                update(table)
                .where(
                    table.c.store_slug == store,
                    table.c.day < now.astimezone(TIME.moscow_timezone).date().isoformat(),
                    table.c.closed_at.is_(None),
                )
                .values(closed_at=now.isoformat())
            )
            return result.rowcount
