"""Saved WB funnel details and dated work notes for the analyzer."""

from datetime import UTC, datetime

from app.core.domain import MOSCOW_TIMEZONE
from app.repositories.core import WRITE_LOCK, get_connection


def daily_metrics(stores: tuple[str, ...], start: str, end: str) -> list[dict]:
    if not stores:
        return []
    marks = ",".join("?" for _ in stores)
    # Older RNP imports used DD-MM-YYYY. Normalize without altering saved data.
    day = "CASE WHEN SUBSTR(day, 3, 1)='-' THEN SUBSTR(day,7,4)||'-'||SUBSTR(day,4,2)||'-'||SUBSTR(day,1,2) ELSE day END"
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT *, {day} AS normalized_day FROM rnp_daily_metrics "
            f"WHERE marketplace='WB' AND store_slug IN ({marks}) AND {day}>=? AND {day}<=? "
            "ORDER BY snapshot_synced_at, funnel_synced_at, advertising_synced_at",
            (*stores, start, end),
        ).fetchall()
    return [{**dict(row), "day": row["normalized_day"]} for row in rows]


def notes(stores: tuple[str, ...], start: str, end: str) -> list[dict]:
    if not stores:
        return []
    marks = ",".join("?" for _ in stores)
    with get_connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,store_slug,article,action_date,note,user_name,created_at,updated_at,updated_by "
                "FROM analyzer_daily_notes "
                f"WHERE marketplace='WB' AND store_slug IN ({marks}) AND action_date>=? AND action_date<=? "
                "ORDER BY action_date,created_at,id",
                (*stores, start, end),
            )
        ]


def get_note(note_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id,store_slug,article,action_date,note,user_name,created_at FROM analyzer_daily_notes "
            "WHERE id=? AND marketplace='WB'",
            (note_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def add_note(store: str, article: str, day: str, note: str, user) -> dict:
    created = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        result = conn.execute(
            "INSERT INTO analyzer_daily_notes "
            "(store_slug,marketplace,article,action_date,note,user_id,user_name,created_at) "
            "VALUES (?,'WB',?,?,?,?,?,?) RETURNING id",
            (store, article, day, note, user.id, user.full_name, created),
        )
        note_id = result.fetchone()["id"]
        conn.commit()
    return {
        "id": note_id,
        "store_slug": store,
        "article": article,
        "action_date": day,
        "note": note,
        "user_name": user.full_name,
        "created_at": created,
        "updated_at": None,
        "updated_by": None,
    }


def update_note(original: dict, note: str, previous_note: str, user) -> dict:
    with WRITE_LOCK, get_connection() as conn:
        edited = datetime.now(UTC)
        result = conn.execute(
            "UPDATE analyzer_daily_notes SET note=?,updated_at=?,updated_by=? "
            "WHERE id=? AND marketplace='WB' AND note=? AND action_date=? "
            "RETURNING id,store_slug,article,action_date,note,user_name,created_at,updated_at,updated_by",
            (
                note,
                edited.isoformat(),
                user.full_name,
                original["id"],
                previous_note,
                edited.astimezone(MOSCOW_TIMEZONE).date().isoformat(),
            ),
        ).fetchone()
        if result is None:
            raise ValueError("Запись уже изменена. Обновите карточку и повторите правку.")
        conn.commit()
    return dict(result)
