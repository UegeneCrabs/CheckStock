import asyncio
import logging

from app.container import ozon_cabinet_service
from app.core.constants import OZON_CABINET
from app.dto.ozon_cabinet import OzonBuyoutSyncResult
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks

logger = logging.getLogger(__name__)


async def sync_all(stores: tuple[str, ...], resources: AsyncResources) -> OzonBuyoutSyncResult:
    results: list[OzonBuyoutSyncResult] = []

    async def sync(store: str) -> None:
        try:
            async with async_locks.hold(OZON_CABINET.lock_prefix + store, resources.database):
                results.append(await ozon_cabinet_service(resources).sync_buyout(store))
        except Exception as error:
            logger.exception("ozon_buyout_store_failed store=%s", store)
            results.append(OzonBuyoutSyncResult(stores=1, error=f"{store}: {type(error).__name__}"))

    async with asyncio.TaskGroup() as group:
        for store in stores:
            group.create_task(sync(store))
    return OzonBuyoutSyncResult(
        stores=sum(item.stores for item in results),
        products=sum(item.products for item in results),
        error="; ".join(item.error for item in results if item.error) or None,
    )
