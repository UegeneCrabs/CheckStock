"""Saved WB funnel details and dated work notes for the analyzer."""

import uuid
from datetime import UTC, datetime

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
        result = [
            dict(row)
            for row in conn.execute(
                "SELECT id,store_slug,article,action_date,note,user_name,created_at FROM rnp_action_log "
                f"WHERE marketplace='WB' AND store_slug IN ({marks}) AND action_date>=? AND action_date<=? "
                "ORDER BY action_date,created_at,id",
                (*stores, start, end),
            )
        ]
        by_id = {row["id"]: row for row in result}
        for row in result:
            row.update(revisions=[], updated_at=None, updated_by=None)
        revisions = conn.execute(
            "SELECT r.* FROM rnp_action_revisions r JOIN rnp_action_log a ON a.id=r.action_id "
            f"WHERE a.marketplace='WB' AND a.store_slug IN ({marks}) "
            "AND a.action_date>=? AND a.action_date<=? ORDER BY r.edited_at,r.id",
            (*stores, start, end),
        )
        for revision in revisions:
            row = by_id[revision["action_id"]]
            row["revisions"].append(dict(revision))
            row["updated_at"], row["updated_by"] = revision["edited_at"], revision["user_name"]
    return result


def get_note(note_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id,store_slug,article,action_date,note,user_name,created_at FROM rnp_action_log "
            "WHERE id=? AND marketplace='WB'",
            (note_id,),
        ).fetchone()
    return dict(row) if row is not None else None


def add_note(store: str, article: str, day: str, note: str, user) -> dict:
    created = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        result = conn.execute(
            "INSERT INTO rnp_action_log "
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
        "revisions": [],
    }


def update_note(original: dict, note: str, user) -> dict:
    edited = datetime.now(UTC).isoformat()
    with WRITE_LOCK, get_connection() as conn:
        result = conn.execute(
            "UPDATE rnp_action_log SET note=? WHERE id=? AND marketplace='WB' AND note=? AND action_date=?",
            (note, original["id"], original["note"], original["action_date"]),
        )
        if result.rowcount != 1:
            raise ValueError("Запись уже изменена. Обновите карточку и повторите правку.")
        conn.execute(
            "INSERT INTO rnp_action_revisions (id,action_id,previous_note,note,user_id,user_name,edited_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, original["id"], original["note"], note, user.id, user.full_name, edited),
        )
        conn.commit()
    return next(
        row
        for row in notes((original["store_slug"],), original["action_date"], original["action_date"])
        if row["id"] == original["id"]
    )
