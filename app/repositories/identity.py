from app.infrastructure.database import DatabaseConnection, DatabaseRow
from app.repositories.core import get_connection
from app.stores import STORES

ROLES = ["superadmin", "admin", "user"]


ROLE_LABELS = {
    "superadmin": "Суперадмин",
    "admin": "Админ",
    "user": "Пользователь",
}


def create_user(
    full_name: str,
    google_email: str,
    login: str,
    password_hash: str,
    role: str,
    created_at: str,
    store_slugs: list[str] | None = None,
) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO users (full_name, google_email, login, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (full_name, google_email, login, password_hash, role, created_at),
        )
        user_id = cur.lastrowid
        set_user_store_access(user_id, store_slugs, conn=conn)
        conn.commit()
        return user_id
    finally:
        conn.close()


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


def normalize_store_slugs(store_slugs: list[str] | tuple[str, ...] | None) -> list[str]:
    if not store_slugs:
        return list(STORES)
    seen = set()
    result = []
    for slug in store_slugs:
        key = str(slug or "").strip().lower()
        if key in STORES and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def set_user_store_access(
    user_id: int,
    store_slugs: list[str] | tuple[str, ...] | None,
    conn: DatabaseConnection | None = None,
) -> None:
    slugs = normalize_store_slugs(store_slugs)
    owns_conn = conn is None
    if conn is None:
        conn = get_connection()
    try:
        conn.execute("DELETE FROM user_store_access WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO user_store_access (user_id, store_slug) VALUES (?, ?)",
            [(user_id, slug) for slug in slugs],
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


def get_user_store_access(user_id: int, conn: DatabaseConnection | None = None) -> list[str]:
    owns_conn = conn is None
    if conn is None:
        conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT store_slug FROM user_store_access WHERE user_id = ? ORDER BY store_slug",
            (user_id,),
        ).fetchall()
        slugs = [row["store_slug"] for row in rows if row["store_slug"] in STORES]
        return slugs or list(STORES)
    finally:
        if owns_conn:
            conn.close()


def _user_from_row(conn: DatabaseConnection, row: DatabaseRow | None) -> dict | None:
    if row is None:
        return None
    user = dict(row)
    user["store_slugs"] = get_user_store_access(user["id"], conn=conn)
    return user


def set_user_permission(user_id: int, field: str, allowed: bool) -> None:

    if field not in ("can_edit_stock", "can_manage_users"):
        raise ValueError(f"неизвестное разрешение {field!r}")

    conn = get_connection()
    conn.execute(f"UPDATE users SET {field} = ? WHERE id = ?", (1 if allowed else 0, user_id))
    conn.commit()
    conn.close()


def list_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, full_name, google_email, login, role, is_active,"
        " can_edit_stock, can_manage_users, created_at FROM users ORDER BY id"
    ).fetchall()
    users = [_user_from_row(conn, row) for row in rows]
    conn.close()
    return [user for user in users if user is not None]


def set_user_active(user_id: int, is_active: bool) -> None:
    conn = get_connection()
    conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
    conn.commit()
    conn.close()


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

    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def update_user_password(user_id: int, password_hash: str) -> None:

    conn = get_connection()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def count_superadmins(exclude_user_id: int | None = None) -> int:

    conn = get_connection()
    if exclude_user_id is None:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'superadmin' AND is_active = 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'superadmin' AND is_active = 1 AND id != ?",
            (exclude_user_id,),
        ).fetchone()
    conn.close()
    return row["n"]


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
