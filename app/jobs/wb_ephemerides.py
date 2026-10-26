import logging
from datetime import datetime, timedelta

from app import db
from app.core.constants import EPHEMERIDES_STOCK_SCHEDULE, JOBS
from app.core.domain import MOSCOW_TIMEZONE
from app.jobs import settings
from app.repositories import ephemerides

JOB = JOBS.wb_ephemerides_stock.name
logger = logging.getLogger(__name__)


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


def snapshot() -> dict:
    now = datetime.now(MOSCOW_TIMEZONE)
    if now.weekday() != EPHEMERIDES_STOCK_SCHEDULE.weekday:
        return {"status": "ok", "saved": 0}
    stores = settings.enabled_stores(JOB, "WB")
    transit = ephemerides.transit(stores)
    result = {}
    for store in stores:
        try:
            products = [
                {
                    "article": str(product["article"]),
                    "fbs": product.get("fbs_stock"),
                    "fbo": product.get("fbo_stock"),
                    "ff": product.get("ff_available") or 0,
                    "fromCustomer": transit.get(
                        (store, str(product["article"]).partition(" / ")[0].strip()), {}
                    ).get("from_customer"),
                }
                for product in db.get_stock_items(store, "WB")
            ]
            if products:
                ephemerides.freeze_stock(store, now.date().isoformat(), products, now.isoformat())
            result[store] = {"status": "ok", "products": len(products)}
        except Exception:
            logger.exception("WB weekly stock snapshot failed for %s", store)
            result[store] = {"status": "error", "error": "Не удалось зафиксировать недельный сток"}
    return result
