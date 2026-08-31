import asyncio
import logging
from collections.abc import Callable, Iterable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool

from app import (
    auth,
    db,
    ftp_export,
    ftp_export_schedule,
    rnp_analytics,
    stock_history,
    stock_sheet_export,
    sync_settings,
    unit_economics_1c,
)
from app import decision_center as decision_service
from app import unit_economics_1c_advertising as advertising_sync
from app import unit_economics_1c_history as unit_margin_history
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
            failed.append(
                SyncFailure(
                    target=target,
                    error_type=type(error).__name__,
                    message=str(error).strip(),
                )
            )
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


def _sync_catalogs_configured() -> SyncGroupReport:
    return _run_sync_group(
        "catalogs",
        (
            (
                "WB",
                lambda: wb_catalog.sync_all(sync_settings.enabled_stores("catalog_sync", "WB")),
            ),
            (
                "OZON",
                lambda: ozon_catalog.sync_all(sync_settings.enabled_stores("catalog_sync", "OZON")),
            ),
            (
                "YANDEX MARKET",
                lambda: ya_catalog.sync_all(
                    sync_settings.enabled_stores("catalog_sync", "YANDEX MARKET")
                ),
            ),
        ),
    )


def _sync_stocks_configured() -> SyncGroupReport:
    return _run_sync_group(
        "stocks",
        (
            ("WB", lambda: wb_sync.sync_all(sync_settings.enabled_stores("stock_sync", "WB"))),
            (
                "OZON",
                lambda: ozon_sync.sync_all(sync_settings.enabled_stores("stock_sync", "OZON")),
            ),
            (
                "YANDEX MARKET",
                lambda: ya_sync.sync_all(
                    sync_settings.enabled_stores("stock_sync", "YANDEX MARKET")
                ),
            ),
        ),
    )


def _sync_wb_advertising() -> SyncGroupReport:
    return _run_sync_group(
        "wb_advertising",
        (("WB advertising", advertising_sync.sync_all),),
    )


def _sync_wb_advertising_configured() -> SyncGroupReport:
    stores = sync_settings.enabled_stores("wb_advertising_sync")
    return _run_sync_group(
        "wb_advertising",
        (("WB advertising", lambda: advertising_sync.sync_stores(stores)),),
    )


def _refresh_token_info(store_slugs: tuple[str, ...] | None = None) -> TokenRefreshResult:
    last_check = db.get_last_token_check()
    if not token_watch.should_refresh(last_check):
        return TokenRefreshResult(refreshed=False)
    if store_slugs is None:
        token_watch.refresh_token_info()
    else:
        token_watch.refresh_token_info(store_slugs)
    return TokenRefreshResult(refreshed=True)


def _refresh_token_info_configured() -> TokenRefreshResult:
    return _refresh_token_info(sync_settings.enabled_stores("wb_token_check"))


def _sync_funnel_configured(name: str, callback: Callable) -> object:
    return callback(sync_settings.enabled_stores(name))


def _sync_prices_due_configured() -> dict[str, dict]:
    return unit_economics_1c.sync_prices_due(
        sync_settings.enabled_stores("unit_economics_1c_sync")
    )


def _sync_wallet_prices_configured() -> dict[str, dict]:
    return unit_economics_1c.sync_wallet_prices(
        sync_settings.enabled_stores("unit_economics_1c_wallet_sync")
    )


def _save_daily_margin_configured() -> dict:
    return unit_margin_history.save_daily_margin_snapshots(
        store_slugs=sync_settings.enabled_stores(
            "unit_economics_1c_daily_margin_snapshot_00_msk"
        )
    )


def _sync_stock_history_configured() -> dict:
    name = "marketplace_stock_sync_and_history_23_msk"
    return stock_history.sync_marketplaces_and_save_daily_history(
        target_stores={
            marketplace: sync_settings.enabled_stores(name, marketplace)
            for marketplace in ("WB", "OZON", "YANDEX MARKET")
        }
    )


def _run_stock_sheet_export_configured() -> dict[str, dict]:
    return stock_sheet_export.run_due(
        store_slugs=sync_settings.enabled_stores("stock_sheet_export")
    )


def _job_enabled(name: str) -> bool:
    return sync_settings.has_enabled_targets(name)


def _daily_delay(hour: int) -> Callable[[], float]:
    return lambda: _seconds_until_next_run(hour)


def _moscow_daily_delay(hour: int) -> Callable[[], float]:
    return lambda: _seconds_until_next_moscow_run(hour)


def _fixed_delay(seconds: int) -> Callable[[], float]:
    return lambda: seconds


def _funnel_jobs() -> tuple[BackgroundJob, ...]:
    return (
        BackgroundJob(
            "wb_funnel_previous_day_close_00_msk",
            wb_funnel_orders.sync_previous_day_all,
            _moscow_daily_delay(0),
            startup_delay_seconds=_seconds_until_next_moscow_run(0),
            is_enabled=lambda: _job_enabled("wb_funnel_previous_day_close_00_msk"),
            run_callback=lambda: _sync_funnel_configured(
                "wb_funnel_previous_day_close_00_msk",
                wb_funnel_orders.sync_previous_day_all,
            ),
        ),
        BackgroundJob(
            "wb_funnel_weekly_metrics_sync",
            wb_funnel_orders.sync_weekly_metrics_all,
            _moscow_daily_delay(1),
            is_enabled=lambda: _job_enabled("wb_funnel_weekly_metrics_sync"),
            run_callback=lambda: _sync_funnel_configured(
                "wb_funnel_weekly_metrics_sync",
                wb_funnel_orders.sync_weekly_metrics_all,
            ),
        ),
        BackgroundJob(
            "wb_funnel_orders_sync",
            wb_funnel_orders.sync_all,
            _fixed_delay(settings.wb_funnel_orders_sync_interval_seconds),
            startup_delay_seconds=5,
            interval_from_start=True,
            is_enabled=lambda: _job_enabled("wb_funnel_orders_sync"),
            run_callback=lambda: _sync_funnel_configured(
                "wb_funnel_orders_sync",
                wb_funnel_orders.sync_all,
            ),
        ),
    )


def _unit_economics_1c_price_jobs() -> tuple[BackgroundJob, ...]:
    return (
        BackgroundJob(
            "unit_economics_1c_sync",
            unit_economics_1c.sync_prices_due,
            _fixed_delay(settings.unit_economics_1c_price_sync_interval_seconds),
            startup_delay_seconds=settings.unit_economics_1c_price_sync_startup_delay_seconds,
            is_enabled=lambda: _job_enabled("unit_economics_1c_sync"),
            run_callback=_sync_prices_due_configured,
        ),
        BackgroundJob(
            "unit_economics_1c_wallet_sync",
            unit_economics_1c.sync_wallet_prices,
            _fixed_delay(settings.unit_economics_1c_wallet_sync_interval_seconds),
            startup_delay_seconds=(settings.unit_economics_1c_price_sync_startup_delay_seconds + 30),
            is_enabled=lambda: _job_enabled("unit_economics_1c_wallet_sync"),
            run_callback=_sync_wallet_prices_configured,
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
            is_enabled=lambda: _job_enabled("marketplace_stock_sync_and_history_23_msk"),
            run_callback=_sync_stock_history_configured,
        ),
        BackgroundJob(
            "fulfillment_stock_history_00_msk",
            stock_history.save_previous_day_fulfillment_history,
            _moscow_daily_delay(0),
            startup_delay_seconds=_seconds_until_next_moscow_run(0),
            ready_event=catalog_ready,
            is_enabled=lambda: _job_enabled("fulfillment_stock_history_00_msk"),
        ),
    )


def _ftp_export_jobs() -> tuple[BackgroundJob, ...]:
    jobs = []
    for job_name, platform in ftp_export.JOB_PLATFORMS.items():
        jobs.append(
            BackgroundJob(
                job_name,
                lambda current_platform=platform: ftp_export.run_platform(current_platform),
                lambda current_job=job_name: ftp_export_schedule.next_delay_seconds(current_job),
                startup_delay_seconds=ftp_export_schedule.startup_delay_seconds(),
                is_enabled=lambda current_job=job_name: (
                    _job_enabled(current_job)
                    and ftp_export_schedule.should_attempt(current_job)
                ),
            )
        )
    return tuple(jobs)


def _jobs(catalog_ready: asyncio.Event) -> tuple[BackgroundJob, ...]:
    return (
        BackgroundJob(
            "catalog_sync",
            _sync_catalogs,
            _daily_delay(settings.catalog_sync_hour),
            on_attempt=catalog_ready.set,
            is_enabled=lambda: _job_enabled("catalog_sync"),
            run_callback=_sync_catalogs_configured,
        ),
        BackgroundJob(
            "stock_sync",
            _sync_stocks,
            _fixed_delay(settings.auto_sync_interval_seconds),
            ready_event=catalog_ready,
            is_enabled=lambda: _job_enabled("stock_sync"),
            run_callback=_sync_stocks_configured,
        ),
        *_wb_stock_history_jobs(catalog_ready),
        BackgroundJob(
            "wb_token_check",
            _refresh_token_info,
            _fixed_delay(settings.token_check_interval_seconds),
            is_enabled=lambda: _job_enabled("wb_token_check"),
            run_callback=_refresh_token_info_configured,
        ),
        BackgroundJob(
            "wb_advertising_sync",
            _sync_wb_advertising,
            _fixed_delay(settings.wb_advertising_sync_interval_seconds),
            startup_delay_seconds=settings.wb_advertising_sync_startup_delay_seconds,
            is_enabled=lambda: _job_enabled("wb_advertising_sync"),
            run_callback=_sync_wb_advertising_configured,
        ),
        *_funnel_jobs(),
        BackgroundJob(
            "unit_economics_1c_daily_margin_snapshot_00_msk",
            unit_margin_history.save_daily_margin_snapshots,
            _moscow_daily_delay(0),
            startup_delay_seconds=_seconds_until_next_moscow_run(0),
            ready_event=catalog_ready,
            is_enabled=lambda: _job_enabled("unit_economics_1c_daily_margin_snapshot_00_msk"),
            run_callback=_save_daily_margin_configured,
        ),
        BackgroundJob(
            "stock_sheet_export",
            stock_sheet_export.run_due,
            _fixed_delay(60),
            ready_event=catalog_ready,
            is_enabled=lambda: _job_enabled("stock_sheet_export"),
            run_callback=_run_stock_sheet_export_configured,
        ),
        BackgroundJob(
            "unit_economics_1c_source_sync",
            unit_source_sync.sync_all,
            _moscow_daily_delay(settings.unit_economics_1c_source_sync_hour),
            ready_event=catalog_ready,
            is_enabled=lambda: _job_enabled("unit_economics_1c_source_sync"),
        ),
        BackgroundJob(
            "unit_economics_1c_reference_sync",
            unit_reference_sync.sync_due,
            _fixed_delay(24 * 60 * 60),
            startup_delay_seconds=30,
            ready_event=catalog_ready,
            is_enabled=lambda: _job_enabled("unit_economics_1c_reference_sync"),
        ),
        *_ftp_export_jobs(),
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
    application.state.background_jobs = jobs
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
