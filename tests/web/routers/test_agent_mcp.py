import base64
import hashlib
import os
import re
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.mcp_store import ACCESS_TTL, OAuthStore
from app.dto.identity import User
from app.web.routers import agent_management, agent_mcp


class McpOAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = OAuthStore(Path(directory.name) / "oauth.db")
        user = User(id=1, login="test", full_name="Test", created_at=datetime.now(UTC))
        environment = patch.dict(os.environ, {"CHECKSTOCK_AGENT_PUBLIC_URL": "https://site.test"})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("CHECKSTOCK_MCP_REDIRECT_URIS", None)
        for target, replacement in (
            ("storage", lambda: store),
            ("browser_user", AsyncMock(return_value=user)),
            ("active_user", AsyncMock(return_value=user)),
        ):
            mocked = patch.object(agent_mcp, target, replacement)
            mocked.start()
            self.addCleanup(mocked.stop)
        app = FastAPI()
        app.include_router(agent_mcp.router)
        self.client = TestClient(app, base_url="https://site.test")
        self.addCleanup(self.client.close)

    # Тест-кейс: метаданные OAuth объявляют issuer и поддержку его передачи.
    def test_metadata_announces_issuer(self) -> None:
        """Тест-кейс: клиент запрашивает настройки OAuth.

        Ожидается публичный issuer и поддержка iss в ответе авторизации.
        """
        # Given
        endpoint = "/.well-known/oauth-authorization-server"

        # When
        response = self.client.get(endpoint)

        # Then
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["issuer"], "https://site.test")
        self.assertTrue(response.json()["authorization_response_iss_parameter_supported"])

    # Тест-кейс: Claude и ChatGPT получают и продлевают доступ после согласия, блокировка прекращает доступ.
    def test_both_clients_authorize_and_deny_with_issuer(self) -> None:
        """Тест-кейс: оба клиента проходят OAuth с согласием, отказом и продлением после истечения часа.

        Ожидается доступ только на чтение без повторного входа; отказ и блокировка прекращают доступ.
        """
        # Given
        callbacks = (
            "https://claude.ai/api/mcp/auth_callback",
            "https://chatgpt.com/connector_platform_oauth_redirect",
        )
        verifier = "v" * 43
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        for callback in callbacks:
            for decision in ("approve", "deny"):
                with self.subTest(callback=callback, decision=decision):
                    # When
                    registered = self.client.post(
                        "/oauth/mcp/register",
                        json={"redirect_uris": [callback], "client_name": "Test MCP"},
                        headers={"Origin": "https://chatgpt.com"},
                    )
                    client_id = registered.json()["client_id"]
                    page = self.client.get(
                        "/oauth/mcp/authorize",
                        params={
                            "client_id": client_id,
                            "redirect_uri": callback,
                            "response_type": "code",
                            "code_challenge": challenge,
                            "code_challenge_method": "S256",
                            "resource": "https://site.test/mcp",
                            "scope": "analytics:read",
                            "state": "state-test",
                        },
                    )
                    pending = re.search(r'name="pending" value="([^"]+)"', page.text).group(1)
                    result = self.client.post(
                        "/oauth/mcp/authorize",
                        data={"pending": pending, "decision": decision},
                        follow_redirects=False,
                    )
                    params = parse_qs(urlsplit(result.headers["location"]).query)
                    tokens = None
                    if decision == "approve":
                        tokens = self.client.post(
                            "/oauth/mcp/token",
                            data={
                                "grant_type": "authorization_code",
                                "client_id": client_id,
                                "code": params["code"][0],
                                "redirect_uri": callback,
                                "code_verifier": verifier,
                                "resource": "https://site.test/mcp",
                            },
                        )

                    # Then
                    self.assertEqual(registered.status_code, 201)
                    self.assertEqual(page.status_code, 200)
                    self.assertEqual(result.status_code, 303)
                    self.assertEqual(params["iss"], ["https://site.test"])
                    self.assertEqual(params["state"], ["state-test"])
                    if decision == "deny":
                        self.assertEqual(params["error"], ["access_denied"])
                        self.assertNotIn("code", params)
                    else:
                        self.assertEqual(tokens.status_code, 200, tokens.text)
                        self.assertIn("access_token", tokens.json())
                        self.assertTrue("refresh_token" in tokens.json())
                        issued = tokens.json()
                        clock = time.time() + ACCESS_TTL + 1
                        request_headers = {"Accept": "application/json, text/event-stream"}
                        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

                        # When
                        with patch("app.agents.mcp_store.time.time", return_value=clock):
                            expired = self.client.post(
                                "/mcp",
                                json=ping,
                                headers={
                                    **request_headers,
                                    "Authorization": "Bearer " + issued["access_token"],
                                },
                            )
                            renewed = self.client.post(
                                "/oauth/mcp/token",
                                data={
                                    "grant_type": "refresh_token",
                                    "client_id": client_id,
                                    "refresh_token": issued["refresh_token"],
                                    "resource": "https://site.test/mcp",
                                },
                            )
                            self.assertEqual(renewed.status_code, 200)
                            refreshed = renewed.json()
                            authenticated = self.client.post(
                                "/mcp",
                                json=ping,
                                headers={
                                    **request_headers,
                                    "Authorization": "Bearer " + refreshed["access_token"],
                                },
                            )
                            with patch.object(agent_mcp, "active_user", AsyncMock(return_value=None)):
                                disabled = self.client.post(
                                    "/oauth/mcp/token",
                                    data={
                                        "grant_type": "refresh_token",
                                        "client_id": client_id,
                                        "refresh_token": refreshed["refresh_token"],
                                        "resource": "https://site.test/mcp",
                                    },
                                )
                            after_disable = self.client.post(
                                "/mcp",
                                json=ping,
                                headers={
                                    **request_headers,
                                    "Authorization": "Bearer " + refreshed["access_token"],
                                },
                            )

                        # Then
                        self.assertEqual(expired.status_code, 401)
                        self.assertIn("resource_metadata", expired.headers["www-authenticate"])
                        self.assertEqual(refreshed["scope"], "analytics:read")
                        self.assertEqual(refreshed["expires_in"], ACCESS_TTL)
                        self.assertTrue(refreshed["refresh_token"] != issued["refresh_token"])
                        self.assertEqual(authenticated.status_code, 200)
                        self.assertEqual(authenticated.json()["result"], {})
                        self.assertEqual(disabled.status_code, 400)
                        self.assertEqual(disabled.json()["error"], "invalid_grant")
                        self.assertEqual(after_disable.status_code, 401)

    # Тест-кейс: чужие callback и Origin не позволяют зарегистрировать клиента.
    def test_untrusted_registration_is_rejected(self) -> None:
        """Тест-кейс: регистрация с недоверенными адресами.

        Ожидается 400 для чужого callback и 403 для чужого Origin.
        """
        # Given
        requests = (
            ({"redirect_uris": ["https://evil.test/callback"]}, {}, 400),
            ({"redirect_uris": ["https://chatgpt.com.evil.test/connector_platform_oauth_redirect"]}, {}, 400),
            ({}, {"Origin": "https://evil.test"}, 403),
        )
        for payload, headers, expected in requests:
            with self.subTest(payload=payload, headers=headers):
                # When
                response = self.client.post("/oauth/mcp/register", json=payload, headers=headers)

                # Then
                self.assertEqual(response.status_code, expected)

    # Тест-кейс: явный список callback отключает не включённый в него ChatGPT.
    def test_explicit_allowlist_disables_other_callbacks(self) -> None:
        """Тест-кейс: администратор разрешил только Claude.

        Ожидается отказ в регистрации ChatGPT с кодом 400.
        """
        # Given
        with patch.dict(
            os.environ, {"CHECKSTOCK_MCP_REDIRECT_URIS": "https://claude.ai/api/mcp/auth_callback"}
        ):
            # When
            response = self.client.post(
                "/oauth/mcp/register",
                json={"redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"]},
            )

            # Then
            self.assertEqual(response.status_code, 400)


class ConnectionTests(unittest.TestCase):
    # Тест-кейс: Подключения разделяются по провайдеру; отозвать можно только собственное подключение.
    def test_filter_existing_grants_and_revoke_ownership(self) -> None:
        """Тест-кейс: Подключения разделяются по провайдеру; отозвать можно только собственное подключение.

        Ожидается: Подключения разделяются по провайдеру; отозвать можно только собственное подключение.
        """

        # Given
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
                # When
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

                # Then
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
