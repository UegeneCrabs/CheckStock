import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.agents.catalog import DOCS, documentation, employee_catalog
from app.web.routers import agent_analytics, agent_full, agent_management


class CatalogTests(unittest.TestCase):
    def test_action_schema_is_ready_for_gpt_with_configured_origin(self):
        with patch.dict(os.environ, {"CHECKSTOCK_AGENT_PUBLIC_URL": "https://checkstock.example/"}):
            schema = asyncio.run(agent_analytics.action_schema())
        self.assertEqual(schema["servers"], [{"url": "https://checkstock.example"}])
        operations = [item["get"] for item in schema["paths"].values()]
        self.assertEqual(len(operations), 30)
        self.assertEqual(len({op["operationId"] for op in operations}), len(operations))
        for operation in operations:
            with self.subTest(method=operation["operationId"]):
                self.assertLessEqual(len(operation.get("description", "")), 300)
                self.assertEqual(operation["security"], [{"EmployeeApiKey": []}])

    def test_schema_does_not_invent_an_origin_when_unconfigured(self):
        with patch.dict(os.environ, {"CHECKSTOCK_AGENT_PUBLIC_URL": ""}):
            self.assertEqual(asyncio.run(agent_analytics.action_schema())["servers"], [])

    def test_gpt_instructions_fit_even_with_windows_line_endings(self):
        path = Path(__file__).resolve().parents[1] / "static/agents/agent-instructions.txt"
        text = path.read_text(encoding="utf-8").replace("\n", "\r\n")
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 8000)

    def test_detailed_paths_match_calculator_and_conditional_views(self):
        from app.agents.calculator import INPUT_LABELS, calculator

        fields = documentation("profit-calculator")["fields"]
        paths = {f["path"] for f in fields}
        for key in INPUT_LABELS:
            self.assertIn("rows[].inputs." + key, paths)
        for key in calculator({"article": "demo"})["results"]:
            self.assertIn("rows[].results." + key, paths)
        self.assertNotIn("rows[].labels", paths)
        stock = {f["name"]: f for f in documentation("stocks")["fields"]}
        self.assertIn("view=summary", stock["total"]["notes"])
        self.assertEqual(stock["context.labels"]["path"], "context.labels")
        self.assertNotIn("cpc_rub", {f["name"] for f in documentation("advertising-campaigns")["fields"]})

    def test_preview_route_removed(self):
        self.assertEqual(self.client().get("/api/ai-agents/catalog/preview?method=orders").status_code, 404)

    def test_reference_covers_schema_and_nullable_types(self):
        schema = asyncio.run(agent_analytics.action_schema())
        self.assertEqual(set(DOCS), {p.rsplit("/", 1)[-1] for p in schema["paths"]})
        fields = schema["paths"]["/api/agent/v1/current-economics"]["get"]["x-response-field-guide"]
        self.assertEqual(next(f["type"] for f in fields if f["name"] == "roi_percent"), "number|null")

    def test_catalog_omits_reports_without_scopes(self):
        with (
            patch.object(
                agent_full,
                "capabilities",
                new_callable=AsyncMock,
                return_value={
                    "reports": [
                        {"report": "orders", "scopes": []},
                        {"report": "stocks", "scopes": [{"store": "one", "marketplace": "WB"}]},
                    ]
                },
            ),
            patch("app.access.access_control.scope_pairs", return_value=(("one", "WB"),)),
        ):
            result = asyncio.run(employee_catalog(object()))
        names = {m["name"] for m in result["methods"]}
        self.assertIn("stocks", names)
        self.assertNotIn("orders", names)

    def client(self):
        app = FastAPI()
        app.include_router(agent_management.router)
        app.dependency_overrides[agent_management.owner] = lambda: SimpleNamespace(id=1)
        return TestClient(app)

    def test_catalog_requires_owner_and_old_link_redirects(self):
        client = self.client()
        self.assertEqual(
            client.get("/ai-agents/sheets", follow_redirects=False).headers["location"],
            "/ai-agents/catalog",
        )

        def forbidden():
            raise HTTPException(403)

        client.app.dependency_overrides[agent_management.owner] = forbidden
        for path in (
            "/api/ai-agents/catalog",
            "/ai-agents/catalog",
        ):
            self.assertEqual(client.get(path).status_code, 403)
