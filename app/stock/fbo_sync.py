import asyncio
from pathlib import Path

from sqlalchemy import select

from app.application.fbo_stock import FboStockService
from app.core.logging_config import tagged_logger
from app.dto.fbo_stock import FboTarget
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.fbo_stock_repository import FboStockRepository
from app.infrastructure.orm import InboundSupplySnapshotRecord

logger = tagged_logger(__name__, "stock", "fbo_sync")


async def reconcile_saved(database: AsyncDatabase) -> None:
    snapshots = table_for(InboundSupplySnapshotRecord)
    async with database.transaction() as connection:
        rows = await connection.execute(select(snapshots.c.store_slug, snapshots.c.marketplace))
        targets = tuple(FboTarget.model_validate(row) for row in rows.mappings())

    async def reconcile(target: FboTarget) -> None:
        try:
            await FboStockService(FboStockRepository(database)).sync(target)
        except Exception:
            logger.exception(
                "fbo_stock_reconcile_failed store=%s marketplace=%s", target.store_slug, target.marketplace
            )

    async with asyncio.TaskGroup() as group:
        for target in targets:
            group.create_task(reconcile(target))


def reconcile_target(target: FboTarget, path: Path) -> None:
    async def run() -> None:
        database = AsyncDatabase(path=path)
        try:
            await FboStockService(FboStockRepository(database)).sync(target)
        finally:
            await database.close()

    asyncio.run(run())
