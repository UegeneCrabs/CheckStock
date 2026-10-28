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

    # API возвращает компактные сценарии прибыли, исходный период и все страницы без потери null.
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
            second = self.get(limit=1, offset=1).json()
            absent = self.get(article="not-found").json()

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
            self.assertEqual(second["rows"][0]["delta"]["roi"], 10)
            self.assertIsNone(second["next_offset"])
            self.assertEqual(absent["total_rows"], 0)
            load.assert_called_with("rimili")

    # Отсутствующий отчёт даёт пустой ответ, а сбой источника возвращает 503.
    def test_missing_and_failed_source(self):
        with patch("app.repositories.wb_standard_costs.load", return_value=None):
            r = self.get()
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["context"]["available"])
            self.assertEqual(r.json()["rows"], [])
        with patch("app.repositories.wb_standard_costs.load", side_effect=RuntimeError("unavailable")):
            response = self.get()

            self.assertEqual(response.status_code, 503)

    # API отклоняет неподдерживаемые параметры и запрос без авторизации; каталог соответствует схеме.
    def test_validation_schema_and_catalog(self):
        for params in (
            {"marketplace": "YANDEX MARKET"},
            {"date_from": "2026-10-12", "date_to": "2026-10-18"},
            {"scheme": "fbs"},
        ):
            response = self.get(**params)

            self.assertEqual(response.status_code, 422)
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
        response = self.get()

        self.assertEqual(response.status_code, 401)

    # Поздние причины неполноты меняют только пометку; числа сохраняются в расчёте сайта и API.
    def test_late_missing_reasons_preserve_values_in_report_and_api(self):
        from app.dto.wb_standard_costs import CalculatorProduct, StorageCalculation
        from app.economics.wb import standard_profit

        row = ReportRow(article="TEST-COMPLETE", scheme="FBW")
        scenario = Scenario(margin=200, roi=40, complete=True)
        for reasons in ([], ["Не хватает данных о хранении"]):
            with (
                self.subTest(reasons=reasons),
                patch.object(standard_profit, "calculate_scenario", return_value=scenario),
                patch.object(
                    standard_profit, "storage_per_unit", return_value=(StorageCalculation(value=10), reasons)
                ),
                patch.object(standard_profit, "logistics_with_returns") as logistics,
            ):
                logistics.return_value.total = 20
                logistics.return_value.missing = ()

                comparison = standard_profit.compare(row, CalculatorProduct())
                report_row = row.model_copy(
                    update={
                        "profit": comparison.standard,
                        "current_profit": comparison.current,
                        "delta": comparison.delta,
                    }
                )
                snapshot = Snapshot(date_from="2026-10-12", date_to="2026-10-18", rows=(report_row,))
                with (
                    patch("app.repositories.wb_standard_costs.load", return_value=snapshot),
                    patch.object(standard_profit, "enrich", side_effect=lambda store, value: value),
                ):
                    response = self.get()

                self.assertEqual(comparison.standard.complete, not reasons)
                self.assertEqual(comparison.standard.missing, tuple(reasons))
                self.assertEqual(comparison.standard.margin, 200)
                self.assertEqual(comparison.standard.roi, 40)
                self.assertEqual(comparison.delta.margin, 0)
                self.assertEqual(comparison.delta.roi, 0)
                self.assertEqual(response.status_code, 200)
                returned = response.json()["rows"][0]
                self.assertEqual(
                    returned["profit"],
                    {
                        "margin": 200,
                        "roi": 40,
                        "complete": not reasons,
                        "missing": reasons,
                    },
                )
                self.assertTrue(returned["current_profit"]["complete"])
