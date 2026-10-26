import json

from sqlalchemy import select, tuple_

from app.dto.inbound_supplies import InboundSnapshot, InboundSupply
from app.dto.stock_pages import StockTarget
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.orm import InboundSupplySnapshotRecord


class InboundReadRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def snapshots(self, targets: tuple[StockTarget, ...]) -> tuple[InboundSnapshot, ...]:
        table = table_for(InboundSupplySnapshotRecord)
        snapshots = {}
        async with self.database.transaction() as connection:
            result = await connection.execute(
                select(table).where(
                    tuple_(table.c.store_slug, table.c.marketplace).in_(
                        tuple((target.store_slug, target.marketplace) for target in targets)
                    )
                )
            )
            for row in result.mappings():
                snapshots[(row["store_slug"], row["marketplace"])] = InboundSnapshot(
                    store_slug=row["store_slug"],
                    marketplace=row["marketplace"],
                    status=row["status"],
                    last_attempt=row["last_attempt"],
                    last_success=row["last_success"],
                    last_finished=row["last_finished"],
                    error=row["error"],
                    supplies=tuple(
                        InboundSupply.model_validate(item) for item in json.loads(row["payload_json"])
                    ),
                )
        return tuple(
            snapshots.get((target.store_slug, target.marketplace))
            or InboundSnapshot.model_validate(
                {
                    "store_slug": target.store_slug,
                    "marketplace": target.marketplace,
                }
            )
            for target in targets
        )
