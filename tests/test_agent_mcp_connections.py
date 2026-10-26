import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.mcp_store import OAuthStore, digest
from app.dto.identity import User
from app.web.routers import agent_management, agent_mcp


class ConnectionTests(unittest.TestCase):
    # Подключения разделяются по провайдеру; отозвать можно только собственное подключение.
    def test_filter_existing_grants_and_revoke_ownership(self):
        app = FastAPI()
        app.include_router(agent_mcp.router)
        user = User(id=1, login="test", full_name="Test", created_at=datetime.now(UTC))
        app.dependency_overrides[agent_management.owner] = lambda: user
        with tempfile.TemporaryDirectory() as directory:
            store = OAuthStore(Path(directory) / "oauth.db")
            for grant_id, host, owner in [
                ("c", "claude.ai", 1),
                ("g", "chatgpt.com", 1),
                ("other", "chatgpt.com", 2),
                ("unknown", "chatgpt.com.evil.test", 1),
            ]:
                cid = store.register({"redirect_uris": ["https://" + host + "/callback"]})
                with store.transaction() as db:
                    db.execute(
                        "INSERT INTO grants(id, user_id, client_id, resource, scope, name, created, expires, revoked) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (
                            grant_id,
                            owner,
                            cid,
                            "https://site.test/mcp",
                            "analytics:read",
                            "Same name",
                            time.time(),
                            time.time() + 3600,
                        ),
                    )
            with (
                patch.dict(os.environ, {"CHECKSTOCK_AGENT_PUBLIC_URL": "https://site.test"}),
                patch.object(agent_mcp, "storage", return_value=store),
                TestClient(app) as client,
            ):
                filtered = {
                    provider: client.get("/api/ai-agents/mcp/connections", params={"provider": provider})
                    for provider in ("claude", "chatgpt", "unknown")
                }
                all_connections = client.get("/api/ai-agents/mcp/connections")
                foreign_revoke = client.delete("/api/ai-agents/mcp/connections/other")
                own_revoke = client.delete("/api/ai-agents/mcp/connections/g")
                remaining_chatgpt = client.get("/api/ai-agents/mcp/connections?provider=chatgpt")
                remaining_claude = client.get("/api/ai-agents/mcp/connections?provider=claude")
                invalid = client.get("/api/ai-agents/mcp/connections?provider=invalid")
                with patch.dict(os.environ, {"CHECKSTOCK_AGENT_PUBLIC_URL": ""}):
                    without_url = client.get("/api/ai-agents/mcp/connections?provider=claude")
                    unknown_revoke = client.delete("/api/ai-agents/mcp/connections/unknown")
                    after_unknown_revoke = client.get("/api/ai-agents/mcp/connections?provider=claude")

                for provider, expected in [("claude", ["c"]), ("chatgpt", ["g"]), ("unknown", ["unknown"])]:
                    with self.subTest(provider=provider):
                        self.assertEqual(filtered[provider].status_code, 200)
                        self.assertEqual(
                            [x["id"] for x in filtered[provider].json()["connections"]], expected
                        )
                self.assertEqual(len(all_connections.json()["connections"]), 3)
                self.assertEqual(
                    [x["id"] for x in filtered["claude"].json()["other_connections"]], ["unknown"]
                )
                self.assertEqual(
                    [x["id"] for x in filtered["chatgpt"].json()["other_connections"]], ["unknown"]
                )
                self.assertEqual(all_connections.json()["other_connections"], [])
                self.assertEqual(foreign_revoke.status_code, 404)
                self.assertEqual(own_revoke.status_code, 204)
                self.assertEqual(remaining_chatgpt.json()["connections"], [])
                self.assertEqual(len(remaining_claude.json()["connections"]), 1)
                self.assertEqual(invalid.status_code, 422)
                self.assertIsNone(without_url.json()["url"])
                self.assertEqual([x["id"] for x in without_url.json()["other_connections"]], ["unknown"])
                self.assertEqual(unknown_revoke.status_code, 204)
                self.assertEqual(after_unknown_revoke.json()["other_connections"], [])

    # Провайдер определяется по выбранному callback и сохраняется после удаления регистрации клиента.
    def test_mixed_client_keeps_selected_provider_after_client_expiry(self):
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

            records = store.connections(1)
            resolved = [store.resolve(token, "https://site.test/mcp") for token in access_tokens]
            revoked = store.revoke(next(r.id for r in records if r.provider.value == "claude"), 1)
            after_revoke = store.resolve(access_tokens[0], "https://site.test/mcp")

            self.assertEqual({record.provider.value for record in records}, {"claude", "chatgpt"})
            self.assertTrue(all(resolved))
            self.assertTrue(revoked)
            self.assertIsNone(after_revoke)

    # Миграция старой схемы сохраняет доступ, распознаёт просроченного клиента и оставляет неизвестные гранты видимыми.
    def test_legacy_migration_preserves_known_and_unknown_connections(self):
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

            first = store.connections(1)
            second = store.connections(1)
            existing = store.resolve("existing-token", "https://site.test/mcp")
            foreign = store.revoke("missing", 2)
            own = store.revoke("missing", 1)
            remaining = store.connections(1)

            self.assertEqual(
                {r.id: r.provider.value for r in first},
                {"old": "claude", "missing": "unknown", "mixed": "unknown"},
            )
            self.assertEqual(first, second)
            self.assertIsNotNone(existing)
            self.assertFalse(foreign)
            self.assertTrue(own)
            self.assertEqual({r.id for r in remaining}, {"old", "mixed"})
