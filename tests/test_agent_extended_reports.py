import asyncio
import json
import sqlite3
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.agents import extended_reports as extra
from app.dto.inbound_supplies import InboundItem, InboundSnapshot, InboundSupply
from app.web.routers import agent_analytics, agent_full, agent_mcp


class ExtendedReportsTests(unittest.TestCase):
    def test_authenticated_http_and_mcp_share_new_route(self):
        app = FastAPI()
        app.include_router(agent_analytics.router)

        async def verified(request: Request):
            request.state.user = object()
            return request.state.user

        app.dependency_overrides[agent_analytics.employee] = verified
        rows = [{"article": "a", "day": "2026-01-01", "quantity": 0}]
        args = {"store": "gogol", "date_from": "2026-01-01", "date_to": "2026-01-02"}
        with (
            patch.object(agent_full, "permitted", return_value=True),
            patch.object(extra, "stock_history", return_value=rows),
        ):
            response = TestClient(app).get("/api/agent/v1/stock-history", params=args)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["rows"], rows)
            result = asyncio.run(
                agent_mcp.call_tool(self.request(), object(), "/api/agent/v1/stock-history", args)
            )
            self.assertFalse(result["isError"])
            self.assertEqual(json.loads(result["content"][0]["text"])["rows"], rows)

    def test_cost_summary_keeps_coverage_without_unpaginated_items(self):
        formula = {"available": False, "items": [{"article": "a"}]}
        data = {"summary": [{"store_slug": "gogol", "fbs_formula": formula}], "reconciliation": [formula]}
        with (
            patch("app.stock.cost_report.build_report", return_value=data),
            patch.object(extra, "scope_pairs", return_value=(("gogol", "WB"),)),
        ):
            result = self.run_extra(
                "stock-cost-report", self.query(date_from="2026-01-01", date_to="2026-01-02")
            )
        self.assertNotIn("items", result["rows"][0]["fbs_formula"])
        self.assertEqual(result["context"]["reconciliation"], [{"available": False}])

    def query(self, **values):
        return agent_full.ReportQuery(store="gogol", **values)

    def request(self, query=None):
        app = FastAPI()
        app.state.container = SimpleNamespace()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [],
                "query_string": (query or "").encode(),
                "app": app,
            }
        )
        request.state.user = object()
        return request

    def run_extra(self, name, query, request=None):
        return asyncio.run(extra.execute_extra(name, request or self.request(), object(), query))

    def test_schema_and_mcp_include_all_six_methods(self):
        schema = asyncio.run(agent_analytics.action_schema())
        tools = asyncio.run(agent_mcp.catalog())
        self.assertEqual(len(schema["paths"]), 30)
        for name in extra.EXTRA_SPECS:
            op = schema["paths"]["/api/agent/v1/" + name]["get"]
            self.assertIn(op["operationId"], tools)
            fields = {p["name"] for p in op["parameters"]}
            self.assertEqual(fields, agent_full.allowed_fields(name))
        for name in ("orders", "stock-history", "stock-cost-report"):
            params = schema["paths"]["/api/agent/v1/" + name]["get"]["parameters"]
            self.assertTrue(all(p["required"] for p in params if p["name"] in {"date_from", "date_to"}))
        # MCP derives its tool registry from this same schema.
        self.assertTrue(
            all(
                op["get"]["operationId"].startswith("getAnalytics")
                for path, op in schema["paths"].items()
                if path.rsplit("/", 1)[-1] in extra.EXTRA_SPECS
            )
        )

    def test_authentication_required_over_http(self):
        app = FastAPI()
        app.include_router(agent_analytics.router)
        client = TestClient(app)
        for name in extra.EXTRA_SPECS:
            response = client.get("/api/agent/v1/" + name)
            self.assertEqual(response.status_code, 401)

    def test_validation_dates_platform_and_unsupported_filters(self):
        bad = [
            ("orders", self.query(), {}),
            ("orders", self.query(date_from="2026-01-01", date_to="2026-04-01"), {}),
            ("orders", self.query(marketplace="OZON"), {}),
            ("economics-history", self.query(), {}),
            ("inbound-supplies", self.query(), {"date_from": "2026-01-01"}),
        ]
        for name, query, supplied in bad:
            with self.assertRaises(HTTPException) as raised:
                agent_full.validate(name, query, supplied)
            self.assertEqual(raised.exception.status_code, 422)

    def test_extra_access_rules(self):
        with (
            patch.object(agent_full, "permitted", return_value=True),
            patch.object(agent_full, "scope_pairs", return_value=(("gogol", "WB"),)),
        ):
            self.assertFalse(agent_full.report_permitted(object(), "supply-arrivals", "gogol", "WB"))
        with patch.object(agent_full, "permitted", side_effect=[True, False]):
            self.assertFalse(agent_full.report_permitted(object(), "stock-cost-report", "gogol", "WB"))
        with (
            patch.object(agent_full, "guard", side_effect=HTTPException(403)),
            patch.object(extra, "execute_extra", new_callable=AsyncMock) as load,
        ):
            with self.assertRaises(HTTPException):
                asyncio.run(agent_full.execute("inbound-supplies", self.request(), object(), self.query()))
            load.assert_not_called()

    def test_stock_history_scope_dates_nulls_and_pagination(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE marketplace_stock_daily_history (store_slug, marketplace, article, day, scheme, quantity, captured_at)"
        )
        conn.execute(
            "CREATE TABLE fulfillment_stock_daily_history (store_slug, marketplace, article, day, fulfillment, quantity, captured_at)"
        )
        conn.executemany(
            "INSERT INTO marketplace_stock_daily_history VALUES (?,?,?,?,?,?,?)",
            [
                ("gogol", "WB", "a", "2026-01-01", "fbo", 0, "time"),
                ("gogol", "WB", "a", "2026-01-02", "fbo", 5, "time"),
                ("other", "WB", "a", "2026-01-01", "fbo", 999, "time"),
                ("gogol", "OZON", "a", "2026-01-01", "fbo", 999, "time"),
                ("gogol", "WB", "a", "2025-12-31", "fbo", 999, "time"),
            ],
        )

        def read(sql, params=()):
            return [dict(r) for r in conn.execute(sql, params)]

        with patch.object(extra.reports, "read", side_effect=read):
            rows = extra.stock_history(self.query(date_from="2026-01-01", date_to="2026-01-02"))
        self.assertEqual([r["quantity"] for r in rows], [0, 5])
        with patch.object(extra, "stock_history", return_value=rows):
            result = self.run_extra(
                "stock-history", self.query(date_from="2026-01-01", date_to="2026-01-02", limit=1)
            )
        self.assertEqual(result["total_rows"], 2)
        self.assertEqual(result["next_offset"], 1)
        self.assertEqual(result["totals"], {})
        self.assertEqual(result["rows"][0]["day"], "2026-01-02")

    def test_orders_manager_filter_and_no_raw_payload_query(self):
        rows = [
            {"article": "a", "external_order_id": "one", "status": "sold"},
            {"article": "b", "external_order_id": "one", "status": "sold"},
        ]
        with (
            patch.object(extra.reports, "read", return_value=rows) as read,
            patch.object(extra.reports, "economic_filter", return_value=rows[:1]),
        ):
            result = extra.orders(
                self.query(date_from="2026-01-01", date_to="2026-01-02", order_id="one", status="sold"),
                object(),
            )
        self.assertEqual(len(result), 1)
        self.assertNotIn("raw_json", read.call_args.args[0])
        self.assertEqual(read.call_args.args[1], ("gogol", "WB", "2026-01-01", "2026-01-03"))

    def test_inbound_item_filter_and_unknown_quantity(self):
        request = self.request()
        snapshot = InboundSnapshot(
            store_slug="gogol",
            marketplace="WB",
            last_success=datetime.now(UTC).isoformat(),
            supplies=(
                InboundSupply(
                    key="s",
                    supply_id="s",
                    status="1",
                    status_label="Planned",
                    stage="planned",
                    items=(
                        InboundItem(article="a", quantity=5, accepted_quantity=None),
                        InboundItem(article="b", quantity=6),
                    ),
                ),
            ),
        )
        request.app.state.container.inbound_supplies = SimpleNamespace(report=lambda targets: [snapshot])
        result = self.run_extra("inbound-supplies", self.query(article="a", status="planned"), request)
        self.assertEqual(result["total_rows"], 1)
        self.assertIsNone(result["rows"][0]["accepted_quantity"])
        self.assertFalse(result["sources"][0]["stale"])

    def test_arrivals_store_scope_and_actual_date_filter(self):
        data = {
            "rows": [
                {"row": 1, "store_slug": "gogol", "arrival": "2026-01-01"},
                {"row": 2, "store_slug": "other", "arrival": "2026-01-01"},
                {"row": 3, "store_slug": "gogol", "arrival": None},
            ],
            "stale": True,
        }
        with patch("app.stock.supply_arrivals.report", return_value=data):
            result = self.run_extra(
                "supply-arrivals", self.query(date_from="2026-01-01", date_to="2026-01-02")
            )
        self.assertEqual([r["row"] for r in result["rows"]], [1])
        self.assertEqual(result["context"]["marketplace_scope"], "shared_store_register")

    def test_cost_detail_hides_cross_marketplace_and_employee(self):
        operations = [
            {
                "id": 1,
                "kind": "transfer",
                "from_marketplace": "WB",
                "to_marketplace": "OZON",
                "items": [{"article": "secret"}],
            },
            {
                "id": 2,
                "kind": "transfer",
                "from_marketplace": "WB",
                "to_marketplace": "WB",
                "employee_name": "private",
                "items": [{"article": "a", "quantity": 2, "purchase_cost": None}],
            },
        ]
        with (
            patch("app.stock.cost_report.build_report", return_value={"operations": operations}),
            patch.object(extra, "scope_pairs", return_value=(("gogol", "WB"),)),
        ):
            rows, _ = extra.cost_report(
                self.query(date_from="2026-01-01", date_to="2026-01-02", cost_view="transfers"), object()
            )
        self.assertEqual(len(rows), 1)
        self.assertNotIn("employee_name", rows[0])
        self.assertIsNone(rows[0]["purchase_cost"])

    def test_economics_history_preserves_website_values_and_missing_data(self):
        product = {
            "store_slug": "gogol",
            "article": "a",
            "history": [{"date": "2026-01-01", "margin_rub": None, "margin_complete": False, "fbo_units": 0}],
        }
        with (
            patch.object(extra.reports, "economic_filter", return_value=[{"article": "a"}]),
            patch(
                "app.web.routers.unit_economics.sales_unit_economics_1c",
                new_callable=AsyncMock,
                return_value=JSONResponse({"product": product}),
            ),
        ):
            result = self.run_extra("economics-history", self.query(article="a"))
        self.assertIsNone(result["rows"][0]["margin_rub"])
        self.assertEqual(result["rows"][0]["fbo_units"], 0)
        self.assertFalse(result["rows"][0]["margin_complete"])


if __name__ == "__main__":
    unittest.main()
