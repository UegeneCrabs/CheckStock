from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.repositories.core import WRITE_LOCK, get_connection


def _now() -> datetime:
    return datetime.now(UTC)


def _row_dict(row) -> dict:
    result = dict(row)
    try:
        result["context"] = json.loads(str(result.pop("context_json") or "{}"))
    except (TypeError, ValueError):
        result["context"] = {}
    return result


def create_access_request(
    *,
    user_id: int,
    permission: str,
    store_slug: str,
    source_marketplace: str,
    target_marketplace: str | None,
    reason: str,
    context: dict | None = None,
    duration_days: int = 7,
) -> dict:
    created_at = _now().isoformat()
    target = str(target_marketplace or "").strip().upper() or None
    duration = min(max(int(duration_days or 7), 1), 7)
    with WRITE_LOCK:
        conn = get_connection()
        try:
            existing = conn.execute(
                """
                SELECT request.*, users.full_name AS user_name, users.google_email AS user_email
                  FROM access_requests request
                  JOIN users ON users.id=request.user_id
                 WHERE request.user_id=? AND request.permission=? AND request.store_slug=?
                   AND request.source_marketplace=?
                   AND COALESCE(request.target_marketplace, '')=COALESCE(?, '')
                   AND request.status='pending'
                 ORDER BY request.id DESC LIMIT 1
                """,
                (user_id, permission, store_slug, source_marketplace, target),
            ).fetchone()
            if existing is not None:
                result = _row_dict(existing)
                result["created_new"] = False
                return result
            cursor = conn.execute(
                """
                INSERT INTO access_requests (
                    user_id, permission, store_slug, source_marketplace,
                    target_marketplace, reason, context_json, duration_days,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                RETURNING id
                """,
                (
                    user_id,
                    permission,
                    store_slug,
                    source_marketplace,
                    target,
                    reason.strip(),
                    json.dumps(context or {}, ensure_ascii=False, separators=(",", ":")),
                    duration,
                    created_at,
                ),
            )
            request_id = int(cursor.lastrowid)
            conn.commit()
            row = conn.execute(
                """
                SELECT request.*, users.full_name AS user_name, users.google_email AS user_email
                  FROM access_requests request
                  JOIN users ON users.id=request.user_id
                 WHERE request.id=?
                """,
                (request_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Не удалось сохранить запрос доступа")
            result = _row_dict(row)
            result["created_new"] = True
            return result
        finally:
            conn.close()


def list_access_requests(status: str | None = None, limit: int = 200) -> list[dict]:
    conn = get_connection()
    try:
        where = "WHERE request.status=?" if status else ""
        params = (status, max(1, int(limit))) if status else (max(1, int(limit)),)
        rows = conn.execute(
            f"""
            SELECT request.*, users.full_name AS user_name, users.google_email AS user_email,
                   approver.full_name AS decided_by_name,
                   access_grant.id AS grant_id, access_grant.valid_until, access_grant.revoked_at
              FROM access_requests request
              JOIN users ON users.id=request.user_id
              LEFT JOIN users approver ON approver.id=request.decided_by_user_id
              LEFT JOIN temporary_access_grants access_grant ON access_grant.request_id=request.id
              {where}
             ORDER BY CASE request.status WHEN 'pending' THEN 0 ELSE 1 END,
                      request.created_at DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
        return [_row_dict(row) for row in rows]
    finally:
        conn.close()


def decide_access_request(
    request_id: int,
    *,
    approved: bool,
    decided_by_user_id: int,
    decision_note: str = "",
) -> dict | None:
    decided_at = _now()
    with WRITE_LOCK:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM access_requests WHERE id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                return None
            request = dict(row)
            if request["status"] != "pending":
                return _row_dict(row)
            status = "approved" if approved else "rejected"
            conn.execute(
                """
                UPDATE access_requests
                   SET status=?, decided_at=?, decided_by_user_id=?, decision_note=?
                 WHERE id=? AND status='pending'
                """,
                (
                    status,
                    decided_at.isoformat(),
                    decided_by_user_id,
                    decision_note.strip() or None,
                    request_id,
                ),
            )
            if approved:
                valid_until = decided_at + timedelta(days=min(max(int(request["duration_days"]), 1), 7))
                conn.execute(
                    """
                    INSERT INTO temporary_access_grants (
                        request_id, user_id, permission, store_slug,
                        source_marketplace, target_marketplace, valid_from,
                        valid_until, granted_by_user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(request_id) DO NOTHING
                    """,
                    (
                        request_id,
                        request["user_id"],
                        request["permission"],
                        request["store_slug"],
                        request["source_marketplace"],
                        request["target_marketplace"],
                        decided_at.isoformat(),
                        valid_until.isoformat(),
                        decided_by_user_id,
                    ),
                )
            conn.commit()
            updated = conn.execute(
                """
                SELECT request.*, users.full_name AS user_name, users.google_email AS user_email,
                       access_grant.id AS grant_id, access_grant.valid_until, access_grant.revoked_at
                  FROM access_requests request
                  JOIN users ON users.id=request.user_id
                  LEFT JOIN temporary_access_grants access_grant ON access_grant.request_id=request.id
                 WHERE request.id=?
                """,
                (request_id,),
            ).fetchone()
            return _row_dict(updated) if updated is not None else None
        finally:
            conn.close()


def has_valid_access_grant(
    user_id: int,
    permission: str,
    store_slug: str,
    source_marketplace: str,
    target_marketplace: str | None,
) -> bool:
    now = _now().isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT 1 FROM temporary_access_grants
             WHERE user_id=? AND permission=? AND store_slug=?
               AND source_marketplace=?
               AND COALESCE(target_marketplace, '')=COALESCE(?, '')
               AND valid_from<=? AND valid_until>?
               AND revoked_at IS NULL
             LIMIT 1
            """,
            (
                user_id,
                permission,
                store_slug,
                source_marketplace,
                str(target_marketplace or "").strip().upper() or None,
                now,
                now,
            ),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def revoke_access_grant(grant_id: int, *, revoked_by_user_id: int) -> bool:
    with WRITE_LOCK:
        conn = get_connection()
        try:
            cursor = conn.execute(
                """
                UPDATE temporary_access_grants
                   SET revoked_at=?, revoked_by_user_id=?
                 WHERE id=? AND revoked_at IS NULL
                """,
                (_now().isoformat(), revoked_by_user_id, grant_id),
            )
            conn.commit()
            return bool(cursor.rowcount)
        finally:
            conn.close()


def list_superadmin_emails() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT google_email FROM users
             WHERE role='superadmin' AND is_active=1 AND TRIM(google_email)<>''
             ORDER BY id
            """
        ).fetchall()
        return [str(row["google_email"]).strip() for row in rows]
    finally:
        conn.close()
