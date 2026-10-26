import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.catalog import documentation
from app.agents.schema import build_action_schema
from app.dto.identity import User
from app.dto.wb_standard_costs import MarginRoi, ReportRow, Scenario, Snapshot
from app.web.routers import agent_analytics, agent_full


class StandardCostsTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(agent_full.router, prefix="/api/agent/v1")
        self.app = app
        app.dependency_overrides[agent_analytics.employee] = lambda: User(
            id=1, login="test", full_name="Test", created_at=datetime.now(UTC)
        )
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def get(self, **params):
        return self.client.get("/api/agent/v1/standard-costs", params={"store": "rimili", **params})

    def test_scenarios_details_pagination_and_source_period(self):
        snapshot = Snapshot(
            version=6,
            date_from="2026-10-12",
            date_to="2026-10-18",
            updated_at="2026-10-19",
            rows=(
                ReportRow(
                    article="A",
                    scheme="FBW",
                    profit=Scenario(margin=None, missing=("commission",)),
                    current_profit=Scenario(margin=10, roi=20),
                    delta=MarginRoi(margin=None),
                ),
                ReportRow(
                    article="A",
                    scheme="FBS",
                    profit=Scenario(margin=15, roi=30),
                    current_profit=Scenario(margin=10, roi=20),
                    delta=MarginRoi(margin=5, roi=10),
                ),
            ),
        )
        with (
            patch("app.repositories.wb_standard_costs.load", return_value=snapshot) as load,
            patch("app.economics.wb.standard_profit.enrich", side_effect=lambda store, value: value),
        ):
            result = self.get(limit=1).json()
            self.assertEqual(result["total_rows"], 2)
            self.assertEqual(result["next_offset"], 1)
            self.assertEqual(result["totals"], {})
            self.assertEqual(result["context"]["snapshot_period"]["date_to"], "2026-10-18")
            self.assertIsNone(result["rows"][0]["profit"]["margin"])
            self.assertEqual(
                set(result["rows"][0]),
                {
                    "article",
                    "name",
                    "scheme",
                    "profit",
                    "current_profit",
                    "delta",
                    "calculator_date",
                    "warnings",
                },
            )
            self.assertEqual(
                set(result["rows"][0]["current_profit"]), {"margin", "roi", "complete", "missing"}
            )
            self.assertEqual(set(result["rows"][0]["profit"]), {"margin", "roi", "complete", "missing"})
            self.assertNotIn("retail", result["rows"][0]["profit"])
            self.assertEqual(result["rows"][0]["profit"]["missing"], ["commission"])
            second = self.get(limit=1, offset=1).json()
            self.assertEqual(second["rows"][0]["delta"]["roi"], 10)
            self.assertIsNone(second["next_offset"])
            self.assertEqual(self.get(article="not-found").json()["total_rows"], 0)
            load.assert_called_with("rimili")

    def test_missing_and_failed_source(self):
        with patch("app.repositories.wb_standard_costs.load", return_value=None):
            r = self.get()
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["context"]["available"])
            self.assertEqual(r.json()["rows"], [])
        with patch("app.repositories.wb_standard_costs.load", side_effect=RuntimeError("unavailable")):
            self.assertEqual(self.get().status_code, 503)

    def test_validation_schema_and_catalog(self):
        for params in (
            {"marketplace": "YANDEX MARKET"},
            {"date_from": "2026-10-12", "date_to": "2026-10-18"},
            {"scheme": "fbs"},
        ):
            self.assertEqual(self.get(**params).status_code, 422)
        schema = build_action_schema(self.app.routes)
        operation = schema["paths"]["/api/agent/v1/standard-costs"]["get"]
        self.assertEqual(operation["operationId"], "getAnalyticsStandardCosts")
        self.assertNotIn("date_from", [p["name"] for p in operation["parameters"]])
        doc = documentation("standard-costs")
        self.assertEqual(doc["title"], "Расходы WB без ИУ")
        self.assertTrue(any("delta.roi" in f["name"] for f in doc["fields"]))
        self.assertFalse(
            any(
                "calculator_evidence" in f["name"]
                or "profit.commission" in f["name"]
                or "profit.purchase" in f["name"]
                or "profit.total_cost" in f["name"]
                for f in doc["fields"]
            )
        )
        self.app.dependency_overrides.clear()
        self.assertEqual(self.get().status_code, 401)
