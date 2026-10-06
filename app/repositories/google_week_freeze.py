"""Persist sheet selection, daily metadata and past-week freeze results."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.repositories.core import WRITE_LOCK, get_connection
from app.repositories.google_week_update import WeekSearchState, _record_export_result


@dataclass(frozen=True, slots=True)
class WeekFreezeState(WeekSearchState):
    spreadsheet_id: str = ""
    sheet_ids: tuple[int, ...] = ()
    selection_initialized: bool = False


def get_state() -> WeekFreezeState:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM google_week_freeze_state WHERE id=1").fetchone()
    if row is None:
        return WeekFreezeState()
    values = dict(row)
    values.pop("id")
    values["enabled"] = bool(values["enabled"])
    values["selection_initialized"] = bool(values["selection_initialized"])
    values["sheet_ids"] = tuple(json.loads(values["sheet_ids"]))
    result = values.pop("result_json")
    values["result"] = json.loads(result) if result else None
    return WeekFreezeState(**values)


def save_selection(
    conn, spreadsheet_id: str, sheet_ids: tuple[int, ...], *, enabled: bool, initialized: bool
) -> None:
    """Use the settings transaction; its caller already holds WRITE_LOCK and the job lock."""
    conn.execute(
        """INSERT INTO google_week_freeze_state
           (id, spreadsheet_id, sheet_ids, enabled, selection_initialized) VALUES (1, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET
             spreadsheet_id=excluded.spreadsheet_id, sheet_ids=excluded.sheet_ids,
             enabled=excluded.enabled, selection_initialized=excluded.selection_initialized,
             last_attempt_at=CASE WHEN google_week_freeze_state.spreadsheet_id != excluded.spreadsheet_id
                 OR google_week_freeze_state.sheet_ids != excluded.sheet_ids THEN NULL
                 ELSE google_week_freeze_state.last_attempt_at END,
             last_success_slot=CASE WHEN google_week_freeze_state.spreadsheet_id != excluded.spreadsheet_id
                 OR google_week_freeze_state.sheet_ids != excluded.sheet_ids THEN NULL
                 ELSE google_week_freeze_state.last_success_slot END""",
        (spreadsheet_id, json.dumps(sheet_ids), int(enabled), int(initialized)),
    )


def initialize_selection(spreadsheet_id: str, sheet_ids: tuple[int, ...]) -> None:
    """First successful catalog load picks defaults once; intentional empty choices stay empty."""
    with WRITE_LOCK, get_connection() as conn:
        row = conn.execute("SELECT * FROM google_week_freeze_state WHERE id=1").fetchone()
        if row is None or row["spreadsheet_id"] != spreadsheet_id or not row["selection_initialized"]:
            save_selection(conn, spreadsheet_id, sheet_ids, enabled=False, initialized=True)
        conn.commit()


def record_attempt(attempted_at: str) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """INSERT INTO google_week_freeze_state (id, last_attempt_at) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at""",
            (attempted_at,),
        )
        conn.commit()


def record_result(
    attempted_at: str, *, slot: str, result: dict | None = None, error: str | None = None
) -> None:
    _record_export_result("google_week_freeze_state", attempted_at, slot=slot, result=result, error=error)


def get_catalog(spreadsheet_id: str) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM google_sheet_catalog WHERE spreadsheet_id=?", (spreadsheet_id,)
        ).fetchone()
    if row is None:
        return {
            "spreadsheet_id": spreadsheet_id,
            "sheets": [],
            "updated_at": None,
            "last_attempt_at": None,
            "last_error": None,
        }
    result = dict(row)
    result["sheets"] = json.loads(result.pop("sheets_json"))
    return result


def record_catalog_attempt(spreadsheet_id: str, attempted_at: str) -> None:
    with WRITE_LOCK, get_connection() as conn:
        conn.execute(
            """INSERT INTO google_sheet_catalog (spreadsheet_id, last_attempt_at) VALUES (?, ?)
               ON CONFLICT(spreadsheet_id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at""",
            (spreadsheet_id, attempted_at),
        )
        conn.commit()


def record_catalog_result(
    spreadsheet_id: str, attempted_at: str, *, sheets: list[dict] | None = None, error: str | None = None
) -> None:
    with WRITE_LOCK, get_connection() as conn:
        if error:
            conn.execute(
                "UPDATE google_sheet_catalog SET last_error=? WHERE spreadsheet_id=?",
                (error, spreadsheet_id),
            )
        else:
            conn.execute(
                """UPDATE google_sheet_catalog SET sheets_json=?, updated_at=?, last_error=NULL
                   WHERE spreadsheet_id=?""",
                (json.dumps(sheets, ensure_ascii=False), attempted_at, spreadsheet_id),
            )
        conn.commit()
