import asyncio
from datetime import UTC, datetime

from app.container import ozon_logistics_service
from app.core.constants import OZON_LOGISTICS
from app.core.logging_config import tagged_logger
from app.dto.ozon_logistics import LogisticsSyncResult, moscow_day
from app.infrastructure.async_http import store_context
from app.infrastructure.async_logistics_repository import AsyncLogisticsRepository
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks

logger = tagged_logger(__name__, "jobs", "ozon", "ozon_logistics")


async def sync_all(stores: tuple[str, ...], resources: AsyncResources) -> LogisticsSyncResult:
    results: list[LogisticsSyncResult] = []

    async def sync(store: str) -> None:
        context = store_context.set(store)
        try:
            async with async_locks.hold(OZON_LOGISTICS.lock_prefix + store, resources.database):
                try:
                    service = ozon_logistics_service(resources, store)
                except Exception as error:
                    await AsyncLogisticsRepository(resources.database).fail(
                        store, moscow_day(datetime.now(UTC)), type(error).__name__
                    )
                    raise
                result = await service.sync()
                results.append(result)
        except Exception as error:
            logger.exception("ozon_logistics_store_failed store=%s", store)
            results.append(LogisticsSyncResult(stores=1, error=f"{store}: {type(error).__name__}"))
        finally:
            store_context.reset(context)

    async with asyncio.TaskGroup() as group:
        for store in stores:
            group.create_task(sync(store))
    return LogisticsSyncResult(
        stores=sum(item.stores for item in results),
        products=sum(item.products for item in results),
        skipped=sum(item.skipped for item in results),
        error="; ".join(item.error for item in results if item.error) or None,
    )
