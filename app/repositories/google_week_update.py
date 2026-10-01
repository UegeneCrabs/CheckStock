from __future__ import annotations

import json
from dataclasses import dataclass

from app.repositories.core import WRITE_LOCK, get_connection


@dataclass(frozen=True, slots=True)
class WeekUpdateSettings:
    enabled: bool = False
    spreadsheet_url: str = (
        "https://docs.google.com/spreadsheets/d/1ifWg6lhhbLANLArnqSAI5QJDksgBdNVcnyoHmkY8CjE/edit"
    )
    sheet_name: str = "2. Факт продаж"
    cells: tuple[str, ...] = ("F4", "AI10")
    weekday: int = 0
    run_time: str = "00:05"
    updated_at: str = ""
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_success_slot: str | None = None
    last_value: str | None = None
    last_error: str | None = None


def get_settings() -> WeekUpdateSettings:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM google_week_update_settings WHERE id = 1").fetchone()
    if row is None:
        return WeekUpdateSettings()
    values = dict(row)
    values.pop("id")
    values["enabled"] = bool(values["enabled"])
    values["cells"] = tuple(json.loads(values["cells"]))
    return WeekUpdateSettings(**values)


def save_settings(settings: WeekUpdateSettings) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """
            INSERT INTO google_week_update_settings
                (id, enabled, spreadsheet_url, sheet_name, cells, weekday, run_time, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled, spreadsheet_url = excluded.spreadsheet_url,
                sheet_name = excluded.sheet_name, cells = excluded.cells,
                weekday = excluded.weekday, run_time = excluded.run_time,
                updated_at = excluded.updated_at
            """,
            (
                int(settings.enabled),
                settings.spreadsheet_url,
                settings.sheet_name,
                json.dumps(settings.cells),
                settings.weekday,
                settings.run_time,
                settings.updated_at,
            ),
        )
        conn.commit()


def record_attempt(attempted_at: str) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            "UPDATE google_week_update_settings SET last_attempt_at = ? WHERE id = 1", (attempted_at,)
        )
        conn.commit()


def record_result(attempted_at: str, *, slot: str, value: str, error: str | None = None) -> None:
    with WRITE_LOCK, get_connection() as conn:
        if error:
            conn.execute("UPDATE google_week_update_settings SET last_error = ? WHERE id = 1", (error,))
        else:
            conn.execute(
                """UPDATE google_week_update_settings
                   SET last_success_at = ?, last_success_slot = ?, last_value = ?, last_error = NULL
                   WHERE id = 1""",
                (attempted_at, slot, value),
            )
        conn.commit()
