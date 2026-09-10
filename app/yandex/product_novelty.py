"""Daily classification by orders in the seven days preceding the last 21 days."""

import logging
from datetime import UTC, date, datetime

from app.domain import MOSCOW_TIMEZONE
from app.repositories import unit_economics_yandex as snapshots
from app.repositories import yandex_product_statuses as repository
from app.stores import STORES
from app.yandex import tokens, unit_economics_sync

logger = logging.getLogger(__name__)
SOURCE = "novelty"
SYNC_INTERVAL_SECONDS = 24 * 60 * 60


def sync_store(store_slug: str, today: date | None = None) -> dict:
    if store_slug not in STORES:
        raise ValueError("Неизвестный кабинет")
    if not tokens.has_credentials(store_slug):
        return {"ok": False, "status": "not_configured"}
    today = today or datetime.now(MOSCOW_TIMEZONE).date()
    now = datetime.now(UTC).isoformat()
    try:
        pending = repository.pending_articles(store_slug, today)
        if not pending:
            return {"ok": True, "source": SOURCE, "rows": 0, "skipped": True}
        start, end = repository.check_period(today)
        api_key = tokens.get_api_key(store_slug)
        business_id = unit_economics_sync.resolve_business_id(store_slug, api_key)
        orders = unit_economics_sync.load_orders(store_slug, api_key, business_id, start, end)
        count = repository.save_check(store_slug, pending, orders, today, now)
        snapshots.save_snapshot(
            store_slug,
            SOURCE,
            [row for row in orders if row["article"] in pending],
            start.isoformat(),
            end.isoformat(),
            now,
        )
        return {"ok": True, "source": SOURCE, "rows": count}
    except Exception as error:
        message = str(error)[:700]
        snapshots.record_error(store_slug, SOURCE, message, now)
        logger.warning("Новизна товаров ЯМ %s: %s", store_slug, message)
        return {"ok": False, "source": SOURCE, "error": message}


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict:
    return {
        slug: sync_store(slug)
        for slug in (tuple(STORES) if store_slugs is None else store_slugs)
        if tokens.has_credentials(slug)
    }
