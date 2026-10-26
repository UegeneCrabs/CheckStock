import json
from datetime import datetime

from sqlalchemy import select, update

from app.core.constants import TIME, Marketplace, OzonPriceField, OzonPriceState
from app.dto.ozon_prices import PriceIdentity, PriceObservation, PriceValue, PriceValues, StoredPrices
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.orm import StockItemRecord
from app.infrastructure.ozon_prices_orm import OzonPriceCurrentRecord, OzonPriceDailyRecord


def stored_price_values(payload: str) -> PriceValues:
    values: object = json.loads(payload)
    if isinstance(values, dict):
        values.pop("old_price", None)
    return PriceValues.model_validate(values)


class AsyncPricesRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def pending_stores(self, now: datetime) -> tuple[str, ...]:
        table = table_for(OzonPriceDailyRecord)
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

    async def catalog(self, store: str) -> tuple[PriceIdentity, ...]:
        table = table_for(StockItemRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table.c.mp_product_id, table.c.article).where(
                    table.c.store_slug == store,
                    table.c.marketplace == Marketplace.OZON,
                    table.c.mp_product_id.is_not(None),
                )
            )
            return tuple(
                PriceIdentity(product_id=int(row.mp_product_id), offer_id=row.article)
                for row in rows
                if str(row.mp_product_id).isdigit() and int(row.mp_product_id) > 0
            )

    async def load(self, stores: tuple[str, ...]) -> tuple[StoredPrices, ...]:
        table = table_for(OzonPriceCurrentRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(select(table).where(table.c.store_slug.in_(stores)))
            return tuple(
                StoredPrices(
                    store_slug=row.store_slug,
                    product_id=int(row.product_id),
                    offer_id=row.offer_id,
                    prices=stored_price_values(row.payload),
                )
                for row in rows
            )

    async def save(self, store: str, observations: tuple[PriceObservation, ...]) -> None:
        current = table_for(OzonPriceCurrentRecord)
        daily = table_for(OzonPriceDailyRecord)
        async with self.database.transaction(write=True) as connection:
            for item in observations:
                key = {"store_slug": store, "product_id": str(item.product_id)}
                current_where = (current.c.store_slug == store, current.c.product_id == str(item.product_id))
                saved = (await connection.execute(select(current.c.payload).where(*current_where))).scalar()
                prices = stored_price_values(saved) if saved else PriceValues()
                by_day: dict[str, dict[str, PriceValue]] = {}
                changes: dict[str, PriceValue] = {}
                for change in item.changes:
                    received = change.value.received_at
                    if received is None:
                        raise ValueError("Price observations require acquisition time")
                    day = received.astimezone(TIME.moscow_timezone).date().isoformat()
                    by_day.setdefault(day, {})[change.field.value] = change.value
                    previous = prices.value(change.field).received_at
                    if previous is None or received >= previous:
                        changes[change.field.value] = change.value
                prices = prices.model_copy(update=changes)
                await connection.execute(
                    upsert(
                        connection,
                        current,
                        {
                            **key,
                            "offer_id": item.offer_id,
                            "payload": prices.model_dump_json(),
                        },
                        ("store_slug", "product_id"),
                    )
                )
                for day, fields in by_day.items():
                    row = (
                        await connection.execute(
                            select(daily).where(
                                daily.c.store_slug == store,
                                daily.c.product_id == str(item.product_id),
                                daily.c.day == day,
                            )
                        )
                    ).first()
                    if row is not None and row.closed_at is not None:
                        raise ValueError("A closed Ozon price day cannot be changed")
                    first = stored_price_values(row.first_payload) if row else PriceValues()
                    last = stored_price_values(row.last_payload) if row else PriceValues()
                    first = first.model_copy(
                        update={
                            field: value
                            for field, value in fields.items()
                            if first.value(OzonPriceField(field)).received_at is None
                        }
                    )
                    last = last.model_copy(
                        update={
                            field: value
                            for field, value in fields.items()
                            if (previous := last.value(OzonPriceField(field)).received_at) is None
                            or (value.received_at is not None and value.received_at >= previous)
                        }
                    )
                    await connection.execute(
                        upsert(
                            connection,
                            daily,
                            {
                                **key,
                                "day": day,
                                "offer_id": item.offer_id,
                                "first_payload": first.model_dump_json(),
                                "last_payload": last.model_dump_json(),
                                "complete": int(last.complete),
                                "closed_at": None,
                            },
                            ("store_slug", "product_id", "day"),
                        )
                    )

    async def fail(
        self,
        store: str,
        identities: tuple[PriceIdentity, ...],
        fields: tuple[OzonPriceField, ...],
        now: datetime,
    ) -> None:
        table = table_for(OzonPriceCurrentRecord)
        async with self.database.transaction(write=True) as connection:
            for identity in identities:
                key = {"store_slug": store, "product_id": str(identity.product_id)}
                payload = (
                    await connection.execute(
                        select(table.c.payload).where(
                            table.c.store_slug == store,
                            table.c.product_id == str(identity.product_id),
                        )
                    )
                ).scalar()
                prices = stored_price_values(payload) if payload else PriceValues()
                prices = prices.model_copy(
                    update={
                        field.value: prices.value(field).model_copy(
                            update={
                                "state": OzonPriceState.ERROR,
                                "attempted_at": now,
                            }
                        )
                        for field in fields
                    }
                )
                await connection.execute(
                    upsert(
                        connection,
                        table,
                        {
                            **key,
                            "offer_id": identity.offer_id,
                            "payload": prices.model_dump_json(),
                        },
                        ("store_slug", "product_id"),
                    )
                )

    async def close_days(self, store: str, now: datetime) -> int:
        table = table_for(OzonPriceDailyRecord)
        today = now.astimezone(TIME.moscow_timezone).date().isoformat()
        async with self.database.transaction(write=True) as connection:
            result = await connection.execute(
                update(table)
                .where(
                    table.c.store_slug == store,
                    table.c.day < today,
                    table.c.closed_at.is_(None),
                )
                .values(closed_at=now.isoformat())
            )
            return result.rowcount
