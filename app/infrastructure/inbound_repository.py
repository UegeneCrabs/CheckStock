import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from sqlalchemy import or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.inbound_supplies import Target
from app.core.constants import INBOUND_SCHEDULE, DatabaseDialect
from app.dto.inbound_supplies import InboundSnapshot, InboundSupply
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.orm import InboundSupplySnapshotRecord as SnapshotRecord
from app.infrastructure.orm import StockItemRecord


class AsyncSqlAlchemyInboundRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self.database = database

    async def previous(self, target: Target) -> tuple[InboundSupply, ...]:
        table = table_for(SnapshotRecord)
        async with self.database.transaction() as connection:
            payload = await connection.scalar(
                select(table.c.payload_json).where(
                    table.c.store_slug == target[0], table.c.marketplace == target[1]
                )
            )
        return tuple(InboundSupply.model_validate(row) for row in json.loads(payload or "[]"))

    async def claim(self, target: Target, token: str, now: datetime, *, recover: bool) -> bool:
        table = table_for(SnapshotRecord)
        async with self.database.transaction(write=True) as connection:
            insert = pg_insert if self.database.dialect == DatabaseDialect.POSTGRESQL else sqlite_insert
            await connection.execute(
                insert(table)
                .values(store_slug=target[0], marketplace=target[1])
                .on_conflict_do_nothing(index_elements=("store_slug", "marketplace"))
            )
            eligible = or_(table.c.run_token.is_(None), table.c.lease_until < now.isoformat())
            cooldown = or_(
                table.c.last_attempt.is_(None),
                table.c.last_attempt
                < (now - timedelta(seconds=INBOUND_SCHEDULE.claim_cooldown_seconds)).isoformat(),
            )
            condition = (
                (eligible & cooldown)
                if not recover
                else (table.c.run_token.is_not(None) | (eligible & cooldown))
            )
            claimed = await connection.scalar(
                update(table)
                .where(table.c.store_slug == target[0], table.c.marketplace == target[1], condition)
                .values(
                    run_token=token,
                    lease_until=(now + timedelta(hours=INBOUND_SCHEDULE.lease_hours)).isoformat(),
                    last_attempt=now.isoformat(),
                    status="running",
                )
                .returning(table.c.run_token)
            )
        return claimed == token

    async def finish(
        self,
        target: Target,
        token: str,
        now: datetime,
        *,
        supplies: tuple[InboundSupply, ...] | None = None,
        status: str = "ok",
        error: str = "",
    ) -> bool:
        table = table_for(SnapshotRecord)
        values: dict[str, str | None] = {
            "status": status,
            "error": error,
            "last_finished": now.isoformat(),
            "run_token": None,
            "lease_until": None,
        }
        if supplies is not None:
            values["payload_json"] = json.dumps(
                [s.model_dump(mode="json", exclude_computed_fields=True) for s in supplies],
                ensure_ascii=False,
            )
            values["last_success"] = now.isoformat()
        async with self.database.transaction(write=True) as connection:
            saved = await connection.scalar(
                update(table)
                .where(
                    table.c.store_slug == target[0],
                    table.c.marketplace == target[1],
                    table.c.run_token == token,
                )
                .values(**values)
                .returning(table.c.store_slug)
            )
        return saved is not None

    async def enrich(self, target: Target, supplies: tuple[InboundSupply, ...]) -> tuple[InboundSupply, ...]:
        table = table_for(StockItemRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table.c.article, table.c.name, table.c.barcode).where(
                    table.c.store_slug == target[0], table.c.marketplace == target[1]
                )
            )
            catalog = {row.article: (row.name, row.barcode) for row in rows}
        return tuple(
            supply.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(
                            update={
                                "name": item.name or catalog[item.article][0],
                                "barcode": item.barcode or catalog[item.article][1],
                            }
                        )
                        if item.article in catalog
                        else item
                        for item in supply.items
                    )
                }
            )
            for supply in supplies
        )


class SqlAlchemyInboundRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self.session_factory = session_factory

    def read(self, targets: Sequence[Target]) -> list[InboundSnapshot]:
        if not targets:
            return []
        with self.session_factory() as session:
            rows = session.scalars(
                select(SnapshotRecord).where(
                    tuple_(SnapshotRecord.store_slug, SnapshotRecord.marketplace).in_(targets)
                )
            ).all()
            by_target = {(row.store_slug, row.marketplace): row for row in rows}
            result = []
            for store, marketplace in targets:
                row = by_target.get((store, marketplace))
                result.append(
                    InboundSnapshot(
                        store_slug=store,
                        marketplace=marketplace,
                        **(
                            {
                                "status": row.status,
                                "last_attempt": row.last_attempt,
                                "last_success": row.last_success,
                                "last_finished": row.last_finished,
                                "error": row.error,
                                "supplies": tuple(
                                    InboundSupply.model_validate(item)
                                    for item in json.loads(row.payload_json)
                                ),
                            }
                            if row
                            else {}
                        ),
                    )
                )
            return result

    def release_interrupted_claims(self, targets: Sequence[Target]) -> int:
        """Called under the shared job lock; keep the previous snapshot intact."""
        if not targets:
            return 0
        with self.session_factory() as session:
            result = session.execute(
                update(SnapshotRecord)
                .where(
                    tuple_(SnapshotRecord.store_slug, SnapshotRecord.marketplace).in_(targets),
                    SnapshotRecord.run_token.is_not(None),
                )
                .values(
                    run_token=None,
                    lease_until=None,
                    last_attempt=None,
                    status="error",
                    error="Предыдущее обновление прервано. Повторяем выгрузку поставок.",
                )
            )
            session.commit()
            return result.rowcount

    def claim(self, target: Target, token: str, now: datetime) -> bool:
        with self.session_factory() as session:
            if session.get(SnapshotRecord, target) is None:
                session.add(SnapshotRecord(store_slug=target[0], marketplace=target[1]))
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
            result = session.execute(
                update(SnapshotRecord)
                .where(
                    SnapshotRecord.store_slug == target[0],
                    SnapshotRecord.marketplace == target[1],
                    or_(SnapshotRecord.run_token.is_(None), SnapshotRecord.lease_until < now.isoformat()),
                    or_(
                        SnapshotRecord.last_attempt.is_(None),
                        SnapshotRecord.last_attempt < (now - timedelta(seconds=60)).isoformat(),
                    ),
                )
                .values(
                    run_token=token,
                    lease_until=(now + timedelta(hours=2)).isoformat(),
                    last_attempt=now.isoformat(),
                    status="running",
                )
            )
            session.commit()
            return result.rowcount == 1

    def finish(
        self,
        target: Target,
        token: str,
        now: datetime,
        *,
        supplies: tuple[InboundSupply, ...] | None = None,
        status: str = "ok",
        error: str = "",
    ) -> bool:
        values = {
            "status": status,
            "error": error,
            "last_finished": now.isoformat(),
            "run_token": None,
            "lease_until": None,
        }
        if supplies is not None:
            values.update(
                payload_json=json.dumps(
                    [supply.model_dump(mode="json", exclude_computed_fields=True) for supply in supplies],
                    ensure_ascii=False,
                ),
                last_success=now.isoformat(),
            )
        with self.session_factory() as session:
            result = session.execute(
                update(SnapshotRecord)
                .where(
                    SnapshotRecord.store_slug == target[0],
                    SnapshotRecord.marketplace == target[1],
                    SnapshotRecord.run_token == token,
                )
                .values(**values)
            )
            session.commit()
            return result.rowcount == 1

    def enrich(self, target: Target, supplies: tuple[InboundSupply, ...]) -> tuple[InboundSupply, ...]:
        with self.session_factory() as session:
            catalog = session.scalars(
                select(StockItemRecord).where(
                    StockItemRecord.store_slug == target[0],
                    StockItemRecord.marketplace == target[1],
                )
            ).all()
            by_article = {row.article: row for row in catalog}
            by_barcode: dict[str, list] = {}
            for row in catalog:
                if row.barcode:
                    by_barcode.setdefault(row.barcode, []).append(row)
            result = []
            for supply in supplies:
                items = []
                for item in supply.items:
                    candidates = by_barcode.get(item.barcode, [])
                    product = (candidates[0] if len(candidates) == 1 else None) if target[1] == "WB" else None
                    product = product or by_article.get(item.article)
                    items.append(
                        item.model_copy(
                            update={
                                "article": product.article if product else item.article,
                                "name": item.name or (product.name if product else ""),
                                "barcode": item.barcode or (product.barcode if product else ""),
                            }
                        )
                    )
                result.append(supply.model_copy(update={"items": tuple(items)}))
            return tuple(result)
