from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, insert, select

from app.core.constants import AdvertisingState
from app.dto.ozon_advertising import (
    AdvertisingPeriod,
    AdvertisingSnapshot,
    AdvertisingSourceStatus,
    PerformanceReportRequest,
    PerformanceRequestEvent,
    SavedPerformanceReport,
)
from app.dto.ozon_limits import PendingPerformanceReport
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.ozon_advertising_orm import (
    OzonAdvertisingCooldownRecord,
    OzonAdvertisingFactRecord,
    OzonAdvertisingReportRecord,
    OzonAdvertisingRequestRecord,
    OzonAdvertisingResponseRecord,
    OzonAdvertisingSourceRecord,
)


class AsyncAdvertisingRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def save(self, snapshot: AdvertisingSnapshot) -> None:
        table = table_for(OzonAdvertisingFactRecord)
        state = AdvertisingSourceStatus(
            store_slug=snapshot.store_slug,
            source=snapshot.source,
            period=snapshot.period,
            state=AdvertisingState.READY,
            attempted_at=snapshot.synced_at,
            snapshot=snapshot,
            refresh_slot=snapshot.refresh_slot,
        )
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                delete(table).where(
                    table.c.store_slug == snapshot.store_slug,
                    table.c.source == snapshot.source,
                    table.c.day.between(snapshot.period.start.isoformat(), snapshot.period.end.isoformat()),
                )
            )
            if snapshot.facts:
                await connection.execute(
                    insert(table),
                    [
                        {
                            "store_slug": snapshot.store_slug,
                            "source": snapshot.source.value,
                            "day": fact.day.isoformat(),
                            "position": index,
                            "payload": fact.model_dump_json(),
                        }
                        for index, fact in enumerate(snapshot.facts)
                    ],
                )
            status_table = table_for(OzonAdvertisingSourceRecord)
            await connection.execute(upsert(connection, status_table, self._values(state), self._keys()))

    @staticmethod
    def _keys() -> tuple[str, ...]:
        return "store_slug", "source", "period_from", "period_to"

    @staticmethod
    def _values(state: AdvertisingSourceStatus) -> dict[str, object]:
        return {
            "store_slug": state.store_slug,
            "source": state.source.value,
            "period_from": state.period.start.isoformat(),
            "period_to": state.period.end.isoformat(),
            "payload": state.model_dump_json(),
        }

    async def fail(self, state: AdvertisingSourceStatus) -> None:
        table = table_for(OzonAdvertisingSourceRecord)
        async with self.database.transaction(write=True) as connection:
            previous = (
                await connection.execute(
                    select(table.c.payload).where(
                        table.c.store_slug == state.store_slug,
                        table.c.source == state.source,
                        table.c.period_from == state.period.start.isoformat(),
                        table.c.period_to == state.period.end.isoformat(),
                    )
                )
            ).scalar_one_or_none()
            if previous:
                saved = AdvertisingSourceStatus.model_validate_json(previous)
                state = state.model_copy(update={"snapshot": saved.snapshot})
            await connection.execute(upsert(connection, table, self._values(state), self._keys()))

    async def load(
        self, stores: tuple[str, ...], period: AdvertisingPeriod
    ) -> tuple[AdvertisingSourceStatus, ...]:
        table = table_for(OzonAdvertisingSourceRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table.c.payload).where(
                    table.c.store_slug.in_(stores),
                    table.c.period_from == period.start.isoformat(),
                    table.c.period_to == period.end.isoformat(),
                )
            )
            return tuple(AdvertisingSourceStatus.model_validate_json(row.payload) for row in rows)

    async def report(self, store: str, key: str) -> SavedPerformanceReport | None:
        table = table_for(OzonAdvertisingReportRecord)
        async with self.database.transaction() as connection:
            row = (
                await connection.execute(
                    select(table.c.uuid, table.c.pending).where(
                        table.c.store_slug == store,
                        table.c.request_key == key,
                    )
                )
            ).one_or_none()
            return SavedPerformanceReport(uuid=row.uuid, pending=bool(row.pending)) if row else None

    async def pending_reports(self, stores: tuple[str, ...]) -> tuple[PendingPerformanceReport, ...]:
        table = table_for(OzonAdvertisingReportRecord)
        async with self.database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(table.c.store_slug, table.c.request_key, table.c.uuid).where(
                        table.c.store_slug.in_(stores), table.c.pending == 1
                    )
                )
            ).mappings()
            return tuple(PendingPerformanceReport.model_validate(row) for row in rows)

    async def forget_report(self, store: str, key: str) -> None:
        table = table_for(OzonAdvertisingReportRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                delete(table).where(table.c.store_slug == store, table.c.request_key == key)
            )

    async def response(self, store: str, key: str) -> bytes | None:
        table = table_for(OzonAdvertisingResponseRecord)
        async with self.database.transaction() as connection:
            payload = (
                await connection.execute(
                    select(table.c.payload).where(table.c.store_slug == store, table.c.request_key == key)
                )
            ).scalar_one_or_none()
        return payload.encode() if payload is not None else None

    async def save_response(
        self, store: str, key: str, request: PerformanceReportRequest, payload: bytes
    ) -> None:
        table = table_for(OzonAdvertisingResponseRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    table,
                    {
                        "store_slug": store,
                        "request_key": key,
                        "request_payload": request.model_dump_json(),
                        "payload": payload.decode(),
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    ("store_slug", "request_key"),
                )
            )

    async def blocked_until(self, store: str) -> datetime | None:
        table = table_for(OzonAdvertisingCooldownRecord)
        async with self.database.transaction() as connection:
            value = (
                await connection.execute(select(table.c.blocked_until).where(table.c.store_slug == store))
            ).scalar_one_or_none()
        return datetime.fromisoformat(value) if value else None

    async def block_exports(self, store: str, until: datetime) -> None:
        table = table_for(OzonAdvertisingCooldownRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    table,
                    {"store_slug": store, "blocked_until": until.isoformat()},
                    ("store_slug",),
                )
            )

    async def record_request(self, event: PerformanceRequestEvent) -> None:
        table = table_for(OzonAdvertisingRequestRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                insert(table).values(
                    id=event.request_id or uuid4().hex,
                    store_slug=event.store_slug,
                    method=event.method.value,
                    path=event.path,
                    period_from=event.period.start.isoformat() if event.period else None,
                    period_to=event.period.end.isoformat() if event.period else None,
                    campaign_count=event.campaign_count,
                    export_units=event.export_units,
                    status_code=event.status_code,
                    error=event.error.value if event.error else None,
                    attempted_at=event.attempted_at.isoformat(),
                )
            )

    async def overlapping(
        self, stores: tuple[str, ...], period: AdvertisingPeriod
    ) -> tuple[AdvertisingSourceStatus, ...]:
        table = table_for(OzonAdvertisingSourceRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table.c.payload).where(
                    table.c.store_slug.in_(stores),
                    table.c.period_from <= period.end.isoformat(),
                    table.c.period_to >= period.start.isoformat(),
                )
            )
            return tuple(AdvertisingSourceStatus.model_validate_json(row.payload) for row in rows)

    async def record_report(self, store: str, key: str, uuid: str, *, pending: bool) -> None:
        table = table_for(OzonAdvertisingReportRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    table,
                    {
                        "store_slug": store,
                        "request_key": key,
                        "uuid": uuid,
                        "pending": int(pending),
                        "updated_at": datetime.now(UTC).isoformat(),
                    },
                    ("store_slug", "request_key"),
                )
            )
