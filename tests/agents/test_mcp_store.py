import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from app.agents.mcp_store import OAuthStore, digest


class OAuthStoreTests(unittest.TestCase):
    # Тест-кейс: Провайдер определяется по выбранному callback и сохраняется после удаления регистрации клиента.
    def test_mixed_client_keeps_selected_provider_after_client_expiry(self) -> None:
        """Тест-кейс: Провайдер определяется по выбранному callback и сохраняется после удаления регистрации клиента.

        Ожидается: Провайдер определяется по выбранному callback и сохраняется после удаления регистрации клиента.
        """

        # Given
        with tempfile.TemporaryDirectory() as directory:
            store = OAuthStore(Path(directory) / "oauth.db")
            callbacks = [
                "https://claude.ai/api/mcp/auth_callback",
                "https://chatgpt.com/connector_platform_oauth_redirect",
            ]
            cid = store.register({"redirect_uris": callbacks})
            access_tokens = []
            for callback in callbacks:
                pending = store.pending(
                    {
                        "user_id": 1,
                        "cookie_hash": digest("cookie"),
                        "client_id": cid,
                        "redirect_uri": callback,
                        "code_challenge": "challenge",
                        "resource": "https://site.test/mcp",
                        "scope": "analytics:read",
                        "name": "Mixed client",
                    }
                )
                code, _ = store.consent(pending, 1, "cookie", True)
                tokens = store.exchange(code, cid, callback, "challenge", "https://site.test/mcp")
                access_tokens.append(tokens["access_token"])
            with store.transaction() as db:
                db.execute("UPDATE clients SET expires = ?", (time.time() - 1,))

            # When
            records = store.connections(1)
            resolved = [store.resolve(token, "https://site.test/mcp") for token in access_tokens]
            revoked = store.revoke(next(r.id for r in records if r.provider.value == "claude"), 1)
            after_revoke = store.resolve(access_tokens[0], "https://site.test/mcp")

            # Then
            self.assertEqual({record.provider.value for record in records}, {"claude", "chatgpt"})
            self.assertTrue(all(resolved))
            self.assertTrue(revoked)
            self.assertIsNone(after_revoke)

    # Тест-кейс: Миграция старой схемы сохраняет доступ, распознаёт просроченного клиента и оставляет неизвестные гранты видимыми.
    def test_legacy_migration_preserves_known_and_unknown_connections(self) -> None:
        """Тест-кейс: Миграция старой схемы сохраняет доступ, распознаёт просроченного клиента и оставляет неизвестные гранты видимыми.

        Ожидается: Миграция старой схемы сохраняет доступ, распознаёт просроченного клиента и оставляет неизвестные гранты видимыми.
        """

        # Given
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.db"
            now = time.time()
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE clients (id TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL);
                    CREATE TABLE grants (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, client_id TEXT NOT NULL,
                        resource TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL,
                        created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE tokens (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, grant_id TEXT NOT NULL,
                        expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                """)
                db.execute(
                    "INSERT INTO clients VALUES (?, ?, ?)",
                    (
                        "old",
                        json.dumps({"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}),
                        now - 1,
                    ),
                )
                db.execute(
                    "INSERT INTO clients VALUES (?, ?, ?)",
                    (
                        "mixed",
                        json.dumps({"redirect_uris": ["https://claude.ai/cb", "https://chatgpt.com/cb"]}),
                        now + 3600,
                    ),
                )
                for client_id in ("old", "missing", "mixed"):
                    db.execute(
                        "INSERT INTO grants VALUES (?, 1, ?, ?, ?, ?, ?, ?, 0)",
                        (
                            client_id,
                            client_id,
                            "https://site.test/mcp",
                            "analytics:read",
                            "Legacy",
                            now,
                            now + 3600,
                        ),
                    )
                db.execute(
                    "INSERT INTO tokens VALUES (?, 'access', 'old', ?, 0)",
                    (digest("existing-token"), now + 3600),
                )
            store = OAuthStore(path)

            # When
            first = store.connections(1)
            second = store.connections(1)
            existing = store.resolve("existing-token", "https://site.test/mcp")
            foreign = store.revoke("missing", 2)
            own = store.revoke("missing", 1)
            remaining = store.connections(1)

            # Then
            self.assertEqual(
                {r.id: r.provider.value for r in first},
                {"old": "claude", "missing": "unknown", "mixed": "unknown"},
            )
            self.assertEqual(first, second)
            self.assertIsNotNone(existing)
            self.assertFalse(foreign)
            self.assertTrue(own)
            self.assertEqual({r.id for r in remaining}, {"old", "mixed"})
