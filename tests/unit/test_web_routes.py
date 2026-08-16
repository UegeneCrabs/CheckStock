import json
import logging
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, decision_center, rnp_analytics
from app.dto.decision import DecisionAction, DecisionStatus
from app.dto.marketplace import Marketplace
from app.dto.rnp import RnpAction, RnpStrategy
from app.dto.system import ReadinessStatus
from app.main import create_app
from app.repositories import core
from app.stores import STORES
from app.wb import sales_funnel as wb_sales_funnel
from app.web import middleware
from app.web.routers import decision_center as decision_routes
from app.web.routers import rnp as rnp_routes
from app.web.routers import sales_overview, unit_economics

NOW = "2026-08-12T10:00:00+00:00"


class WebRouteUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_patch = mock.patch.object(
            core,
            "DB_PATH",
            Path(self.temporary_directory.name) / "web-unit.db",
        )
        self.database_patch.start()
        db.init_db()
        decision_center.init_schema()
        rnp_analytics.init_schema()
        db.seed_defaults()
        self.user = {
            "id": 1,
            "full_name": "Unit Admin",
            "login": "unit-admin",
            "role": "superadmin",
            "is_active": 1,
            "can_edit_stock": 1,
            "can_manage_users": 1,
            "store_slugs": list(STORES),
        }
        self.app = create_app()
        self.authentication_patch = mock.patch.object(
            self.app.state.container.identity,
            "user_for_token",
            return_value=self.user,
        )
        self.authentication_patch.start()
        logging.disable(logging.CRITICAL)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.cookies.set(middleware.auth.SESSION_COOKIE, "test-session")
        db.upsert_ff_stock("rimili", "949558341", "ФулСервис Подольск", 5, NOW, "WB")
        db.upsert_mp_stock("rimili", "949558341", "WB", "fbs", 3, NOW)
        db.replace_mp_warehouse_stock(
            "rimili",
            "WB",
            "fbo",
            [("949558341", "Коледино", "Москва", 2, NOW)],
        )
        self.operation_id = db.record_operation(
            "rimili",
            "delivery",
            "manual",
            [
                {
                    "article": "949558341",
                    "barcode": "2050292584830",
                    "name": "Ретро гирлянда",
                    "quantity": 5,
                }
            ],
            1,
            "Unit Admin",
            NOW,
        )

    def tearDown(self) -> None:
        self.client.close()
        logging.disable(logging.NOTSET)
        self.authentication_patch.stop()
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def test_html_pages_and_downloads_render(self) -> None:
        paths = (
            "/sales",
            "/sales/decision-center",
            "/sales/ephemerides",
            "/sales/rnp",
            "/sales/unit-economics",
            "/sales/unit-economics/wb-fbs",
            "/sales/unit-economics/ozon",
            "/sales/unit-economics/yandex-market",
            "/supply",
            "/stock",
            "/stock-2",
            "/stock-2/details/frozen",
            "/stock/rimili",
            "/stock/rimili/fbs",
            "/stock/rimili/warehouses",
            "/stock/rimili/operations",
            "/admin",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, f"{path}: {response.text[:500]}")
                self.assertTrue(response.content)

        root = self.client.get("/", follow_redirects=False)
        self.assertEqual(root.status_code, 303)
        self.assertEqual(root.headers["location"], "/sales")

        article = self.client.get(
            "/stock/rimili/article-detail",
            params={"mp": "WB", "article": "949558341"},
        )
        self.assertEqual(article.status_code, 200, article.text)
        self.assertIn("949558341", article.text)

        downloads = (
            "/stock/rimili/stock.xlsx?mp=WB",
            "/stock/rimili/warehouses/xlsx?mp=WB&scheme=fbo&warehouse=Коледино",
            "/stock/rimili/operations/xlsx",
            f"/admin/operations/{self.operation_id}/xlsx",
        )
        for path in downloads:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text[:500])
                self.assertTrue(response.content)

    def test_unit_economics_is_visible_in_sales_navigation(self) -> None:
        response = self.client.get("/sales")

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/sales/unit-economics', response.text)

    def test_yandex_stock_shows_combined_fby_and_fbs(self) -> None:
        db.replace_catalog(
            "rimili",
            "YANDEX MARKET",
            [{"article": "YA-1", "barcode": "123", "name": "Яндекс товар"}],
            NOW,
        )
        db.upsert_mp_stock("rimili", "YA-1", "YANDEX MARKET", "fbo", 19, NOW)
        db.upsert_mp_stock("rimili", "YA-1", "YANDEX MARKET", "fbs", 17, NOW)
        db.replace_mp_warehouse_stock(
            "rimili",
            "YANDEX MARKET",
            "fbs_149217490",
            [("YA-1", "Afflatus", None, 17, NOW)],
        )

        page = self.client.get("/stock/rimili", params={"mp": "YANDEX MARKET"})
        self.assertEqual(page.status_code, 200, page.text[:500])
        self.assertIn("FBY — склады Маркета", page.text)
        self.assertIn("FBS — склады продавца", page.text)
        self.assertIn('tot-grand">36</strong>', page.text)
        self.assertIn('tot-fbo">19</strong>', page.text)
        self.assertIn('tot-fbs">17</strong>', page.text)

        fbs = self.client.get("/stock/rimili/fbs", params={"mp": "YANDEX MARKET"})
        self.assertEqual(fbs.json(), {"fbs": {"YA-1": 17}})

        detail = self.client.get(
            "/stock/rimili/article-detail",
            params={"mp": "YANDEX MARKET", "article": "YA-1"},
        )
        fulfillment = next(
            row for row in detail.json()["warehouses"] if row["name"] == "AFFLATUS Купавна"
        )
        self.assertEqual(fulfillment["fbs"], 17)

        warehouses = self.client.get("/stock/rimili/warehouses", params={"mp": "YANDEX MARKET"})
        self.assertEqual(warehouses.status_code, 200, warehouses.text[:500])
        self.assertIn("Afflatus", warehouses.text)

    def test_page_and_download_errors_are_reported(self) -> None:
        cases = (
            ("/stock/unknown", 404),
            ("/stock/unknown/fbs", 404),
            ("/stock/unknown/warehouses", 404),
            ("/stock/unknown/operations", 404),
            ("/stock-2/details/unknown", 404),
            ("/admin/operations/999999/xlsx", 404),
            ("/stock/rimili/article-detail?mp=WB&article=missing", 200),
        )
        for path, status in cases:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, status)

    def test_sales_decision_and_rnp_api_contracts(self) -> None:
        dashboard = {
            "ok": True,
            "marketplace": "WB",
            "period": {},
            "summary": {},
            "opportunities": [],
            "products": [],
            "sync": [],
        }
        with mock.patch.object(decision_routes.decision_service, "dashboard", return_value=dashboard):
            response = self.client.get("/api/decision-center")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        with mock.patch.object(
            decision_routes.decision_service, "dashboard", side_effect=RuntimeError("broken")
        ):
            response = self.client.get("/api/decision-center")
        self.assertEqual(response.status_code, 500)

        with mock.patch.object(
            decision_routes.decision_service, "sync_many", return_value={"rimili": {"ok": True}}
        ):
            response = self.client.post("/api/decision-center/sync", json={"store": "rimili"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("rimili", response.json()["stores"])

        with mock.patch.object(
            self.app.state.container.decision_commands,
            "set_status",
            return_value=DecisionAction(
                fingerprint="rimili:test",
                status=DecisionStatus.COMPLETED,
                updated_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ):
            response = self.client.post(
                "/api/decision-center/status",
                json={"fingerprint": "rimili:test", "status": "completed"},
            )
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            self.app.state.container.decision_commands,
            "set_status",
            side_effect=ValueError("bad status"),
        ):
            response = self.client.post(
                "/api/decision-center/status",
                json={"fingerprint": "rimili:test", "status": "completed"},
            )
        self.assertEqual(response.status_code, 400)

        rnp_payload = {
            "ok": True,
            "marketplace": "WB",
            "store": "rimili",
            "items": [],
            "summary": {},
        }
        with mock.patch.object(rnp_routes.rnp_service, "dashboard", return_value=rnp_payload):
            response = self.client.get("/api/rnp?store=rimili&month=2026-08")
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(rnp_routes.rnp_service, "dashboard", side_effect=ValueError("bad")):
            response = self.client.get("/api/rnp?store=rimili&month=bad")
        self.assertEqual(response.status_code, 400)
        with mock.patch.object(rnp_routes.rnp_service, "dashboard", side_effect=RuntimeError("db")):
            response = self.client.get("/api/rnp?store=rimili&month=2026-08")
        self.assertEqual(response.status_code, 500)

        with (
            mock.patch.object(rnp_routes.rnp_service, "sales_lookback_days", return_value=8),
            mock.patch.object(rnp_routes.sales_service, "sync_store", return_value={"ok": True}),
            mock.patch.object(rnp_routes.rnp_service, "sync_metrics", return_value={"ok": True}),
        ):
            response = self.client.post(
                "/api/rnp/sync",
                json={"store": "rimili", "month": "2026-08", "marketplace": "WB"},
            )
        self.assertEqual(response.status_code, 200)

        with mock.patch.object(
            self.app.state.container.rnp_commands,
            "save_strategy",
            return_value=RnpStrategy(
                store_slug="rimili",
                marketplace=Marketplace.WB,
                article="949558341",
                strategy="growth",
                date_from="2026-08-01",
                date_to="2026-08-31",
                updated_by="Unit Admin",
                updated_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ):
            response = self.client.post(
                "/api/rnp/strategy",
                json={
                    "store": "rimili",
                    "marketplace": "WB",
                    "article": "949558341",
                    "strategy": "growth",
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                },
            )
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            self.app.state.container.rnp_commands,
            "add_action",
            return_value=RnpAction(
                id=1,
                article="949558341",
                action_date="2026-08-12",
                note="Done",
                user_name="Unit Admin",
                created_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ):
            response = self.client.post(
                "/api/rnp/action",
                json={
                    "store": "rimili",
                    "marketplace": "WB",
                    "article": "949558341",
                    "action_date": "2026-08-12",
                    "note": "Done",
                },
            )
        self.assertEqual(response.status_code, 200)

    def test_sales_and_unit_economics_api_contracts(self) -> None:
        with mock.patch.object(
            sales_overview.sales_service,
            "dashboard",
            return_value={"ok": True, "daily": [], "summary": {}},
        ):
            response = self.client.get(
                "/api/sales?store=rimili&marketplace=WB&date_from=2026-08-01&date_to=2026-08-12"
            )
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            wb_sales_funnel,
            "dashboard",
            return_value={"ok": True, "marketplace": "WB", "products": [], "totals": {}},
        ):
            response = self.client.get(
                "/api/sales/wb-funnel?store=rimili&date_from=2026-08-01&date_to=2026-08-12"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/sales/wb-funnel").status_code, 400)
        with mock.patch.object(wb_sales_funnel, "dashboard", side_effect=ValueError("bad period")):
            self.assertEqual(self.client.get("/api/sales/wb-funnel?store=rimili").status_code, 400)
        with mock.patch.object(
            sales_overview.sales_service, "dashboard", side_effect=ValueError("bad period")
        ):
            response = self.client.get("/api/sales?store=rimili")
        self.assertEqual(response.status_code, 400)
        with mock.patch.object(sales_overview.sales_service, "dashboard", side_effect=RuntimeError("db")):
            response = self.client.get("/api/sales?store=rimili")
        self.assertEqual(response.status_code, 500)

        workbook = b"PK\x03\x04unit"
        with mock.patch.object(sales_overview.sales_service, "export_xlsx", return_value=workbook):
            response = self.client.get(
                "/sales/orders.xlsx?store=rimili&marketplace=WB&date_from=2026-08-01&date_to=2026-08-12"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, workbook)
        with mock.patch.object(
            sales_overview.sales_service, "export_xlsx", side_effect=ValueError("bad period")
        ):
            response = self.client.get("/sales/orders.xlsx?store=rimili")
        self.assertEqual(response.status_code, 400)

        with mock.patch.object(
            unit_economics.wb_unit_economics,
            "load_wb_fbs_data",
            return_value={"ok": True, "items": []},
        ):
            response = self.client.post("/sales/unit-economics/wb-fbs/calculate", json={"store": "rimili"})
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            unit_economics.wb_unit_economics,
            "load_wb_fbs_data",
            side_effect=RuntimeError("api"),
        ):
            response = self.client.post("/sales/unit-economics/wb-fbs/calculate", json={"store": "rimili"})
        self.assertEqual(response.status_code, 502)

        rates = [
            {"name": name, "storage": 1, "accept": 2, "fulfillment": 3} for name in db.get_fulfillments()
        ]
        response = self.client.post("/sales/unit-economics/wb-fbs/fulfillment-rates", json={"rates": rates})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post("/sales/unit-economics/wb-fbs/fulfillment-rates", json={"rates": "bad"})
        self.assertEqual(response.status_code, 422)

    def test_authorization_and_system_failures(self) -> None:
        self.authentication_patch.stop()
        anonymous = mock.patch.object(self.app.state.container.identity, "user_for_token", return_value=None)
        anonymous.start()
        try:
            html_response = self.client.get("/stock", follow_redirects=False)
            json_response = self.client.get("/api/sales", headers={"accept": "application/json"})
            mutation_response = self.client.post("/api/rnp/sync", json={})
        finally:
            anonymous.stop()
            self.authentication_patch.start()
        self.assertEqual(html_response.status_code, 303)
        self.assertEqual(json_response.status_code, 401)
        self.assertEqual(mutation_response.status_code, 401)

        with mock.patch.object(
            self.app.state.container.health,
            "readiness",
            return_value=ReadinessStatus(status="unavailable", database="unavailable"),
        ):
            response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_invalid_json_and_forbidden_store_paths(self) -> None:
        limited_user = dict(self.user, role="user", store_slugs=["tris"])
        with mock.patch.object(
            self.app.state.container.identity, "user_for_token", return_value=limited_user
        ):
            self.assertEqual(self.client.get("/api/decision-center?store=rimili").status_code, 403)
            self.assertEqual(
                self.client.post("/api/decision-center/sync", json={"store": "rimili"}).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    "/api/decision-center/status",
                    json={"fingerprint": "rimili:test", "status": "completed"},
                ).status_code,
                403,
            )
            self.assertEqual(self.client.get("/api/sales?store=rimili").status_code, 403)
            self.assertEqual(self.client.get("/api/sales/wb-funnel?store=rimili").status_code, 403)
            self.assertEqual(self.client.get("/api/rnp?store=rimili").status_code, 403)
            self.assertEqual(
                self.client.post(
                    "/api/rnp/sync",
                    json={"store": "rimili", "marketplace": "WB", "month": "2026-08"},
                ).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    "/sales/unit-economics/wb-fbs/calculate", json={"store": "rimili"}
                ).status_code,
                403,
            )

        bad_body = "[not-json"
        headers = {"content-type": "application/json"}
        self.assertEqual(
            self.client.post("/api/rnp/strategy", content=bad_body, headers=headers).status_code,
            422,
        )
        self.assertEqual(
            self.client.post("/api/rnp/action", content=json.dumps([]), headers=headers).status_code,
            422,
        )
        self.assertEqual(
            self.client.post("/sales/unit-economics/wb-fbs/calculate", json={"store": "missing"}).status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
