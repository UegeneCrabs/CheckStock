from datetime import datetime, timedelta

from app import db
from app.core.constants import EPHEMERIDES_STOCK_SCHEDULE, JOBS
from app.core.domain import MOSCOW_TIMEZONE
from app.core.logging_config import tagged_logger
from app.dto.ephemerides_stock import EphemeridesStockProduct, EphemeridesStockSnapshot
from app.dto.system import SyncFailure, SyncGroupReport
from app.infrastructure.ephemerides_stock_repository import freeze_stock
from app.jobs import settings
from app.repositories import ephemerides

JOB = JOBS.wb_ephemerides_stock.name
logger = tagged_logger(__name__, "jobs", "wb", "wb_ephemerides")


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
    if now.weekday() != rule.weekday or now < now.replace(
        hour=rule.hour, minute=rule.minute, second=0, microsecond=0
    ):
        return SyncGroupReport(group=JOB, succeeded=(), failed=())
    stores = settings.enabled_stores(JOB, "WB")
    transit = ephemerides.transit(stores)
    succeeded: list[str] = []
    failed: list[SyncFailure] = []
    for store in stores:
        try:
            products = tuple(
                EphemeridesStockProduct(
                    article=str(product["article"]),
                    fbs=product.get("fbs_stock"),
                    fbo=product.get("fbo_stock"),
                    ff=product.get("ff_available") or 0,
                    fromCustomer=transit.get(
                        (store, str(product["article"]).partition(" / ")[0].strip()), {}
                    ).get("from_customer"),
                )
                for product in db.get_stock_items(store, "WB")
            )
            if products:
                freeze_stock(
                    EphemeridesStockSnapshot(store=store, week=now.date(), products=products, captured_at=now)
                )
            succeeded.append(store)
        except Exception as error:
            logger.exception("WB weekly stock snapshot failed for %s", store)
            failed.append(SyncFailure(target=store, error_type=type(error).__name__))
    return SyncGroupReport(group=JOB, succeeded=tuple(succeeded), failed=tuple(failed))
