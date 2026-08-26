import asyncio
import logging
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from app import auth, db, rnp_analytics, stock_history, stock_sheet_export, unit_economics_1c
from app import decision_center as decision_service
from app import sales as sales_service
from app import unit_economics_1c_advertising as advertising_sync
from app import unit_economics_1c_reference_data as unit_reference_sync
from app import unit_economics_1c_source_data as unit_source_sync
from app.config import settings
from app.domain import MOSCOW_TIMEZONE
from app.dto.system import SyncFailure, SyncGroupReport, TokenRefreshResult
from app.ozon import catalog as ozon_catalog
from app.ozon import sync as ozon_sync
from app.scheduling import BackgroundJob, run_background_job
from app.wb import catalog as wb_catalog
from app.wb import funnel_orders as wb_funnel_orders
from app.wb import sync as wb_sync
from app.wb import token_watch
from app.yandex import catalog as ya_catalog
from app.yandex import sync as ya_sync

logger = logging.getLogger(__name__)

SyncTarget = tuple[str, Callable[[], object]]


def _seconds_until_next_run(hour: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _seconds_until_next_moscow_run(hour: int) -> float:
    now = datetime.now(MOSCOW_TIMEZONE)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _run_sync_group(group: str, targets: Iterable[SyncTarget]) -> SyncGroupReport:
    succeeded: list[str] = []
    failed: list[SyncFailure] = []
    for target, sync in targets:
        try:
            sync()
        except Exception as error:
            logger.exception(
                "sync_target_failed group=%s target=%s error_type=%s",
                group,
                target,
                type(error).__name__,
            )
            failed.append(SyncFailure(target=target, error_type=type(error).__name__))
        else:
            succeeded.append(target)
    return SyncGroupReport(group=group, succeeded=tuple(succeeded), failed=tuple(failed))


def _sync_catalogs() -> SyncGroupReport:
    return _run_sync_group(
        "catalogs",
        (
            ("WB", wb_catalog.sync_all),
            ("OZON", ozon_catalog.sync_all),
            ("YANDEX MARKET", ya_catalog.sync_all),
        ),
    )


def _sync_stocks() -> SyncGroupReport:
    return _run_sync_group(
        "stocks",
        (
            ("WB", wb_sync.sync_all),
            ("OZON", ozon_sync.sync_all),
            ("YANDEX MARKET", ya_sync.sync_all),
        ),
    )


def _sync_sales_and_advertising() -> SyncGroupReport:
    return _run_sync_group(
        "sales_and_advertising",
        (
            ("orders", sales_service.sync_all),
            ("WB advertising", advertising_sync.sync_all),
        ),
    )


def _refresh_token_info() -> TokenRefreshResult:
    last_check = db.get_last_token_check()
    if not token_watch.should_refresh(last_check):
        return TokenRefreshResult(refreshed=False)
    token_watch.refresh_token_info()
    return TokenRefreshResult(refreshed=True)


def _daily_delay(hour: int) -> Callable[[], float]:
    return lambda: _seconds_until_next_run(hour)


def _moscow_daily_delay(hour: int) -> Callable[[], float]:
    return lambda: _seconds_until_next_moscow_run(hour)


def _fixed_delay(seconds: int) -> Callable[[], float]:
    return lambda: seconds


def _funnel_jobs() -> tuple[BackgroundJob, ...]:
    return (
        BackgroundJob(
            "wb_funnel_orders_sync",
            wb_funnel_orders.sync_all,
            _fixed_delay(settings.wb_funnel_orders_sync_interval_seconds),
            startup_delay_seconds=5,
        ),
    )


def _unit_economics_1c_price_jobs() -> tuple[BackgroundJob, ...]:
    return (
        BackgroundJob(
            "unit_economics_1c_sync",
            unit_economics_1c.sync_prices_due,
            _fixed_delay(settings.unit_economics_1c_price_sync_interval_seconds),
            startup_delay_seconds=settings.unit_economics_1c_price_sync_startup_delay_seconds,
        ),
        BackgroundJob(
            "unit_economics_1c_wallet_sync",
            unit_economics_1c.sync_wallet_prices,
            _fixed_delay(settings.unit_economics_1c_wallet_sync_interval_seconds),
            startup_delay_seconds=(settings.unit_economics_1c_price_sync_startup_delay_seconds + 30),
        ),
    )


def _wb_stock_history_jobs(catalog_ready: asyncio.Event) -> tuple[BackgroundJob, ...]:
    return (
        BackgroundJob(
            "marketplace_stock_sync_and_history_23_msk",
            stock_history.sync_marketplaces_and_save_daily_history,
            _moscow_daily_delay(23),
            startup_delay_seconds=_seconds_until_next_moscow_run(23),
            ready_event=catalog_ready,
        ),
        BackgroundJob(
            "fulfillment_stock_history_00_msk",
            stock_history.save_previous_day_fulfillment_history,
            _moscow_daily_delay(0),
            startup_delay_seconds=_seconds_until_next_moscow_run(0),
            ready_event=catalog_ready,
        ),
    )


def _jobs(catalog_ready: asyncio.Event) -> tuple[BackgroundJob, ...]:
    return (
        BackgroundJob(
            "catalog_sync",
            _sync_catalogs,
            _daily_delay(settings.catalog_sync_hour),
            on_attempt=catalog_ready.set,
        ),
        BackgroundJob(
            "stock_sync",
            _sync_stocks,
            _fixed_delay(settings.auto_sync_interval_seconds),
            ready_event=catalog_ready,
        ),
        *_wb_stock_history_jobs(catalog_ready),
        BackgroundJob(
            "wb_token_check",
            _refresh_token_info,
            _fixed_delay(settings.token_check_interval_seconds),
        ),
        BackgroundJob(
            "sales_sync",
            _sync_sales_and_advertising,
            _fixed_delay(settings.sales_sync_interval_seconds),
            startup_delay_seconds=settings.sales_sync_startup_delay_seconds,
        ),
        *_funnel_jobs(),
        BackgroundJob(
            "rnp_analytics_sync",
            rnp_analytics.sync_current,
            _fixed_delay(settings.rnp_analytics_sync_interval_seconds),
            startup_delay_seconds=settings.rnp_sync_startup_delay_seconds,
            ready_event=catalog_ready,
        ),
        BackgroundJob(
            "stock_sheet_export",
            stock_sheet_export.run_due,
            _fixed_delay(60),
            ready_event=catalog_ready,
        ),
        BackgroundJob(
            "unit_economics_1c_source_sync",
            unit_source_sync.sync_all,
            _moscow_daily_delay(settings.unit_economics_1c_source_sync_hour),
            ready_event=catalog_ready,
        ),
        BackgroundJob(
            "unit_economics_1c_reference_sync",
            unit_reference_sync.sync_due,
            _fixed_delay(24 * 60 * 60),
            startup_delay_seconds=30,
            ready_event=catalog_ready,
        ),
    )


def _initialize_application() -> None:
    db.init_db()
    decision_service.init_schema()
    rnp_analytics.init_schema()
    db.seed_defaults()
    stock_sheet_export.ensure_defaults()
    auth.seed_superadmin()


@asynccontextmanager
async def lifespan(application: FastAPI):
    await run_in_threadpool(_initialize_application)
    catalog_ready = asyncio.Event()
    jobs = _jobs(catalog_ready) if settings.background_sync_enabled else ()
    if not settings.background_sync_enabled and settings.funnel_orders_sync_enabled:
        jobs += _funnel_jobs()
    if settings.unit_economics_1c_price_sync_enabled:
        jobs += _unit_economics_1c_price_jobs()
    tasks = [asyncio.create_task(run_background_job(job), name=f"checkstock:{job.name}") for job in jobs]
    application.state.background_tasks = tasks
    logger.info(
        "application_started background_jobs=%s database=%s",
        len(tasks),
        "postgresql" if settings.database_url else settings.database_path,
    )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("application_stopped background_jobs=%s", len(tasks))
