import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from app.application.yandex_analytics import YandexAnalyticsService
from app.config import settings
from app.core.constants import EPHEMERIDES_STOCK_SCHEDULE, JOBS, YANDEX_ANALYTICS
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.system import SyncFailure, SyncGroupReport
from app.dto.yandex_analytics import AnalyticsSourceError, StockProduct, WeeklyStock
from app.infrastructure.yandex_analytics_repository import YandexAnalyticsRepository
from app.jobs import settings as job_settings
from app.yandex import tokens
from app.yandex.analytics_sync import load_funnel, load_transit
from app.yandex.api import YandexApiError

logger = logging.getLogger(__name__)


def build_service() -> YandexAnalyticsService:
    return YandexAnalyticsService(YandexAnalyticsRepository(), lambda: datetime.now(MOSCOW_TIMEZONE))


def sync(source: str) -> SyncGroupReport:
    job = JOBS.yandex_analytics_funnel if source == "funnel" else JOBS.yandex_customer_transit
    succeeded: list[str] = []
    failed: list[SyncFailure] = []
    service = build_service()
    stores = job_settings.enabled_stores(job.name, YANDEX_ANALYTICS.marketplace)
    if not stores:
        return SyncGroupReport(group=job.name, succeeded=(), failed=())
    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        futures = {executor.submit(sync_store, service, store, source): store for store in stores}
        for future in as_completed(futures):
            failure = future.result()
            if failure:
                failed.append(failure)
            else:
                succeeded.append(futures[future])
    return SyncGroupReport(group=job.name, succeeded=tuple(succeeded), failed=tuple(failed))


def sync_store(service: YandexAnalyticsService, store: str, source: str) -> SyncFailure | None:
    if not tokens.has_credentials(store):
        service.source_error(store, source, "credentials_missing")
        logger.warning("yandex_analytics_credentials_missing source=%s store=%s", source, store)
        return SyncFailure(target=store, error_type="CredentialsMissing", message="credentials_missing")
    try:
        if source == "funnel":
            for day in service.funnel_days(store, settings.yandex_analytics_funnel_history_refresh_seconds):
                service.save_funnel(load_funnel(store, day))
        else:
            service.save_transit(load_transit(store))
    except Exception as error:
        code = (
            error.reason
            if isinstance(error, AnalyticsSourceError)
            else f"{type(error).__name__}:{error.status}"
            if isinstance(error, YandexApiError)
            else type(error).__name__
        )
        service.source_error(store, source, code)
        logger.exception("yandex_analytics_sync_failed source=%s store=%s", source, store)
        return SyncFailure(target=store, error_type=type(error).__name__, message=str(error))
    return None


def delay(*, startup: bool = False) -> float:
    now = datetime.now(MOSCOW_TIMEZONE)
    rule = EPHEMERIDES_STOCK_SCHEDULE
    target = now.replace(hour=rule.hour, minute=rule.minute, second=0, microsecond=0)
    target += timedelta(days=(rule.weekday - now.weekday()) % rule.days)
    if startup and now.weekday() == rule.weekday and now >= target:
        return 0
    if target <= now:
        target += timedelta(days=rule.days)
    return (target - now).total_seconds()


def snapshot() -> SyncGroupReport:
    now = datetime.now(MOSCOW_TIMEZONE)
    rule = EPHEMERIDES_STOCK_SCHEDULE
    job = JOBS.yandex_ephemerides_stock
    if now.weekday() != rule.weekday or now < now.replace(
        hour=rule.hour, minute=rule.minute, second=0, microsecond=0
    ):
        return SyncGroupReport(group=job.name, succeeded=(), failed=())
    repository = YandexAnalyticsRepository()
    service = build_service()
    succeeded: list[str] = []
    failed: list[SyncFailure] = []
    for store in job_settings.enabled_stores(job.name, YANDEX_ANALYTICS.marketplace):
        try:
            sources = repository.sources((store,), now.date(), now.date(), current=False, history=False)
            transit = repository.transit(store)
            fresh = (
                transit is not None
                and not transit.error
                and (now - transit.received_at).total_seconds()
                <= settings.yandex_analytics_transit_interval_seconds
            )
            products = tuple(
                StockProduct(
                    article=item.product.article,
                    fbs=item.product.fbs_stock,
                    fbo=item.product.fbo_stock,
                    ff=item.product.ff_available,
                    fromCustomer=transit.quantities.get(item.product.article, 0)
                    if fresh and transit
                    else None,
                )
                for item in sources.stores[0].products.values()
            )
            service.freeze_stock(
                WeeklyStock(store=store, week=now.date(), captured_at=now, products=products)
            )
            succeeded.append(store)
        except Exception as error:
            logger.exception("yandex_weekly_stock_failed store=%s", store)
            failed.append(SyncFailure(target=store, error_type=type(error).__name__))
    return SyncGroupReport(group=job.name, succeeded=tuple(succeeded), failed=tuple(failed))
