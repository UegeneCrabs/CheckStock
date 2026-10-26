import base64
import hashlib
import os
import re
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.mcp_store import OAuthStore
from app.dto.identity import User
from app.web.routers import agent_mcp


class McpOAuthTests(unittest.TestCase):
    def test_both_clients_authorize_and_deny_with_issuer(self):
        app = FastAPI()
        app.include_router(agent_mcp.router)
        user = User(id=1, login="test", full_name="Test", created_at=datetime.now(UTC))
        with tempfile.TemporaryDirectory() as directory:
            store = OAuthStore(Path(directory) / "oauth.db")
            with (
                patch.dict(os.environ, {"CHECKSTOCK_AGENT_PUBLIC_URL": "https://site.test"}),
                patch.object(agent_mcp, "storage", return_value=store),
                patch.object(agent_mcp, "browser_user", AsyncMock(return_value=user)),
                patch.object(agent_mcp, "active_user", AsyncMock(return_value=user)),
            ):
                with patch.dict(os.environ):
                    os.environ.pop("CHECKSTOCK_MCP_REDIRECT_URIS", None)
                    with TestClient(app, base_url="https://site.test") as client:
                        metadata = client.get("/.well-known/oauth-authorization-server").json()
                        self.assertTrue(metadata["authorization_response_iss_parameter_supported"])
                        for callback in agent_mcp.allowed_redirects():
                            for decision in ("approve", "deny"):
                                with self.subTest(callback=callback, decision=decision):
                                    registered = client.post(
                                        "/oauth/mcp/register",
                                        json={"redirect_uris": [callback], "client_name": "Test MCP"},
                                        headers={"Origin": "https://chatgpt.com"},
                                    )
                                    self.assertEqual(registered.status_code, 201)
                                    client_id = registered.json()["client_id"]
                                    verifier = "v" * 43
                                    challenge = (
                                        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                                        .decode()
                                        .rstrip("=")
                                    )
                                    page = client.get(
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
                                    self.assertEqual(page.status_code, 200)
                                    pending = re.search(r'name="pending" value="([^"]+)"', page.text).group(1)
                                    result = client.post(
                                        "/oauth/mcp/authorize",
                                        data={"pending": pending, "decision": decision},
                                        follow_redirects=False,
                                    )
                                    self.assertEqual(result.status_code, 303)
                                    params = parse_qs(urlsplit(result.headers["location"]).query)
                                    self.assertEqual(params["iss"], [metadata["issuer"]])
                                    self.assertEqual(params["state"], ["state-test"])
                                    if decision == "deny":
                                        self.assertEqual(params["error"], ["access_denied"])
                                    else:
                                        tokens = client.post(
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
                                        self.assertEqual(tokens.status_code, 200, tokens.text)
                                        self.assertIn("access_token", tokens.json())
                        for callback in (
                            "https://evil.test/callback",
                            "https://chatgpt.com.evil.test/connector_platform_oauth_redirect",
                        ):
                            self.assertEqual(
                                client.post(
                                    "/oauth/mcp/register", json={"redirect_uris": [callback]}
                                ).status_code,
                                400,
                            )
                        self.assertEqual(
                            client.post(
                                "/oauth/mcp/register", json={}, headers={"Origin": "https://evil.test"}
                            ).status_code,
                            403,
                        )
                        with patch.dict(
                            os.environ,
                            {"CHECKSTOCK_MCP_REDIRECT_URIS": "https://claude.ai/api/mcp/auth_callback"},
                        ):
                            self.assertEqual(
                                client.post(
                                    "/oauth/mcp/register",
                                    json={
                                        "redirect_uris": [
                                            "https://chatgpt.com/connector_platform_oauth_redirect"
                                        ]
                                    },
                                ).status_code,
                                400,
                            )
