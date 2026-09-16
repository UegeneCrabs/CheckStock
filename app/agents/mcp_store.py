"""Persistent OAuth state. Only hashes of codes, cookies and tokens are stored.

SQLite transactions serialize code redemption and refresh rotation across workers.
Use a persistent local volume; this file is not intended for network filesystems.
"""

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ACCESS_TTL = 3600
GRANT_TTL = 30 * 86400


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class OAuthError(Exception):
    pass


class OAuthStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS temporary (
                    hash TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS grants (
                    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, client_id TEXT NOT NULL,
                    resource TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL,
                    created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tokens (
                    hash TEXT PRIMARY KEY, kind TEXT NOT NULL, grant_id TEXT NOT NULL,
                    expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS tokens_grant ON tokens(grant_id);
            """)
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            db.execute("DELETE FROM temporary WHERE expires < ?", (now,))
            db.execute("DELETE FROM clients WHERE expires < ?", (now,))
            db.execute("DELETE FROM tokens WHERE expires < ?", (now,))
            db.execute("DELETE FROM grants WHERE expires < ?", (now,))
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def register(self, metadata):
        with self.transaction() as db:
            # Bound unauthenticated registration storage; also rate-limit at the proxy.
            if db.execute("SELECT COUNT(*) FROM clients").fetchone()[0] >= 10000:
                raise OAuthError("temporarily_unavailable")
            client_id = secrets.token_urlsafe(24)
            db.execute(
                "INSERT INTO clients VALUES (?, ?, ?)",
                (client_id, json.dumps(metadata), time.time() + 90 * 86400),
            )
            return client_id

    def client(self, client_id):
        with self.transaction() as db:
            row = db.execute("SELECT payload FROM clients WHERE id = ?", (client_id,)).fetchone()
            if not row:
                raise OAuthError("invalid_client")
            return json.loads(row[0])

    def pending(self, payload):
        value = secrets.token_urlsafe(32)
        with self.transaction() as db:
            db.execute(
                "INSERT INTO temporary VALUES (?, 'consent', ?, ?)",
                (digest(value), json.dumps(payload), time.time() + 600),
            )
        return value

    def consent(self, value, user_id, cookie, approve):
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM temporary WHERE hash = ? AND kind = 'consent'", (digest(value),)
            ).fetchone()
            if not row:
                raise OAuthError("invalid_request")
            payload = json.loads(row[0])
            if payload["user_id"] != user_id or payload["cookie_hash"] != digest(cookie):
                raise OAuthError("invalid_request")
            db.execute("DELETE FROM temporary WHERE hash = ?", (digest(value),))
            code = secrets.token_urlsafe(32) if approve else None
            if code:
                db.execute(
                    "INSERT INTO temporary VALUES (?, 'code', ?, ?)",
                    (digest(code), json.dumps(payload), time.time() + 120),
                )
            return code, payload

    def _tokens(self, db, grant):
        now = time.time()
        access = "csmcp_" + secrets.token_urlsafe(32)
        ttl = min(ACCESS_TTL, int(grant["expires"] - now))
        db.execute(
            "INSERT INTO tokens VALUES (?, 'access', ?, ?, 0)", (digest(access), grant["id"], now + ttl)
        )
        result = {"access_token": access, "token_type": "Bearer", "expires_in": ttl, "scope": grant["scope"]}
        if "offline_access" in grant["scope"].split():
            refresh = "csmcpr_" + secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO tokens VALUES (?, 'refresh', ?, ?, 0)",
                (digest(refresh), grant["id"], grant["expires"]),
            )
            result["refresh_token"] = refresh
        return result

    def exchange(self, code, client_id, redirect_uri, challenge, resource):
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM temporary WHERE hash = ? AND kind = 'code'", (digest(code),)
            ).fetchone()
            if not row:
                raise OAuthError("invalid_grant")
            payload = json.loads(row[0])
            expected = (
                payload["client_id"],
                payload["redirect_uri"],
                payload["code_challenge"],
                payload["resource"],
            )
            if expected != (client_id, redirect_uri, challenge, resource):
                raise OAuthError("invalid_grant")
            db.execute("DELETE FROM temporary WHERE hash = ?", (digest(code),))
            now = time.time()
            if (
                db.execute(
                    "SELECT COUNT(*) FROM grants WHERE user_id = ? AND revoked = 0", (payload["user_id"],)
                ).fetchone()[0]
                >= 20
            ):
                raise OAuthError("access_denied")
            grant = {
                "id": secrets.token_hex(16),
                "user_id": payload["user_id"],
                "client_id": client_id,
                "resource": resource,
                "scope": payload["scope"],
                "name": payload["name"],
                "created": now,
                "expires": now + GRANT_TTL,
            }
            db.execute(
                """INSERT INTO grants(id, user_id, client_id, resource, scope, name, created, expires)
                          VALUES (:id, :user_id, :client_id, :resource, :scope, :name, :created, :expires)""",
                grant,
            )
            return self._tokens(db, grant)

    def refresh(self, token, client_id, resource):
        replay = False
        result = None
        with self.transaction() as db:
            row = db.execute(
                """SELECT g.*, t.used FROM tokens t JOIN grants g ON g.id = t.grant_id
                                WHERE t.hash = ? AND t.kind = 'refresh' AND g.revoked = 0""",
                (digest(token),),
            ).fetchone()
            if not row or row["client_id"] != client_id or row["resource"] != resource:
                raise OAuthError("invalid_grant")
            if row["used"]:
                db.execute("UPDATE grants SET revoked = 1 WHERE id = ?", (row["id"],))
                replay = True
            else:
                db.execute("UPDATE tokens SET used = 1 WHERE hash = ?", (digest(token),))
                result = self._tokens(db, row)
        # Commit the revocation before reporting replay.
        if replay:
            raise OAuthError("invalid_grant")
        return result

    def resolve(self, token, resource):
        with self.transaction() as db:
            row = db.execute(
                """SELECT g.* FROM tokens t JOIN grants g ON g.id = t.grant_id
                                WHERE t.hash = ? AND t.kind = 'access' AND g.revoked = 0
                                AND g.resource = ?""",
                (digest(token), resource),
            ).fetchone()
            return dict(row) if row else None

    def connections(self, user_id):
        with self.transaction() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id, name, created, expires FROM grants WHERE user_id = ? AND revoked = 0",
                    (user_id,),
                )
            ]

    def revoke(self, grant_id, user_id):
        with self.transaction() as db:
            return (
                db.execute(
                    "UPDATE grants SET revoked = 1 WHERE id = ? AND user_id = ?", (grant_id, user_id)
                ).rowcount
                > 0
            )

    def revoke_token(self, token, client_id):
        with self.transaction() as db:
            db.execute(
                """UPDATE grants SET revoked = 1 WHERE client_id = ? AND id IN
                          (SELECT grant_id FROM tokens WHERE hash = ?)""",
                (client_id, digest(token)),
            )
