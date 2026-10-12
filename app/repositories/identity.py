from app.infrastructure.database import DatabaseConnection, DatabaseRow
from app.repositories.core import get_connection


def create_user(full_name: str, google_email: str, login: str, password_hash: str, created_at: str) -> int:
    from datetime import datetime

    from app.access.auth import _service
    from app.dto.identity import CreateUserCommand

    return (
        _service()
        .create_user(
            CreateUserCommand(
                full_name=full_name,
                google_email=google_email,
                login=login,
                password_hash=password_hash,
                created_at=datetime.fromisoformat(created_at),
            )
        )
        .root
    )


def get_user_by_login(login: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
    user = _user_from_row(conn, row)
    conn.close()
    return user


def get_user(user_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    user = _user_from_row(conn, row)
    conn.close()
    return user


def _user_from_row(conn: DatabaseConnection, row: DatabaseRow | None) -> dict | None:
    return dict(row) if row is not None else None


def list_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, full_name, google_email, login, is_active, created_at FROM users ORDER BY id"
    ).fetchall()
    users = [_user_from_row(conn, row) for row in rows]
    conn.close()
    return [user for user in users if user is not None]


def set_user_active(user_id: int, is_active: bool) -> None:
    from app.access.auth import _service
    from app.dto.identity import UserActiveChange

    _service().set_active(UserActiveChange(user_id=user_id, is_active=is_active))


def count_users() -> int:
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    conn.close()
    return n


def create_session(token: str, user_id: int, created_at: str, expires_at: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, created_at, expires_at),
    )
    conn.commit()
    conn.close()


def get_session(token: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def delete_expired_sessions(now: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.commit()
    conn.close()


def log_action(user_id: int | None, user_name: str, action: str, details: str, created_at: str) -> None:

    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (user_id, user_name, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, user_name, action, details, created_at),
    )
    conn.commit()
    conn.close()


def get_activity_log(limit: int = 200) -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_user(user_id: int) -> None:
    from app.access.auth import _service
    from app.dto.identity import UserId

    _service().delete_user(UserId(user_id))


def update_user_password(user_id: int, password_hash: str) -> None:

    conn = get_connection()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def delete_sessions_for_user(user_id: int) -> None:

    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def upsert_wb_token_info(store_slug: str, expires_at: str | None, checked_at: str) -> None:

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO wb_token_info (store_slug, expires_at, checked_at)
        VALUES (?, ?, ?)
        ON CONFLICT(store_slug)
        DO UPDATE SET expires_at = excluded.expires_at, checked_at = excluded.checked_at
        """,
        (store_slug, expires_at, checked_at),
    )
    conn.commit()
    conn.close()


def delete_wb_token_info(store_slug: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM wb_token_info WHERE store_slug = ?", (store_slug,))
    conn.commit()
    conn.close()


def get_wb_token_infos() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT store_slug, expires_at, checked_at FROM wb_token_info ORDER BY store_slug"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_last_token_check() -> str | None:
    conn = get_connection()
    row = conn.execute("SELECT MAX(checked_at) AS last FROM wb_token_info").fetchone()
    conn.close()
    return row["last"] if row else None
