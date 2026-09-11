from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Thread
from time import monotonic

from app import db, sync_locks

logger = logging.getLogger(__name__)
MAX_SYNC_ERROR_LENGTH = 12_000


def _now() -> datetime:
    return datetime.now(UTC)


def _trim_error(value: object) -> str:
    return str(value or "").strip()[:MAX_SYNC_ERROR_LENGTH]


def _result_failure_details(result: object, prefix: str = "") -> list[str]:
    failed = tuple(getattr(result, "failed", ()) or ())
    if failed:
        details: list[str] = []
        for item in failed:
            target = str(getattr(item, "target", "") or "").strip()
            error_type = str(getattr(item, "error_type", "") or "").strip()
            message = str(getattr(item, "message", "") or "").strip()
            error = ": ".join(value for value in (error_type, message) if value)
            label = " / ".join(value for value in (prefix, target) if value)
            details.append(f"{label}: {error}" if label and error else label or error)
        return [detail for detail in details if detail]
    if isinstance(result, dict):
        details = []
        if result.get("ok") is False or result.get("status") == "error":
            error = next(
                (
                    str(result[key]).strip()
                    for key in ("error", "detail", "message", "error_type")
                    if result.get(key)
                ),
                "Операция вернула ok=false",
            )
            details.append(f"{prefix}: {error}" if prefix else error)
        for key, value in result.items():
            if isinstance(value, (dict, list, tuple)):
                child_prefix = " / ".join(part for part in (prefix, str(key)) if part)
                details.extend(_result_failure_details(value, child_prefix))
        return details
    if isinstance(result, (list, tuple)):
        details = []
        for index, value in enumerate(result, start=1):
            child_prefix = prefix or f"Элемент {index}"
            if isinstance(value, (dict, list, tuple)) or getattr(value, "failed", None):
                details.extend(_result_failure_details(value, child_prefix))
            elif prefix.rsplit(" / ", 1)[-1] in {"errors", "failed"} and value:
                details.append(f"{prefix}: {value}")
        return details
    return []


def _result_error(result: object) -> str | None:
    unique_details = list(dict.fromkeys(_result_failure_details(result)))
    return _trim_error("; ".join(unique_details)) or None


def _exception_error(error: Exception) -> str:
    message = str(error).strip()
    return _trim_error(
        f"{type(error).__name__}: {message}" if message else type(error).__name__
    )


def run_tracked(name: str, trigger: str, callback: Callable[[], object]) -> object:
    """Execute one synchronization and retain its status and operational error details."""
    with sync_locks.hold(name):
        run_id, started_clock = _start_run(name, trigger)
        return _finish_run(name, run_id, started_clock, callback)


def _start_run(name: str, trigger: str, *, required: bool = False) -> tuple[str, float]:
    run_id, started_clock = uuid.uuid4().hex, monotonic()
    try:
        db.record_sync_job_started(name, trigger, _now().isoformat(), run_id)
    except Exception:
        if required:
            raise
        logger.exception("sync_job_start_tracking_failed job=%s", name)
    return run_id, started_clock


def queue_tracked(name: str, callback: Callable[[], object]) -> str:
    """Reserve and record the run before returning; HTTP need not wait for an API report."""
    handle = sync_locks.acquire(name)
    try:
        run_id, started_clock = _start_run(name, "manual", required=True)
    except Exception:
        handle.close()
        raise

    def work():
        try:
            _finish_run(name, run_id, started_clock, callback)
        except Exception:
            logger.exception("manual_sync_failed job=%s", name)
        finally:
            handle.close()

    try:
        Thread(target=work, name=f"manual-sync:{name}", daemon=True).start()
    except Exception as error:
        try:
            db.record_sync_job_finished(name, run_id, status="error", finished_at=_now().isoformat(),
                duration_ms=0, error=_exception_error(error))
        finally:
            handle.close()
        raise
    return run_id


def _finish_run(name: str, run_id: str, started_clock: float, callback: Callable[[], object]) -> object:
    try:
        result = callback()
    except Exception as error:
        finished_at = _now()
        try:
            db.record_sync_job_finished(
                name,
                run_id,
                status="error",
                finished_at=finished_at.isoformat(),
                duration_ms=max(0, round((monotonic() - started_clock) * 1000)),
                error=_exception_error(error),
            )
        except Exception:
            logger.exception("sync_job_failure_tracking_failed job=%s", name)
        raise

    result_error = _result_error(result)
    finished_at = _now()
    try:
        db.record_sync_job_finished(
            name,
            run_id,
            status="error" if result_error else "success",
            finished_at=finished_at.isoformat(),
            duration_ms=max(0, round((monotonic() - started_clock) * 1000)),
            error=result_error,
        )
    except Exception:
        logger.exception("sync_job_success_tracking_failed job=%s", name)
    return result


def set_next_run(name: str, delay_seconds: float) -> None:
    next_run = _now() + timedelta(seconds=max(0, delay_seconds))
    try:
        db.record_sync_job_next_run(name, next_run.isoformat())
    except Exception:
        logger.exception("sync_job_next_run_tracking_failed job=%s", name)
