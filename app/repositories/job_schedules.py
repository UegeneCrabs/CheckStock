"""Schedule mutations must run while holding the cross-process lock for the job."""

from datetime import UTC, datetime
from uuid import uuid4

from app.repositories import sync_jobs
from app.repositories.core import WRITE_LOCK, get_connection


def ensure(name: str, initial_run_at: datetime, now: datetime) -> dict:
    with WRITE_LOCK, get_connection() as connection:
        row = connection.execute("SELECT * FROM sync_job_schedules WHERE name = ?", (name,)).fetchone()
        if row is not None:
            return dict(row)
        previous = connection.execute(
            "SELECT next_run_at FROM sync_job_states WHERE name = ?", (name,)
        ).fetchone()
        try:
            planned = datetime.fromisoformat(str(previous["next_run_at"])) if previous else initial_run_at
            if planned.tzinfo is None:
                planned = planned.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            planned = initial_run_at
        connection.execute(
            """
            INSERT INTO sync_job_schedules (name, next_run_at, attempt_number, active_run_id, updated_at)
            VALUES (?, ?, 0, NULL, ?) ON CONFLICT(name) DO NOTHING
            """,
            (name, planned.astimezone(UTC).isoformat(), now.isoformat()),
        )
        row = dict(connection.execute("SELECT * FROM sync_job_schedules WHERE name = ?", (name,)).fetchone())
        sync_jobs.record_next_run(name, row["next_run_at"], connection=connection)
        connection.commit()
        return row


def begin(schedule: dict, now: datetime) -> str | None:
    name, run_id = schedule["name"], uuid4().hex
    with WRITE_LOCK, get_connection() as connection:
        claimed = connection.execute(
            """
            UPDATE sync_job_schedules
               SET active_run_id = ?, attempt_number = attempt_number + 1, updated_at = ?
             WHERE name = ? AND active_run_id IS NULL AND next_run_at = ? AND attempt_number = ?
            """,
            (run_id, now.isoformat(), name, schedule["next_run_at"], schedule["attempt_number"]),
        )
        if claimed.rowcount != 1:
            return None
        sync_jobs.record_started(name, "scheduled", now.isoformat(), run_id, connection=connection)
        connection.commit()
        return run_id


def finish(
    name: str,
    run_id: str,
    *,
    finished_at: datetime,
    duration_ms: int,
    error: str | None,
    next_run_at: datetime,
    attempt_number: int,
) -> None:
    with WRITE_LOCK, get_connection() as connection:
        updated = connection.execute(
            """
            UPDATE sync_job_schedules
               SET active_run_id = NULL, next_run_at = ?, attempt_number = ?, updated_at = ?
             WHERE name = ? AND active_run_id = ?
            """,
            (next_run_at.isoformat(), attempt_number, finished_at.isoformat(), name, run_id),
        )
        if updated.rowcount != 1:
            raise RuntimeError(f"Scheduled run ownership changed: {name}/{run_id}")
        sync_jobs.record_finished(
            name,
            run_id,
            status="error" if error else "success",
            finished_at=finished_at.isoformat(),
            duration_ms=duration_ms,
            error=error,
            connection=connection,
        )
        sync_jobs.record_next_run(name, next_run_at.isoformat(), connection=connection)
        connection.commit()


def defer(name: str, next_run_at: datetime, now: datetime) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            """
            UPDATE sync_job_schedules SET next_run_at = ?, attempt_number = 0, updated_at = ?
             WHERE name = ? AND active_run_id IS NULL
            """,
            (next_run_at.isoformat(), now.isoformat(), name),
        )
        sync_jobs.record_next_run(name, next_run_at.isoformat(), connection=connection)
        connection.commit()
