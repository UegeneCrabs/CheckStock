"""Load a rolling completed week through WB API; publish only a complete snapshot."""

import logging
from datetime import date, datetime, timedelta

from app.container import ApplicationContainer
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.wb_standard_costs import PublishSnapshot, StoreSyncResult, SyncResult
from app.economics.wb.standard_costs import build
from app.jobs import settings
from app.wb import tokens
from app.wb.standard_costs import fetch_sources as fetch_sources

JOB = "wb_standard_costs_sync"
WEEK_DAYS = 7
logger = logging.getLogger(__name__)


def completed_week(now: datetime | None = None) -> tuple[date, date]:
    now = now or datetime.now(MOSCOW_TIMEZONE)
    end = now.astimezone(MOSCOW_TIMEZONE).date() - timedelta(days=1)
    return end - timedelta(days=WEEK_DAYS - 1), end


def sync_store(store: str, *, now: datetime | None = None) -> SyncResult:
    """Publish sources and calculated report together after successful validation."""
    start, end = completed_week(now)
    raw = fetch_sources(tokens.get_token(store), start, end)
    report = build(raw)
    # No DB writes until all API pages and calculation validation have succeeded.
    ApplicationContainer().wb_standard_costs.publish(
        PublishSnapshot(store_slug=store, snapshot=report, raw=raw)
    )
    return SyncResult(ok=True, date_from=raw.date_from, date_to=raw.date_to, rows=len(report.rows))


def sync_all() -> list[StoreSyncResult]:
    result = []
    for store in settings.enabled_stores(JOB, "WB"):
        try:
            if not tokens.has_token(store):
                result.append(
                    StoreSyncResult(store, SyncResult(ok=True, skipped=True, reason="Нет токена WB"))
                )
                continue
            result.append(StoreSyncResult(store, sync_store(store)))
        except Exception as error:
            logger.exception("wb_standard_costs_sync_failed store=%s", store)
            result.append(StoreSyncResult(store, SyncResult(ok=False, error=str(error))))
    return result


def sync_job_payload() -> dict[str, object]:
    """Serialize at the existing job runner's JSON result boundary."""
    return {entry.store: entry.result.model_dump(exclude_unset=True) for entry in sync_all()}
