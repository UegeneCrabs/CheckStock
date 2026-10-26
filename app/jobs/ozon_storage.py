import asyncio
import logging
from datetime import UTC, datetime

from app.container import ozon_storage_service
from app.core.constants import OZON_STORAGE
from app.dto.ozon_logistics import moscow_day
from app.dto.ozon_storage import StorageSyncResult
from app.infrastructure.async_http import store_context
from app.infrastructure.async_resources import AsyncResources
from app.infrastructure.async_storage_repository import AsyncStorageRepository
from app.jobs import async_locks

logger = logging.getLogger(__name__)


async def sync_all(stores: tuple[str, ...], resources: AsyncResources) -> StorageSyncResult:
    results: list[StorageSyncResult] = []

    async def sync(store: str) -> None:
        context = store_context.set(store)
        try:
            async with async_locks.hold(OZON_STORAGE.lock_prefix + store, resources.database):
                try:
                    service = ozon_storage_service(resources, store)
                except Exception as error:
                    await AsyncStorageRepository(resources.database).fail(
                        store, moscow_day(datetime.now(UTC)), type(error).__name__
                    )
                    raise
                result = await service.sync()
                results.append(result)
        except Exception as error:
            logger.exception("ozon_storage_store_failed store=%s", store)
            results.append(StorageSyncResult(stores=1, error=f"{store}: {type(error).__name__}"))
        finally:
            store_context.reset(context)

    async with asyncio.TaskGroup() as group:
        for store in stores:
            group.create_task(sync(store))
    return StorageSyncResult(
        stores=sum(item.stores for item in results),
        products=sum(item.products for item in results),
        skipped=sum(item.skipped for item in results),
        error="; ".join(item.error for item in results if item.error) or None,
    )
