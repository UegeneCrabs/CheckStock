from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.repositories.core import WRITE_LOCK, get_connection

SYNC_RUN_RETENTION_DAYS = 30


def record_started(name: str, trigger: str, started_at: str, run_id: str) -> None:
    with WRITE_LOCK, get_connection() as connection:
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
            datetime.fromisoformat(started_at).astimezone(UTC)
            - timedelta(days=SYNC_RUN_RETENTION_DAYS)
        ).isoformat()
        connection.execute(
            "DELETE FROM sync_job_runs WHERE started_at < ?",
            (retention_cutoff,),
        )
        connection.commit()


def record_finished(
    name: str,
    run_id: str,
    *,
    status: str,
    finished_at: str,
    duration_ms: int,
    error: str | None,
) -> None:
    with WRITE_LOCK, get_connection() as connection:
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
            """,
            (status, finished_at, status, finished_at, duration_ms, error, finished_at, name),
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
        connection.commit()


def record_next_run(name: str, next_run_at: str) -> None:
    with WRITE_LOCK, get_connection() as connection:
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
        connection.commit()


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
