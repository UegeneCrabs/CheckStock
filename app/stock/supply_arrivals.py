import json
import logging
from datetime import UTC, datetime, timedelta

from app.access.access_control import accessible_stores
from app.config import settings
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.identity import Role, coerce_user
from app.integrations.supply_arrivals_sheet import SupplySheetError, fetch_arrivals
from app.jobs import locks
from app.repositories import supply_arrivals as repository

logger = logging.getLogger(__name__)
JOB_NAME = "supply_arrivals_sync"


def source_key() -> str:
    return f"{settings.supply_arrivals_spreadsheet_id}:{settings.supply_arrivals_sheet_gid}"


def source_url() -> str:
    return f"https://docs.google.com/spreadsheets/d/{settings.supply_arrivals_spreadsheet_id}/edit#gid={settings.supply_arrivals_sheet_gid}"


def sync() -> dict:
    """Called under the shared lock by the scheduler, manual action or CLI."""
    key = source_key()
    now = datetime.now(UTC)
    previous = repository.read(key)
    if previous["last_attempt"] and datetime.fromisoformat(previous["last_attempt"]) > now - timedelta(
        seconds=60
    ):
        return {"status": "skipped", "reason": "Обновление уже выполнялось в последнюю минуту"}
    repository.save(key, last_attempt=now.isoformat())
    try:
        title, rows = fetch_arrivals()
    except Exception as error:
        message = str(error) if isinstance(error, SupplySheetError) else "Ошибка чтения реестра поставок"
        repository.save(key, error=message)
        logger.exception("supply_arrivals_sync_failed")
        return {"status": "error", "error": message}
    repository.save(
        key,
        sheet_title=title,
        payload_json=json.dumps([row.model_dump(mode="json") for row in rows], ensure_ascii=False),
        last_success=datetime.now(UTC).isoformat(),
        error="",
    )
    return {"status": "ok", "count": len(rows), "warnings": sum(bool(row.warnings) for row in rows)}


def report(user) -> dict:
    snapshot = repository.read(source_key())
    normalized = coerce_user(user)
    stores = set(accessible_stores(normalized))
    if normalized is None or normalized.role is not Role.SUPERADMIN:
        snapshot["rows"] = [row for row in snapshot["rows"] if row["store_slug"] in stores]
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.supply_arrivals_sync_interval_seconds * 2)
    return {
        **snapshot,
        "running": locks.is_running(JOB_NAME),
        "stale": not snapshot["last_success"] or datetime.fromisoformat(snapshot["last_success"]) < cutoff,
        "today": datetime.now(MOSCOW_TIMEZONE).date().isoformat(),
        "source_url": source_url(),
    }
