import asyncio
import logging
from collections import defaultdict

from app.application.ozon_analytics import OzonAnalyticsAccountStore
from app.application.ozon_limits import account_digest
from app.container import ozon_analytics_service
from app.core.constants import OZON_ANALYTICS
from app.dto.orders import OzonCredentials
from app.dto.ozon_analytics import OzonAnalyticsApiError, OzonAnalyticsSyncResult
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks
from app.ozon import tokens
from app.ozon.async_analytics import AsyncOzonAnalyticsClient

logger = logging.getLogger(__name__)


async def sync_all(stores: tuple[str, ...], resources: AsyncResources) -> OzonAnalyticsSyncResult:
    service = ozon_analytics_service(resources.database)
    results: list[OzonAnalyticsSyncResult] = []
    accounts: dict[str, list[OzonAnalyticsAccountStore]] = defaultdict(list)

    async def sync(store: str) -> None:
        try:
            async with async_locks.hold(OZON_ANALYTICS.sources_lock_prefix + store, resources.database):
                products = await service.capture_reference(store)
                results.append(OzonAnalyticsSyncResult(stores=1, products=products))
                client_id, api_key = tokens.get_credentials(store)
                client = AsyncOzonAnalyticsClient(
                    resources.http, OzonCredentials(client_id=client_id, api_key=api_key)
                )
                accounts[account_digest(client_id)].append(
                    OzonAnalyticsAccountStore(
                        store=store, client=client, catalog=await resources.catalog.get_ozon_articles(store)
                    )
                )
                await service.transit(store, client)
        except Exception as error:
            reason = str(error.reason) if isinstance(error, OzonAnalyticsApiError) else type(error).__name__
            status = error.status if isinstance(error, OzonAnalyticsApiError) else None
            logger.exception(
                "ozon_analytics_sources_failed store=%s reason=%s status=%s", store, reason, status
            )
            detail = f"{reason}:{status}" if status is not None else reason
            results.append(OzonAnalyticsSyncResult(error=f"{store}: {detail}"))

    async with asyncio.TaskGroup() as group:
        for store in stores:
            group.create_task(sync(store))

    async def funnel(account: str, targets: tuple[OzonAnalyticsAccountStore, ...]) -> None:
        try:
            results.append(await service.sync_funnel_account(account, targets))
        except Exception as error:
            logger.exception("ozon_analytics_funnel_failed")
            results.append(OzonAnalyticsSyncResult(error=type(error).__name__))

    async with asyncio.TaskGroup() as group:
        for account, targets in accounts.items():
            group.create_task(funnel(account, tuple(targets)))
    return OzonAnalyticsSyncResult(
        stores=sum(item.stores for item in results),
        products=sum(item.products for item in results),
        attempts=sum(item.attempts for item in results),
        deferred=sum(item.deferred for item in results),
        error="; ".join(item.error for item in results if item.error) or None,
    )


async def snapshot_all(stores: tuple[str, ...], resources: AsyncResources) -> OzonAnalyticsSyncResult:
    service = ozon_analytics_service(resources.database)
    results: list[OzonAnalyticsSyncResult] = []

    async def capture(store: str) -> None:
        try:
            async with async_locks.hold(OZON_ANALYTICS.stock_lock_prefix + store, resources.database):
                results.append(OzonAnalyticsSyncResult(stores=1, products=await service.capture_stock(store)))
        except Exception as error:
            logger.exception("ozon_analytics_stock_failed store=%s", store)
            results.append(OzonAnalyticsSyncResult(error=f"{store}: {type(error).__name__}"))

    async with asyncio.TaskGroup() as group:
        for store in stores:
            group.create_task(capture(store))
    return OzonAnalyticsSyncResult(
        stores=sum(item.stores for item in results),
        products=sum(item.products for item in results),
        error="; ".join(item.error for item in results if item.error) or None,
    )
