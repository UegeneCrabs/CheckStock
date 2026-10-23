import asyncio
from datetime import datetime, timedelta
from functools import partial

from app.core.constants import JOBS, OZON_ADVERTISING, OZON_ORDERS, OZON_PRICES, TIME, Marketplace
from app.dto.jobs import AsyncJobResult
from app.dto.orders import OrderPeriod, OrderSyncResult
from app.economics import ozon_advertising_breakdown, ozon_prices
from app.infrastructure.async_resources import AsyncResources
from app.jobs.scheduling import AsyncBackgroundJob
from app.jobs.settings import configuration_from_rows
from app.orders.ozon_async import sync_configured_store


async def enabled(name: str, resources: AsyncResources) -> bool:
    config = configuration_from_rows(name, await resources.jobs.settings(name))
    return config.effective_enabled and (not config.targets or bool(config.enabled_target_count))


async def run(name: str, resources: AsyncResources) -> AsyncJobResult:
    config = configuration_from_rows(name, await resources.jobs.settings(name))
    stores = tuple(target.store_slug for target in config.targets if target.enabled)
    if name == JOBS.ozon_prices_close.name:
        return await ozon_prices.close_days(resources)
    if name == JOBS.ozon_prices.name:
        return await ozon_prices.sync_all(stores, resources=resources)
    if name == JOBS.ozon_advertising_breakdown.name:
        return await ozon_advertising_breakdown.sync_all(stores, resources=resources)
    today = datetime.now(TIME.moscow_timezone).date()
    period = OrderPeriod(
        start=today - timedelta(days=OZON_ORDERS.lookback_days - 1), end=today + timedelta(days=1)
    )
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(sync_configured_store(store, period, resources)) for store in stores]
    return OrderSyncResult(stores=tuple(result for task in tasks if (result := task.result()) is not None))


def daily_close_delay() -> float:
    now = datetime.now(TIME.moscow_timezone)
    target = now.replace(hour=OZON_PRICES.close_hour_msk, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def jobs(ready: asyncio.Event | None = None) -> tuple[AsyncBackgroundJob, ...]:
    return tuple(
        AsyncBackgroundJob(
            metadata.name,
            partial(run, metadata.name),
            daily_close_delay if metadata == JOBS.ozon_prices_close else partial(float, interval),
            ready_event=ready,
            is_enabled=partial(enabled, metadata.name),
            retry_delays=OZON_ORDERS.retry_delays_seconds,
            interval_from_start=metadata != JOBS.ozon_prices_close,
        )
        for metadata, interval in (
            (JOBS.ozon_orders, OZON_ORDERS.interval_seconds),
            (JOBS.ozon_prices, OZON_PRICES.interval_seconds),
            (JOBS.ozon_prices_close, TIME.seconds_per_day),
            (JOBS.ozon_advertising_breakdown, OZON_ADVERTISING.interval_seconds),
        )
        if Marketplace.OZON in metadata.marketplaces
    )
