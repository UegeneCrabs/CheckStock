import os
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.mcp_store import OAuthStore
from app.dto.identity import User
from app.web.routers import agent_management, agent_mcp


class ConnectionTests(unittest.TestCase):
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
                        "INSERT INTO grants VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
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
                for provider, expected in [("claude", ["c"]), ("chatgpt", ["g"]), ("unknown", ["unknown"])]:
                    r = client.get("/api/ai-agents/mcp/connections", params={"provider": provider})
                    self.assertEqual(r.status_code, 200)
                    self.assertEqual([x["id"] for x in r.json()["connections"]], expected)
                self.assertEqual(len(client.get("/api/ai-agents/mcp/connections").json()["connections"]), 3)
                self.assertEqual(client.delete("/api/ai-agents/mcp/connections/other").status_code, 404)
                self.assertEqual(client.delete("/api/ai-agents/mcp/connections/g").status_code, 204)
                self.assertEqual(
                    client.get("/api/ai-agents/mcp/connections?provider=chatgpt").json()["connections"], []
                )
                self.assertEqual(
                    len(client.get("/api/ai-agents/mcp/connections?provider=claude").json()["connections"]), 1
                )
                self.assertEqual(
                    client.get("/api/ai-agents/mcp/connections?provider=invalid").status_code, 422
                )
