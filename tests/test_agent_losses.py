import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.agents.catalog import employee_catalog
from app.dto.identity import SectionName
from app.economics.yandex import reports as yandex
from app.web.routers import agent_analytics as api
from app.web.routers import agent_full

YM = "YANDEX MARKET"


class LossApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(api.router)
        self.user = object()

        async def employee(request: Request):
            request.state.user = self.user
            return self.user

        app.dependency_overrides[api.employee] = employee
        self.client = TestClient(app)
        self.params = dict(store="gogol", marketplace=YM, date_from="2026-01-01", date_to="2026-01-02")

    def row(self, article, margin, complete=True):
        return dict(
            store_slug="gogol",
            article=article,
            name=article,
            margin=margin,
            margin_complete=complete,
            orders_count=2,
            orders_amount=100,
            advertising_spend=10,
            roi=-5,
        )

    def test_yandex_http_uses_native_report_and_sorts_all_rows_before_limit(self):
        rows = [
            self.row("small", -10),
            self.row("incomplete", -1000, False),
            self.row("zero", 0),
            self.row("profit", 100),
            self.row("unknown", None),
            self.row("big", -100),
        ]
        with (
            patch.object(agent_full, "guard") as guard,
            patch.object(api, "accessible_stores", return_value=("gogol",)),
            patch.object(yandex, "load_rows", return_value=rows) as load,
            patch.object(api, "_unit_economics_1c_unit_profit_report_data", new_callable=AsyncMock) as wb,
        ):
            response = self.client.get("/api/agent/v1/loss-products", params={**self.params, "limit": 1})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["marketplace"], YM)
        self.assertEqual(data["total_products_checked"], 6)
        self.assertEqual(data["excluded_incomplete_products"], 2)
        self.assertEqual(data["total_loss_products"], 2)
        self.assertEqual(data["rows"][0]["article"], "big")
        self.assertEqual(data["rows"][0]["estimated_profit_rub"], -100)
        self.assertIsNone(data["rows"][0]["orders_updated_at"])
        self.assertEqual(guard.call_args.args[1], SectionName.UNIT_ECONOMICS_YANDEX)
        self.assertIs(load.call_args.args[1], self.user)
        self.assertEqual(load.call_args.args[0], ("gogol",))
        wb.assert_not_called()

    def test_article_filter_passed_to_native_manager_scoped_loader(self):
        with (
            patch.object(agent_full, "guard"),
            patch.object(api, "accessible_stores", return_value=("gogol",)),
            patch.object(yandex, "load_rows", return_value=[]) as load,
        ):
            response = self.client.get(
                "/api/agent/v1/loss-products", params={**self.params, "article": "hidden"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rows"], [])
        self.assertEqual(load.call_args.kwargs["article"], "hidden")

    def test_wb_is_still_default(self):
        params = {k: v for k, v in self.params.items() if k != "marketplace"}
        with (
            patch.object(agent_full, "guard") as guard,
            patch.object(api, "accessible_stores", return_value=("gogol",)),
            patch.object(
                api,
                "_unit_economics_1c_unit_profit_report_data",
                new_callable=AsyncMock,
                return_value={"rows": [self.row("wb", -20)]},
            ),
            patch.object(yandex, "load_rows") as load,
        ):
            response = self.client.get("/api/agent/v1/loss-products", params=params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["marketplace"], "WB")
        self.assertEqual(guard.call_args.args[1], SectionName.UNIT_ECONOMICS_WB)
        load.assert_not_called()

    def test_missing_yandex_permission_is_denied_before_reading(self):
        with (
            patch.object(
                agent_full, "permitted", side_effect=lambda u, s, *a: s == SectionName.UNIT_ECONOMICS_WB
            ),
            patch.object(yandex, "load_rows") as load,
        ):
            response = self.client.get("/api/agent/v1/loss-products", params=self.params)
        self.assertEqual(response.status_code, 403)
        load.assert_not_called()

    def test_invalid_platform_period_and_wrong_store_do_not_read(self):
        with (
            patch.object(agent_full, "guard"),
            patch.object(api, "accessible_stores", return_value=()),
            patch.object(yandex, "load_rows") as load,
        ):
            for override, status in [
                ({"marketplace": "OZON"}, 422),
                ({"date_to": "2025-12-01"}, 422),
                ({}, 403),
            ]:
                response = self.client.get("/api/agent/v1/loss-products", params={**self.params, **override})
                self.assertEqual(response.status_code, status)
        load.assert_not_called()

    def test_yandex_error_is_sanitized(self):
        with (
            patch.object(agent_full, "guard"),
            patch.object(api, "accessible_stores", return_value=("gogol",)),
            patch.object(yandex, "load_rows", side_effect=RuntimeError("private source error")),
        ):
            response = self.client.get("/api/agent/v1/loss-products", params=self.params)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)

    def test_capabilities_and_catalog_include_yandex_losses(self):
        with (
            patch.object(api, "accessible_stores", side_effect=lambda u, mp: ("gogol",) if mp == YM else ()),
            patch.object(
                agent_full,
                "permitted",
                side_effect=lambda u, s, store, mp, *a: s == SectionName.UNIT_ECONOMICS_YANDEX and mp == YM,
            ),
            patch.object(agent_full, "scope_pairs", return_value=(("gogol", YM),)),
            patch("app.access.access_control.scope_pairs", return_value=(("gogol", YM),)),
        ):
            available = asyncio.run(agent_full.capabilities(self.user))
            catalog = asyncio.run(employee_catalog(self.user))
        method = next(r for r in available["reports"] if r["report"] == "loss-products")
        self.assertEqual(method["scopes"], [{"store": "gogol", "marketplace": YM}])
        method = next(r for r in catalog["methods"] if r["name"] == "loss-products")
        self.assertTrue(method["marketplace_support"][YM])
        fields = {f["name"] for f in method["marketplace_guides"][YM]["fields"]}
        self.assertIn("estimated_profit_rub", fields)
        self.assertNotIn("orders_updated_at", fields)
        schema = asyncio.run(api.action_schema())
        params = schema["paths"]["/api/agent/v1/loss-products"]["get"]["parameters"]
        self.assertIn(YM, next(p for p in params if p["name"] == "marketplace")["schema"]["enum"])
