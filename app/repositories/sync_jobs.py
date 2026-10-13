from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from app.infrastructure.database import DatabaseConnection, repository_connection
from app.repositories.core import WRITE_LOCK, get_connection

SYNC_RUN_RETENTION_DAYS = 30


def record_started(
    name: str, trigger: str, started_at: str, run_id: str, *, connection: DatabaseConnection | None = None
) -> None:
    with (
        WRITE_LOCK if connection is None else nullcontext(),
        repository_connection(connection) as connection,
    ):
        connection.execute(
            """
            INSERT INTO sync_job_states (
                name, last_trigger, status, last_started_at, last_finished_at,
                last_success_at, duration_ms, error, next_run_at, updated_at
            ) VALUES (?, ?, 'running', ?, NULL, NULL, NULL, NULL, NULL, ?)
            ON CONFLICT(name) DO UPDATE SET
                last_trigger = excluded.last_trigger,
                status = 'running',
                last_started_at = excluded.last_started_at,
                last_finished_at = NULL,
                duration_ms = NULL,
                error = NULL,
                updated_at = excluded.updated_at
            """,
            (name, trigger, started_at, started_at),
        )
        connection.execute(
            """
            INSERT INTO sync_job_runs (
                id, name, trigger, status, started_at, finished_at, duration_ms, error
            ) VALUES (?, ?, ?, 'running', ?, NULL, NULL, NULL)
            """,
            (run_id, name, trigger, started_at),
        )
        retention_cutoff = (
            datetime.fromisoformat(started_at).astimezone(UTC) - timedelta(days=SYNC_RUN_RETENTION_DAYS)
        ).isoformat()
        connection.execute(
            """
            DELETE FROM sync_job_runs WHERE started_at < ?
            AND id NOT IN (
                SELECT active_run_id FROM sync_job_schedules WHERE active_run_id IS NOT NULL
            )
            """,
            (retention_cutoff,),
        )


def record_finished(
    name: str,
    run_id: str,
    *,
    status: str,
    finished_at: str,
    duration_ms: int,
    error: str | None,
    connection: DatabaseConnection | None = None,
) -> None:
    with (
        WRITE_LOCK if connection is None else nullcontext(),
        repository_connection(connection) as connection,
    ):
        connection.execute(
            """
            UPDATE sync_job_states
               SET status = ?,
                   last_finished_at = ?,
                   last_success_at = CASE WHEN ? = 'success' THEN ? ELSE last_success_at END,
                   duration_ms = ?,
                   error = ?,
                   updated_at = ?
             WHERE name = ?
               AND last_started_at = (SELECT started_at FROM sync_job_runs WHERE id = ?)
            """,
            (status, finished_at, status, finished_at, duration_ms, error, finished_at, name, run_id),
        )
        connection.execute(
            """
            UPDATE sync_job_runs
               SET status = ?,
                   finished_at = ?,
                   duration_ms = ?,
                   error = ?
             WHERE id = ?
            """,
            (status, finished_at, duration_ms, error, run_id),
        )


def record_next_run(
    name: str,
    next_run_at: str,
    *,
    reschedule: bool = False,
    connection: DatabaseConnection | None = None,
) -> None:
    with (
        WRITE_LOCK if connection is None else nullcontext(),
        repository_connection(connection) as connection,
    ):
        connection.execute(
            """
            INSERT INTO sync_job_states (name, next_run_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                next_run_at = excluded.next_run_at,
                updated_at = excluded.updated_at
            """,
            (name, next_run_at, next_run_at),
        )
        if reschedule:
            connection.execute(
                """
                UPDATE sync_job_schedules SET next_run_at = ?, attempt_number = 0, updated_at = ?
                 WHERE name = ? AND active_run_id IS NULL
                """,
                (next_run_at, datetime.now(UTC).isoformat(), name),
            )


def list_states() -> list[dict]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT name, last_trigger, status, last_started_at, last_finished_at,
                   last_success_at, duration_ms, error, next_run_at, updated_at
              FROM sync_job_states
             ORDER BY name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_runs(name: str, limit: int = 50) -> list[dict]:
    safe_limit = min(max(int(limit), 1), 200)
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, trigger, status, started_at, finished_at, duration_ms, error
              FROM sync_job_runs
             WHERE name = ?
             ORDER BY started_at DESC
             LIMIT ?
            """,
            (name, safe_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def list_settings(name: str | None = None) -> list[dict]:
    query = """
        SELECT name, store_slug, marketplace, enabled, updated_at
          FROM sync_job_settings
    """
    parameters: tuple[str, ...] = ()
    if name is not None:
        query += " WHERE name = ?"
        parameters = (name,)
    query += " ORDER BY name, store_slug, marketplace"
    with get_connection() as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def set_setting(
    name: str,
    store_slug: str,
    marketplace: str,
    enabled: bool,
    updated_at: str,
) -> None:
    with WRITE_LOCK, get_connection() as connection:
        connection.execute(
            """
            INSERT INTO sync_job_settings (name, store_slug, marketplace, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name, store_slug, marketplace) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (name, store_slug, marketplace, 1 if enabled else 0, updated_at),
        )
        connection.commit()
