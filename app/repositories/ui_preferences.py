import json
from datetime import UTC, datetime

from app.repositories.core import WRITE_LOCK, get_connection


def get_ui_preference(user_id: int, scope: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT preference_json FROM user_ui_preferences WHERE user_id=? AND scope=?",
            (int(user_id), str(scope)),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {}
    try:
        value = json.loads(str(row["preference_json"] or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_ui_preference(user_id: int, scope: str, preference: dict) -> dict:
    payload = json.dumps(preference, ensure_ascii=False, separators=(",", ":"))
    updated_at = datetime.now(UTC).isoformat()
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO user_ui_preferences (user_id, scope, preference_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, scope) DO UPDATE SET
                    preference_json=excluded.preference_json,
                    updated_at=excluded.updated_at
                """,
                (int(user_id), str(scope), payload, updated_at),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return preference
