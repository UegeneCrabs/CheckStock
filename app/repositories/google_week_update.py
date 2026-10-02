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


def save_settings(
    settings: WeekUpdateSettings,
    *,
    search_enabled: bool | None = None,
    sales_enabled: bool | None = None,
    stock_enabled: bool | None = None,
    stock_sheet_name: str | None = None,
) -> None:
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
        if search_enabled is not None:
            conn.execute(
                """INSERT INTO google_week_search_state (id, enabled) VALUES (1, ?)
                   ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled""",
                (int(search_enabled),),
            )
        if sales_enabled is not None:
            conn.execute(
                """INSERT INTO google_week_sales_state (id, enabled) VALUES (1, ?)
                   ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled""",
                (int(sales_enabled),),
            )
        if stock_enabled is not None or stock_sheet_name is not None:
            conn.execute("""INSERT INTO google_week_stock_state (id) VALUES (1) ON CONFLICT(id) DO NOTHING""")
            if stock_enabled is not None:
                conn.execute("UPDATE google_week_stock_state SET enabled=? WHERE id=1", (int(stock_enabled),))
            if stock_sheet_name is not None:
                conn.execute(
                    "UPDATE google_week_stock_state SET sheet_name=? WHERE id=1", (stock_sheet_name,)
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


@dataclass(frozen=True, slots=True)
class WeekSearchState:
    enabled: bool = False
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_success_slot: str | None = None
    last_error: str | None = None
    result: dict | None = None


@dataclass(frozen=True, slots=True)
class WeekStockState(WeekSearchState):
    sheet_name: str = "Сток на складах ВБ"


def get_search_state() -> WeekSearchState:
    return _get_state("google_week_search_state")


def get_sales_state() -> WeekSearchState:
    return _get_state("google_week_sales_state")


def get_stock_state() -> WeekStockState:
    return _get_state("google_week_stock_state", WeekStockState)


def _get_state(table: str, state_type=WeekSearchState) -> WeekSearchState:
    with get_connection() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = 1").fetchone()
    if row is None:
        return state_type()
    values = dict(row)
    values.pop("id")
    values["enabled"] = bool(values["enabled"])
    result = values.pop("result_json")
    values["result"] = json.loads(result) if result else None
    return state_type(**values)


def record_search_attempt(attempted_at: str) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """INSERT INTO google_week_search_state (id, enabled, last_attempt_at) VALUES (1, 0, ?)
               ON CONFLICT(id) DO UPDATE SET last_attempt_at = excluded.last_attempt_at""",
            (attempted_at,),
        )
        conn.commit()


def record_sales_attempt(attempted_at: str) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """INSERT INTO google_week_sales_state (id, enabled, last_attempt_at) VALUES (1, 0, ?)
               ON CONFLICT(id) DO UPDATE SET last_attempt_at = excluded.last_attempt_at""",
            (attempted_at,),
        )
        conn.commit()


def record_sales_result(
    attempted_at: str, *, slot: str, result: dict | None = None, error: str | None = None
) -> None:
    _record_export_result("google_week_sales_state", attempted_at, slot=slot, result=result, error=error)


def record_stock_attempt(attempted_at: str) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """INSERT INTO google_week_stock_state (id, last_attempt_at) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at""",
            (attempted_at,),
        )
        conn.commit()


def record_stock_result(
    attempted_at: str, *, slot: str, result: dict | None = None, error: str | None = None
) -> None:
    _record_export_result("google_week_stock_state", attempted_at, slot=slot, result=result, error=error)


def _record_export_result(
    table: str, attempted_at: str, *, slot: str, result: dict | None, error: str | None
) -> None:
    with WRITE_LOCK, get_connection() as conn:
        if error:
            conn.execute(f"UPDATE {table} SET last_error = ? WHERE id = 1", (error,))
        else:
            # Incomplete source data can become available later in the same scheduled week.
            complete = bool(result and result["complete"])
            conn.execute(
                f"""UPDATE {table} SET result_json = ?, last_error = NULL,
                   last_success_at = CASE WHEN ? = 1 THEN ? ELSE last_success_at END,
                   last_success_slot = CASE WHEN ? = 1 THEN ? ELSE last_success_slot END
                   WHERE id = 1""",
                (json.dumps(result, ensure_ascii=False), int(complete), attempted_at, int(complete), slot),
            )
        conn.commit()


def record_search_result(
    attempted_at: str, *, slot: str, result: dict | None = None, error: str | None = None
) -> None:
    with WRITE_LOCK, get_connection() as conn:
        if error:
            # Keep the last successful result, clearly timestamped, when the new read fails.
            conn.execute("UPDATE google_week_search_state SET last_error = ? WHERE id = 1", (error,))
        else:
            conn.execute(
                """UPDATE google_week_search_state
                   SET last_success_at = ?, last_success_slot = ?, result_json = ?, last_error = NULL
                   WHERE id = 1""",
                (attempted_at, slot, json.dumps(result, ensure_ascii=False)),
            )
        conn.commit()
