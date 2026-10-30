from datetime import date
from uuid import uuid4

from sqlalchemy import Table, func, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from app.core.constants import OZON_CALENDAR, OZON_MARGIN, Marketplace, OzonCalendarEventKind
from app.dto.ozon_advertising import AdvertisingPeriod
from app.dto.ozon_calendar import (
    OzonCalendarConflict,
    OzonCalendarEvent,
    OzonCalendarJournal,
    OzonCalendarKey,
    OzonCalendarProduct,
    OzonCalendarProductSource,
    OzonCalendarSave,
    OzonCalendarSources,
)
from app.dto.ozon_economics import OzonProductsQuery
from app.dto.ozon_margin import OzonMarginSnapshot
from app.economics import ozon_calendar
from app.infrastructure import daily_economics_orm as daily_economics_orm
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.async_turnover_repository import AsyncTurnoverRepository
from app.infrastructure.orm import CatalogBarcodeRecord, OrmBase, StockItemRecord
from app.infrastructure.ozon_margin_orm import OzonMarginDailyRecord
from app.ozon import tokens


def records_table() -> Table:
    return OrmBase.metadata.tables[OZON_CALENDAR.records_table]


def events_table() -> Table:
    return OrmBase.metadata.tables[OZON_CALENDAR.events_table]


def identity(table: Table, key: OzonCalendarKey) -> tuple[ColumnElement[bool], ...]:
    return (
        table.c.marketplace == Marketplace.OZON,
        table.c.store_slug == key.store,
        table.c.article == key.article,
        table.c.day == key.day.isoformat(),
    )


class OzonCalendarRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def days(self, store: str, day: date, article: str = "") -> OzonCalendarSources:
        catalog = table_for(StockItemRecord)
        codes = table_for(CatalogBarcodeRecord)
        snapshots = table_for(OzonMarginDailyRecord)
        records = records_table()
        catalog_scope = (
            catalog.c.marketplace == Marketplace.OZON,
            catalog.c.store_slug == store,
            catalog.c.is_service == 0,
        )
        async with self.database.transaction() as connection:
            products = tuple(
                OzonCalendarProduct.model_validate(row)
                for row in (
                    await connection.execute(
                        select(
                            catalog.c.article, catalog.c.name, catalog.c.barcode, catalog.c.image_url
                        ).where(*catalog_scope, *((catalog.c.article == article,) if article else ()))
                    )
                ).mappings()
            )
            barcodes: dict[str, list[str]] = {}
            for row in await connection.execute(
                select(catalog.c.article, codes.c.barcode)
                .join(codes, codes.c.stock_item_id == catalog.c.id)
                .where(*catalog_scope, *((catalog.c.article == article,) if article else ()))
            ):
                barcodes.setdefault(row.article, []).append(row.barcode)
            raw = tuple(
                OzonMarginSnapshot.model_validate_json(payload)
                for payload in await connection.scalars(
                    select(snapshots.c.payload).where(
                        snapshots.c.store_slug == store,
                        snapshots.c.day == day.isoformat(),
                        *((snapshots.c.article == article,) if article else ()),
                    )
                )
            )
            journals = {
                value.source.article: value
                for payload in await connection.scalars(
                    select(records.c.payload_json).where(
                        records.c.marketplace == Marketplace.OZON,
                        records.c.store_slug == store,
                        records.c.day == day.isoformat(),
                        *((records.c.article == article,) if article else ()),
                    )
                )
                for value in (OzonCalendarJournal.model_validate_json(payload),)
            }
            first_day = await connection.scalar(
                select(func.min(snapshots.c.day))
                .join(
                    catalog,
                    (catalog.c.store_slug == snapshots.c.store_slug)
                    & (catalog.c.article == snapshots.c.article),
                )
                .where(*catalog_scope)
            )
        saved = {
            item.article: journals.get(
                item.article, OzonCalendarJournal(original=item, source=item)
            ).model_copy(update={"source": item})
            for item in raw
            if item.version == OZON_MARGIN.version
        }
        period = AdvertisingPeriod(start=day, end=day)
        orders = await AsyncTurnoverRepository(self.database).load(
            OzonProductsQuery(stores=(store,), article=article, period_from=day, period_to=day)
        )
        advertising = await AsyncAdvertisingRepository(self.database).overlapping((store,), period)
        return OzonCalendarSources(
            first_day=date.fromisoformat(first_day) if first_day else None,
            turnover=orders,
            advertising=advertising,
            credentials_present=tokens.has_performance_credentials(store),
            products=tuple(
                OzonCalendarProductSource(
                    product=product.model_copy(
                        update={
                            "barcodes": tuple(
                                sorted({product.barcode, *barcodes.get(product.article, ())} - {""})
                            )
                        }
                    ),
                    journal=saved.get(product.article),
                )
                for product in products
            ),
        )

    async def events(self, key: OzonCalendarKey) -> tuple[OzonCalendarEvent, ...]:
        table = events_table()
        async with self.database.transaction() as connection:
            return tuple(
                OzonCalendarEvent.model_validate_json(payload)
                for payload in await connection.scalars(
                    select(table.c.payload_json).where(*identity(table, key)).order_by(table.c.revision)
                )
            )

    async def apply(self, snapshots: tuple[OzonMarginSnapshot, ...]) -> tuple[OzonMarginSnapshot, ...]:
        if not snapshots:
            return snapshots
        table = records_table()
        async with self.database.transaction() as connection:
            journals = {
                (value.source.store_slug, value.source.article, value.source.day): value
                for payload in await connection.scalars(
                    select(table.c.payload_json).where(
                        table.c.marketplace == Marketplace.OZON,
                        table.c.store_slug.in_(tuple({item.store_slug for item in snapshots})),
                        table.c.day.between(
                            min(item.day for item in snapshots).isoformat(),
                            max(item.day for item in snapshots).isoformat(),
                        ),
                    )
                )
                for value in (OzonCalendarJournal.model_validate_json(payload),)
            }
        return tuple(
            ozon_calendar.effective(journal.model_copy(update={"source": item}))
            if (journal := journals.get((item.store_slug, item.article, item.day)))
            else item
            for item in snapshots
        )

    @staticmethod
    async def write(
        connection: AsyncConnection,
        key: OzonCalendarKey,
        journal: OzonCalendarJournal,
        event: OzonCalendarEvent,
    ) -> None:
        keys = {
            "marketplace": Marketplace.OZON,
            "store_slug": key.store,
            "article": key.article,
            "day": key.day.isoformat(),
        }
        await connection.execute(
            upsert(
                connection,
                records_table(),
                {
                    **keys,
                    "revision": journal.revision,
                    "payload_json": journal.model_dump_json(),
                    "updated_at": event.at.isoformat(),
                },
                tuple(keys),
            )
        )
        await connection.execute(
            insert(events_table()).values(
                **keys,
                id=str(uuid4()),
                revision=journal.revision,
                payload_json=event.model_dump_json(),
                created_at=event.at.isoformat(),
                actor=event.actor,
            )
        )

    @staticmethod
    async def observe(connection: AsyncConnection, snapshot: OzonMarginSnapshot) -> None:
        key = OzonCalendarKey(store=snapshot.store_slug, article=snapshot.article, day=snapshot.day)
        table = records_table()
        payload = await connection.scalar(
            select(table.c.payload_json).where(*identity(table, key)).with_for_update()
        )
        previous = OzonCalendarJournal.model_validate_json(payload) if payload else None
        journal = (
            previous.model_copy(update={"source": snapshot, "revision": previous.revision + 1})
            if previous
            else OzonCalendarJournal(original=snapshot, source=snapshot, revision=1)
        )
        await OzonCalendarRepository.write(
            connection,
            key,
            journal,
            OzonCalendarEvent(
                at=snapshot.observed_at,
                actor=OZON_CALENDAR.source_actor,
                kind=OzonCalendarEventKind.SOURCE,
                before=ozon_calendar.source_values(previous.source) if previous else None,
                after=ozon_calendar.source_values(snapshot),
            ),
        )

    async def save(self, command: OzonCalendarSave) -> None:
        table = table_for(OzonMarginDailyRecord)
        records = records_table()
        async with self.database.transaction(write=True) as connection:
            payload = await connection.scalar(
                select(table.c.payload)
                .where(
                    table.c.store_slug == command.key.store,
                    table.c.article == command.key.article,
                    table.c.day == command.key.day.isoformat(),
                )
                .with_for_update()
            )
            if payload is None or OzonMarginSnapshot.model_validate_json(payload) != command.expected.source:
                raise OzonCalendarConflict()
            revision = await connection.scalar(
                select(records.c.revision).where(*identity(records, command.key)).with_for_update()
            )
            if (revision or 0) != command.expected.revision:
                raise OzonCalendarConflict()
            await self.write(connection, command.key, command.journal, command.event)
