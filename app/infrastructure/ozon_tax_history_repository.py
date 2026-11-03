from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import Marketplace
from app.dto.ozon_cabinet import OzonCabinetRecord, OzonCabinetValues
from app.dto.ozon_calendar import OzonCalendarConflict, OzonCalendarJournal, OzonCalendarKey
from app.dto.ozon_margin import OzonMarginSnapshot
from app.dto.ozon_prices import PriceValue
from app.dto.ozon_tax_history import (
    OzonTaxHistoryCommand,
    OzonTaxHistoryData,
    OzonTaxHistoryPlan,
    OzonTaxHistoryRow,
)
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.async_prices_repository import stored_price_values
from app.infrastructure.ozon_cabinet_orm import OzonCabinetRecord as CabinetTable
from app.infrastructure.ozon_calendar_repository import (
    OzonCalendarRepository,
    events_table,
    identity,
    records_table,
)
from app.infrastructure.ozon_margin_orm import OzonMarginDailyRecord
from app.infrastructure.ozon_prices_orm import OzonPriceDailyRecord


async def cabinet_record(connection: AsyncConnection, store: str, *, lock: bool = False) -> OzonCabinetRecord:
    table = table_for(CabinetTable)
    statement = select(table).where(table.c.store_slug == store)
    row = (await connection.execute(statement.with_for_update() if lock else statement)).first()
    return (
        OzonCabinetRecord(
            store_slug=store,
            revision=row.revision,
            values=OzonCabinetValues.model_validate_json(row.payload),
            updated_at=row.updated_at,
            updated_by=row.updated_by,
        )
        if row
        else OzonCabinetRecord(store_slug=store)
    )


class OzonTaxHistoryRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def load(self, command: OzonTaxHistoryCommand) -> OzonTaxHistoryData:
        snapshots = table_for(OzonMarginDailyRecord)
        prices = table_for(OzonPriceDailyRecord)
        records, events = records_table(), events_table()
        through = command.through.isoformat()
        async with self.database.transaction() as connection:
            cabinet = await cabinet_record(connection, command.store)
            raw = tuple(
                OzonMarginSnapshot.model_validate_json(payload)
                for payload in await connection.scalars(
                    select(snapshots.c.payload)
                    .where(
                        snapshots.c.store_slug == command.store,
                        snapshots.c.day <= through,
                    )
                    .order_by(snapshots.c.day, snapshots.c.article)
                )
            )
            journals = {
                (value.source.day.isoformat(), value.source.article): value
                for payload in await connection.scalars(
                    select(records.c.payload_json).where(
                        records.c.marketplace == Marketplace.OZON,
                        records.c.store_slug == command.store,
                        records.c.day <= through,
                    )
                )
                for value in (OzonCalendarJournal.model_validate_json(payload),)
            }
            repaired = {
                (row.day, row.article)
                for row in await connection.execute(
                    select(events.c.day, events.c.article).where(
                        events.c.marketplace == Marketplace.OZON,
                        events.c.store_slug == command.store,
                        events.c.day <= through,
                        events.c.actor == command.operation,
                    )
                )
            }
            historical: dict[tuple[str, str], list[PriceValue]] = {}
            for row in await connection.execute(
                select(prices.c.day, prices.c.offer_id, prices.c.last_payload)
                .where(
                    prices.c.store_slug == command.store,
                    prices.c.day <= through,
                )
                .order_by(prices.c.product_id)
            ):
                historical.setdefault((row.day, row.offer_id), []).append(
                    stored_price_values(row.last_payload).black_price
                )
        return OzonTaxHistoryData(
            cabinet=cabinet,
            rows=tuple(
                OzonTaxHistoryRow(
                    snapshot=item,
                    journal=journals.get((item.day.isoformat(), item.article)),
                    prices=tuple(historical.get((item.day.isoformat(), item.article), ())),
                    repaired=(item.day.isoformat(), item.article) in repaired,
                )
                for item in raw
            ),
        )

    async def save(self, plan: OzonTaxHistoryPlan) -> None:
        if not plan.changes and plan.cabinet_change is None:
            return
        snapshots = table_for(OzonMarginDailyRecord)
        prices = table_for(OzonPriceDailyRecord)
        records = records_table()
        async with self.database.transaction(write=True) as connection:
            if await cabinet_record(connection, plan.command.store, lock=True) != plan.cabinet:
                raise OzonCalendarConflict()
            for change in plan.changes:
                snapshot = change.before.snapshot
                key = OzonCalendarKey(store=snapshot.store_slug, article=snapshot.article, day=snapshot.day)
                scope = (
                    snapshots.c.store_slug == key.store,
                    snapshots.c.article == key.article,
                    snapshots.c.day == key.day.isoformat(),
                )
                payload = await connection.scalar(select(snapshots.c.payload).where(*scope).with_for_update())
                if payload is None or OzonMarginSnapshot.model_validate_json(payload) != snapshot:
                    raise OzonCalendarConflict()
                journal_payload = await connection.scalar(
                    select(records.c.payload_json).where(*identity(records, key)).with_for_update()
                )
                journal = (
                    OzonCalendarJournal.model_validate_json(journal_payload) if journal_payload else None
                )
                if journal != change.before.journal:
                    raise OzonCalendarConflict()
                historical = tuple(
                    stored_price_values(value).black_price
                    for value in await connection.scalars(
                        select(prices.c.last_payload)
                        .where(
                            prices.c.store_slug == key.store,
                            prices.c.offer_id == key.article,
                            prices.c.day == key.day.isoformat(),
                        )
                        .order_by(prices.c.product_id)
                        .with_for_update()
                    )
                )
                if historical != change.before.prices:
                    raise OzonCalendarConflict()
                await connection.execute(
                    update(snapshots).where(*scope).values(payload=change.snapshot.model_dump_json())
                )
                await OzonCalendarRepository.write(connection, key, change.journal, change.event)
            if command := plan.cabinet_change:
                table = table_for(CabinetTable)
                values = plan.cabinet.values.model_copy(update={"expenses": command.change.expenses})
                await connection.execute(
                    upsert(
                        connection,
                        table,
                        {
                            "store_slug": command.store_slug,
                            "revision": command.change.revision + 1,
                            "payload": values.model_dump_json(),
                            "updated_at": command.saved_at.isoformat(),
                            "updated_by": command.user_name,
                        },
                        ("store_slug",),
                    )
                )
