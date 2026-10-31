"""Persist complete customer-return snapshots independently of stock writers."""

from app.core.logging_config import tagged_logger
from app.exports.fbo_transit import _wb
from app.jobs import settings
from app.repositories.ephemerides import replace_transit

JOB = "wb_customer_transit_sync"
logger = tagged_logger(__name__, "jobs", "wb", "wb_transit")


def sync_all():
    result = {}
    for store in settings.enabled_stores(JOB, "WB"):
        try:
            snapshot = _wb(store)
            replace_transit(store, snapshot)
            result[store] = {"status": "ok", "products": len(snapshot.from_customer)}
        except Exception:
            logger.exception("WB customer transit failed for %s", store)
            result[store] = {
                "status": "error",
                "error": "Не удалось обновить товары в пути; предыдущий снимок сохранён",
            }
    return result
