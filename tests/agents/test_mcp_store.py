import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.agents.mcp_store import ACCESS_TTL, GRANT_TTL, OAuthError, OAuthStore, digest


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


class RefreshTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = OAuthStore(Path(directory.name) / "oauth.db")
        self.resource = "https://site.test/mcp"
        self.callback = "https://chatgpt.com/connector_platform_oauth_redirect"
        self.client_id = self.store.register({"redirect_uris": [self.callback]})

    def issue(self, scope: str = "analytics:read"):
        pending = self.store.pending(
            {
                "user_id": 1,
                "cookie_hash": digest("cookie"),
                "client_id": self.client_id,
                "redirect_uri": self.callback,
                "code_challenge": "challenge",
                "resource": self.resource,
                "scope": scope,
                "name": "Refresh test",
            }
        )
        code, _ = self.store.consent(pending, 1, "cookie", True)
        return self.store.exchange(code, self.client_id, self.callback, "challenge", self.resource)

    # Тест-кейс: Согласованный доступ только на чтение получает продление без дополнительного scope.
    def test_read_only_grant_receives_hashed_refresh_without_offline_scope(self) -> None:
        """Тест-кейс: Клиент запрашивает только analytics:read.

        Ожидается refresh token без расширения scope и без хранения секретов открытым текстом.
        """
        # Given
        scope = "analytics:read"

        # When
        tokens = self.issue(scope)
        with self.store.transaction() as db:
            saved = db.execute("SELECT hash, kind FROM tokens").fetchall()

        # Then
        self.assertTrue("refresh_token" in tokens, "Read-only consent must issue a refresh token")
        self.assertEqual(tokens["scope"], scope)
        self.assertEqual(tokens["expires_in"], ACCESS_TTL)
        self.assertEqual({row["kind"] for row in saved}, {"access", "refresh"})
        self.assertEqual(
            {row["hash"] for row in saved}, {digest(tokens["access_token"]), digest(tokens["refresh_token"])}
        )

    # Тест-кейс: После истечения часа клиент обновляет доступ в пределах исходного разрешения.
    def test_refresh_after_expiry_preserves_identity_scope_and_grant_deadline(self) -> None:
        """Тест-кейс: Исходный access token истёк, refresh token ещё действителен.

        Ожидается новый доступ с тем же владельцем и сроком разрешения без повторного согласия.
        """
        for scope in ("analytics:read", "analytics:read offline_access"):
            with self.subTest(scope=scope):
                # Given
                tokens = self.issue(scope)
                grant = self.store.resolve(tokens["access_token"], self.resource)
                now = time.time()

                # When
                with patch("app.agents.mcp_store.time.time", return_value=now + ACCESS_TTL + 1):
                    self.store = OAuthStore(self.store.path)
                    expired = self.store.resolve(tokens["access_token"], self.resource)
                    renewed = self.store.refresh(tokens["refresh_token"], self.client_id, self.resource)
                    current = self.store.resolve(renewed["access_token"], self.resource)

                # Then
                self.assertIsNone(expired)
                self.assertEqual(current["id"], grant["id"])
                self.assertEqual(current["user_id"], grant["user_id"])
                self.assertEqual(current["scope"], scope)
                self.assertEqual(current["expires"], grant["expires"])
                self.assertEqual(renewed["scope"], scope)
                self.assertNotEqual(renewed["refresh_token"], tokens["refresh_token"])
                self.assertEqual(renewed["expires_in"], ACCESS_TTL)

    # Тест-кейс: Чужой клиент или ресурс не могут использовать и погасить токен владельца.
    def test_wrong_client_and_resource_do_not_consume_refresh(self) -> None:
        """Тест-кейс: Продление пытаются выполнить с чужой привязкой.

        Ожидается отказ, после которого правильный клиент по-прежнему может обновить доступ.
        """
        # Given
        tokens = self.issue()

        # When
        for client_id, resource in (("other", self.resource), (self.client_id, "https://other.test/mcp")):
            with self.assertRaisesRegex(OAuthError, "invalid_grant"):
                self.store.refresh(tokens["refresh_token"], client_id, resource)
        renewed = self.store.refresh(tokens["refresh_token"], self.client_id, self.resource)

        # Then
        self.assertIsNotNone(self.store.resolve(renewed["access_token"], self.resource))
        self.assertEqual(renewed["scope"], "analytics:read")

    # Тест-кейс: Повторное предъявление уже заменённого refresh token отзывает всё подключение.
    def test_refresh_replay_revokes_token_family(self) -> None:
        """Тест-кейс: После успешной ротации повторно используют старый токен.

        Ожидается отказ и прекращение действия как старых, так и новых токенов подключения.
        """
        # Given
        tokens = self.issue()
        renewed = self.store.refresh(tokens["refresh_token"], self.client_id, self.resource)

        # When
        with self.assertRaisesRegex(OAuthError, "invalid_grant"):
            self.store.refresh(tokens["refresh_token"], self.client_id, self.resource)

        # Then
        self.assertIsNone(self.store.resolve(tokens["access_token"], self.resource))
        self.assertIsNone(self.store.resolve(renewed["access_token"], self.resource))
        with self.assertRaisesRegex(OAuthError, "invalid_grant"):
            self.store.refresh(renewed["refresh_token"], self.client_id, self.resource)

    # Тест-кейс: Отзыв и окончание тридцатидневного разрешения запрещают продление.
    def test_revoked_and_expired_grants_cannot_refresh(self) -> None:
        """Тест-кейс: Разрешение отозвано либо закончился его предельный срок.

        Ожидается отказ в продлении без восстановления или увеличения срока доступа.
        """
        # Given
        revoked = self.issue()
        expired = self.issue()
        grant = self.store.resolve(revoked["access_token"], self.resource)

        # When
        self.store.revoke(grant["id"], grant["user_id"])

        # Then
        with self.assertRaisesRegex(OAuthError, "invalid_grant"):
            self.store.refresh(revoked["refresh_token"], self.client_id, self.resource)
        with patch("app.agents.mcp_store.time.time", return_value=time.time() + GRANT_TTL + 1):
            with self.assertRaisesRegex(OAuthError, "invalid_grant"):
                self.store.refresh(expired["refresh_token"], self.client_id, self.resource)

    # Тест-кейс: Продление перед окончанием разрешения не выдаёт доступ сверх оставшегося срока.
    def test_refresh_near_grant_deadline_limits_access_lifetime(self) -> None:
        """Тест-кейс: До окончания исходного разрешения осталось меньше часа.

        Ожидается ограниченный остатком времени access token и отказ после предельной даты.
        """
        # Given
        tokens = self.issue()
        grant = self.store.resolve(tokens["access_token"], self.resource)

        # When
        with patch("app.agents.mcp_store.time.time", return_value=grant["expires"] - 25):
            renewed = self.store.refresh(tokens["refresh_token"], self.client_id, self.resource)

        # Then
        self.assertGreater(renewed["expires_in"], 0)
        self.assertLessEqual(renewed["expires_in"], 25)
        with patch("app.agents.mcp_store.time.time", return_value=grant["expires"] + 1):
            self.assertIsNone(self.store.resolve(renewed["access_token"], self.resource))
            with self.assertRaisesRegex(OAuthError, "invalid_grant"):
                self.store.refresh(renewed["refresh_token"], self.client_id, self.resource)
