import json

from pydantic import TypeAdapter
from sqlalchemy import insert, select

from app.core.constants import SupplyStockAction
from app.dto.supply_arrivals import (
    SupplyArrival,
    SupplyArrivalMissingError,
    SupplyStockCommand,
    SupplyStockConflictError,
    SupplyStockState,
)
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.orm import ActivityLogRecord, SupplyArrivalsSnapshotRecord, SupplyArrivalStockRecord
from app.stock.supply_arrival_identity import arrival_id

STOCK = table_for(SupplyArrivalStockRecord)
SNAPSHOTS = table_for(SupplyArrivalsSnapshotRecord)
ACTIVITY = table_for(ActivityLogRecord)
ARRIVALS = TypeAdapter(tuple[SupplyArrival, ...])
STATE_COLUMNS = (STOCK.c.supply_id, STOCK.c.checked, STOCK.c.version, STOCK.c.marked_at, STOCK.c.marked_by)


class SupplyArrivalStockRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def states(self, source_key: str) -> tuple[SupplyStockState, ...]:
        async with self._database.transaction() as connection:
            records = await connection.execute(select(*STATE_COLUMNS).where(STOCK.c.source_key == source_key))
            return tuple(SupplyStockState.model_validate(row) for row in records.mappings())

    async def save(self, command: SupplyStockCommand) -> SupplyStockState:
        async with self._database.transaction(write=True) as connection:
            payload = await connection.scalar(
                select(SNAPSHOTS.c.payload_json)
                .where(SNAPSHOTS.c.source_key == command.source_key)
                .with_for_update()
            )
            arrivals = ARRIVALS.validate_json(payload or "[]")
            matches = [row for row in arrivals if arrival_id(command.source_key, row) == command.supply_id]
            if not matches:
                raise SupplyArrivalMissingError
            if len(matches) != 1:
                raise SupplyStockConflictError
            record = (
                (
                    await connection.execute(
                        select(*STATE_COLUMNS).where(
                            STOCK.c.source_key == command.source_key, STOCK.c.supply_id == command.supply_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            previous = (
                SupplyStockState.model_validate(record)
                if record is not None
                else SupplyStockState(supply_id=command.supply_id)
            )
            if previous.version != command.change.expected_version:
                raise SupplyStockConflictError
            if previous.checked == command.change.checked:
                return previous
            state = SupplyStockState(
                supply_id=command.supply_id,
                checked=command.change.checked,
                version=previous.version + 1,
                marked_at=command.occurred_at if command.change.checked else None,
                marked_by=command.user_name if command.change.checked else None,
            )
            await connection.execute(
                upsert(
                    connection,
                    STOCK,
                    {
                        **state.model_dump(mode="json"),
                        "checked": int(state.checked),
                        "source_key": command.source_key,
                        "marked_by_id": command.user_id if state.checked else None,
                    },
                    ("source_key", "supply_id"),
                )
            )
            await connection.execute(
                insert(ACTIVITY).values(
                    user_id=command.user_id,
                    user_name=command.user_name,
                    action=SupplyStockAction.MARK if state.checked else SupplyStockAction.UNMARK,
                    details=json.dumps(
                        {
                            "source_key": command.source_key,
                            "supply_id": command.supply_id,
                            "project": matches[0].project,
                            "order": matches[0].order,
                            "previous": previous.model_dump(mode="json"),
                            "current": state.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                    created_at=command.occurred_at.isoformat(),
                )
            )
            return state
