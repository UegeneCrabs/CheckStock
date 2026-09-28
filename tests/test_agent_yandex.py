import asyncio
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.agents import extended_reports, reports
from app.agents import yandex_reports as ym
from app.agents.catalog import documentation
from app.dto.identity import SectionName as S
from app.web.routers import agent_analytics, agent_full


class YandexApiTests(unittest.TestCase):
    def test_current_spp_matches_dashboard_prices_without_pay_discount(self):
        for seller, buyer, expected in [(1000, 800, 20), (1000, 1000, 0),
                                        (None, 800, None), (1000, None, None), (0, 800, None)]:
            with self.subTest(seller=seller, buyer=buyer):
                product = {"article": "a", "price": {"current": seller, "with_spp": buyer, "with_wallet": 500}}
                with (patch.object(ym.ym, "catalog", return_value=[{"article": "a"}]),
                      patch.object(ym.calculations, "load_products", return_value=[product])):
                    result = asyncio.run(ym.execute("current-economics", self.query(), object()))
                self.assertEqual(result["rows"][0]["spp_percent"], expected)
        fields = documentation("current-economics")["marketplace_guides"][ym.MARKETPLACE]["fields"]
        self.assertEqual(next(f for f in fields if f["name"] == "spp_percent")["type"], "number|null")

    def query(self, **values):
        return agent_full.ReportQuery(store="gogol", marketplace=ym.MARKETPLACE, **values)

    def test_sections_actions_and_scope_are_marketplace_specific(self):
        def access(user, section):
            return section == S.UNIT_ECONOMICS_YANDEX

        with (
            patch.object(agent_full, "has_access", side_effect=access),
            patch.object(agent_full, "scope_pairs", return_value=(("gogol", ym.MARKETPLACE),)),
            patch.object(agent_full, "has_action_permission", return_value=True) as action,
        ):
            self.assertTrue(agent_full.report_permitted(object(), "costs", "gogol", ym.MARKETPLACE))
            self.assertEqual(action.call_args.kwargs["marketplace"], ym.MARKETPLACE)
            self.assertFalse(agent_full.report_permitted(object(), "costs", "gogol", "WB"))
            self.assertFalse(agent_full.report_permitted(object(), "costs", "other", ym.MARKETPLACE))
            self.assertFalse(agent_full.report_permitted(object(), "profit", "gogol", ym.MARKETPLACE))

    def test_yandex_only_employee_discovers_reports(self):
        with (
            patch.object(agent_full, "has_access", side_effect=lambda u, s: s == S.UNIT_ECONOMICS_YANDEX),
            patch.object(agent_full, "scope_pairs", return_value=(("gogol", ym.MARKETPLACE),)),
            patch.object(agent_full, "has_action_permission", return_value=True),
            patch.object(agent_analytics, "accessible_stores", return_value=()),
        ):
            data = asyncio.run(agent_full.capabilities(object()))
        found = {r["report"]: r["scopes"] for r in data["reports"]}
        self.assertEqual(found["costs"], [{"store": "gogol", "marketplace": ym.MARKETPLACE}])
        self.assertNotIn("advertising-campaigns", found)

    def test_validation_rejects_unsupported_and_historical_prices(self):
        for name, query in [
            ("advertising-campaigns", self.query()),
            ("prices", self.query(date_from="2026-01-01", date_to="2026-01-02")),
            ("costs", agent_full.ReportQuery(store="gogol", marketplace="OZON")),
        ]:
            with self.assertRaises(HTTPException) as raised:
                agent_full.validate(name, query, query.model_fields_set)
            self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(
            agent_full.validate("profit", self.query(date_from="2026-01-01", date_to="2026-01-02"), {}),
            S.REPORT_UNIT_PROFIT_YANDEX,
        )

    def test_native_profit_values_pagination_and_missing_data(self):
        products = [{"article": "a"}, {"article": "b"}]
        source = [
            dict(
                article="a",
                orders_amount=120,
                margin=15,
                roi=10,
                margin_complete=False,
                margin_missing_days=["2026-01-02"],
                expected_buyout_amount=100,
                daily_calculations=[{"private": "omit"}],
            ),
            dict(article="b", orders_amount=200, margin=50, roi=25, margin_complete=True),
        ]
        query = self.query(date_from="2026-01-01", date_to="2026-01-02", limit=1, order="asc")
        with (
            patch.object(ym.ym, "catalog", return_value=products),
            patch.object(ym.ym, "load_rows", return_value=source),
            patch.object(reports, "economic_filter", side_effect=AssertionError("WB used")),
        ):
            result = asyncio.run(ym.execute("profit", query, object()))
        self.assertEqual(result["next_offset"], 1)
        row = result["rows"][0]
        self.assertEqual(row["report_margin"], 15)
        self.assertIsNone(row["margin"])
        self.assertEqual(row["margin_missing_days"], ["2026-01-02"])
        self.assertEqual(row["expected_buyout_amount"], 100)
        self.assertNotIn("daily_calculations", row)
        self.assertEqual(source[0]["margin"], 15)  # cache is not mutated

    def test_orders_use_yandex_manager_scope(self):
        rows = [
            {"article": "allowed", "external_order_id": "1"},
            {"article": "hidden", "external_order_id": "2"},
        ]
        with (
            patch.object(reports, "read", return_value=rows) as read,
            patch.object(ym.ym, "catalog", return_value=[{"article": "allowed", "manager": "Alex"}]),
            patch.object(reports, "economic_filter", side_effect=AssertionError("WB used")),
        ):
            result = extended_reports.orders(
                self.query(date_from="2026-01-01", date_to="2026-01-02", manager="alex"), object()
            )
        self.assertEqual([r["article"] for r in result], ["allowed"])
        self.assertEqual(read.call_args.args[1][1], ym.MARKETPLACE)

    def test_inaccessible_article_does_not_load_calculator(self):
        with patch.object(ym.ym, "catalog", return_value=[]), patch.object(ym.economics, "detail") as load:
            with self.assertRaises(HTTPException) as raised:
                ym.load("profit-calculator", self.query(article="hidden"), object())
        self.assertEqual(raised.exception.status_code, 404)
        load.assert_not_called()

    def test_calculator_reuses_yandex_saved_calculation(self):
        data = {
            "calculator_values": {"pay_price": 100},
            "calculator_result": {"margin": 17, "roi": None},
            "calculator_origins": {},
            "calculator_pricing": {},
            "calculator_tariff": {},
            "calculator_advertising": {},
        }
        with (
            patch.object(ym.ym, "catalog", return_value=[{"article": "a", "name": "A"}]),
            patch.object(ym.economics, "detail", return_value=data) as load,
        ):
            rows, _ = ym.load("profit-calculator", self.query(article="a"), object())
        self.assertEqual(rows[0]["results"], data["calculator_result"])
        load.assert_called_once_with("gogol", "a", mode="calculator", include_history=False)

    def test_advertising_coverage_and_null_are_preserved(self):
        raw = [{"article": "a", "day": "2026-01-01", "spend": 30, "impressions": 100, "clicks": None}]
        with (
            patch.object(ym.ym, "catalog", return_value=[{"article": "a"}]),
            patch.object(ym.metrics, "get_history", return_value=(raw, {"2026-01-01"})),
        ):
            rows, context = ym.load(
                "advertising",
                self.query(date_from="2026-01-01", date_to="2026-01-02", group_by="day"),
                object(),
            )
        self.assertEqual(rows[0]["spend"], 30)
        self.assertIsNone(rows[0]["ctr_percent"])
        self.assertEqual(context["missing_days"], ["2026-01-02"])
        self.assertFalse(context["complete"])

    def test_documentation_does_not_label_yandex_as_wb(self):
        fields = {
            f["name"]: f for f in documentation("prices")["marketplace_guides"][ym.MARKETPLACE]["fields"]
        }
        self.assertIn("pay_price", fields)
        self.assertNotIn("club_discounted_price", fields)
        fields = {
            f["name"]: f for f in documentation("profit")["marketplace_guides"][ym.MARKETPLACE]["fields"]
        }
        self.assertEqual(fields["margin_missing_days"]["type"], "array")
        self.assertNotIn("buyout_amount", fields)

    def test_http_dispatches_yandex_and_denies_wb_only_permission(self):
        app = FastAPI()
        app.include_router(agent_analytics.router)

        async def verified(request: Request):
            request.state.user = object()
            return request.state.user

        app.dependency_overrides[agent_analytics.employee] = verified
        params = {"store": "gogol", "marketplace": ym.MARKETPLACE}
        with (
            patch.object(agent_full, "permitted", return_value=True),
            patch.object(ym.ym, "catalog", return_value=[{"article": "a"}]),
            patch.object(
                ym.yandex_source_values,
                "get_values",
                return_value={"a": {"purchase_price": 123, "source_sheet_id": "secret"}},
            ),
        ):
            response = TestClient(app).get("/api/agent/v1/costs", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["rows"][0]["purchase_price"], 123)
        self.assertNotIn("source_sheet_id", response.json()["rows"][0])
        with patch.object(agent_full, "permitted", side_effect=lambda u, s, *a: s == S.UNIT_ECONOMICS_WB):
            self.assertEqual(TestClient(app).get("/api/agent/v1/costs", params=params).status_code, 403)
