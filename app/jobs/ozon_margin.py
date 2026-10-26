import asyncio
import logging

from app.container import ozon_margin_service
from app.core.constants import OZON_MARGIN
from app.dto.ozon_margin import OzonMarginSyncResult
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks

logger = logging.getLogger(__name__)


async def sync_all(stores: tuple[str, ...], resources: AsyncResources) -> OzonMarginSyncResult:
    results: list[OzonMarginSyncResult] = []

    async def sync(store: str) -> None:
        try:
            async with async_locks.hold(OZON_MARGIN.lock_prefix + store, resources.database):
                results.append(await ozon_margin_service(resources).capture(store))
        except Exception as error:
            logger.exception("ozon_margin_store_failed store=%s", store)
            results.append(OzonMarginSyncResult(error=f"{store}: {type(error).__name__}"))

    async with asyncio.TaskGroup() as group:
        for store in stores:
            group.create_task(sync(store))
    return OzonMarginSyncResult(
        products=sum(item.products for item in results),
        error="; ".join(item.error for item in results if item.error) or None,
    )
