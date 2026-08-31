from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app import db
from app.config import settings
from app.domain import MOSCOW_TIMEZONE


def _now() -> datetime:
    return datetime.now(MOSCOW_TIMEZONE)


def _window(now: datetime) -> tuple[datetime, datetime]:
    localized = now.astimezone(MOSCOW_TIMEZONE)
    start = localized.replace(
        hour=settings.ftp_export_start_hour,
        minute=settings.ftp_export_start_minute,
        second=0,
        microsecond=0,
    )
    deadline = localized.replace(
        hour=settings.ftp_export_deadline_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    return start, deadline


def _last_success_at(job_name: str) -> datetime | None:
    state = next((item for item in db.list_sync_job_states() if item["name"] == job_name), None)
    value = state.get("last_success_at") if state else None
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(MOSCOW_TIMEZONE)


def succeeded_in_current_window(job_name: str, now: datetime | None = None) -> bool:
    current = (now or _now()).astimezone(MOSCOW_TIMEZONE)
    start, deadline = _window(current)
    last_success = _last_success_at(job_name)
    return bool(last_success and start <= last_success < deadline)


def should_attempt(job_name: str, now: datetime | None = None) -> bool:
    current = (now or _now()).astimezone(MOSCOW_TIMEZONE)
    start, deadline = _window(current)
    return start <= current < deadline and not succeeded_in_current_window(job_name, current)


def _next_start(current: datetime, start: datetime) -> datetime:
    return start if current < start else start + timedelta(days=1)


def startup_delay_seconds(now: datetime | None = None) -> float:
    current = (now or _now()).astimezone(MOSCOW_TIMEZONE)
    start, deadline = _window(current)
    if start <= current < deadline:
        return 0
    return max(0.0, (_next_start(current, start) - current).total_seconds())


def next_delay_seconds(job_name: str, now: datetime | None = None) -> float:
    current = (now or _now()).astimezone(MOSCOW_TIMEZONE)
    start, deadline = _window(current)
    if current < start:
        return max(0.0, (start - current).total_seconds())
    if current >= deadline or succeeded_in_current_window(job_name, current):
        return max(0.0, (start + timedelta(days=1) - current).total_seconds())

    retry_at = current + timedelta(seconds=settings.ftp_export_retry_interval_seconds)
    if retry_at >= deadline:
        return max(0.0, (start + timedelta(days=1) - current).total_seconds())
    return float(settings.ftp_export_retry_interval_seconds)
