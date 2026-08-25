import io
import json
import logging
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest import mock

import openpyxl
from fastapi.testclient import TestClient

from app import auth, db, decision_center, rnp_analytics
from app.dto.decision import DecisionAction, DecisionStatus
from app.main import create_app
from app.repositories import core
from app.stores import STORES
from app.web.routers import admin, sales_overview
from app.web.routers import decision_center as decision_routes
from app.web.routers import rnp as rnp_routes


class HttpServiceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)
        self.temp = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp.name) / "integration-http.sqlite3"
        self.path_patch = mock.patch.object(core, "DB_PATH", self.database_path)
        self.path_patch.start()
        db.init_db()
        db.seed_defaults()
        decision_center.init_schema()
        rnp_analytics._SCHEMA_READY = False
        rnp_analytics.init_schema()
        self.admin_id = db.create_user(
            "Integration Admin",
            "integration@test",
            "integration-admin",
            auth.hash_password("integration-password"),
            "superadmin",
            "2026-08-12T10:00:00+00:00",
            list(STORES),
        )
        for marketplace in db.MARKETPLACES:
            db.replace_catalog(
                "rimili",
                marketplace,
                [
                    {
                        "article": "A-1",
                        "barcode": "460000000001",
                        "name": "Integration Product",
                        "mp_sku": "1001",
                        "mp_product_id": "P-1",
                        "image_url": "",
                    }
                ],
                "2026-08-12T10:00:00+00:00",
            )
        self.client = TestClient(create_app(), raise_server_exceptions=False)
        response = self.client.post(
            "/login",
            data={"login": "integration-admin", "password": "integration-password"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303, response.text)

    def tearDown(self) -> None:
        self.client.close()
        self.path_patch.stop()
        self.temp.cleanup()
        rnp_analytics._SCHEMA_READY = False
        logging.disable(logging.NOTSET)

    def test_system_and_auth_good_and_bad_outcomes(self) -> None:
        anonymous = TestClient(create_app(), raise_server_exceptions=False)
        try:
            self.assertEqual(anonymous.get("/healthz").json(), {"status": "ok"})
            self.assertEqual(anonymous.get("/readyz").json(), {"status": "ok", "database": "ok"})
            self.assertEqual(anonymous.get("/sales", follow_redirects=False).status_code, 303)
            self.assertEqual(
                anonymous.get("/api/sales", headers={"accept": "application/json"}).status_code, 401
            )
            self.assertEqual(
                anonymous.post(
                    "/login", data={"login": "integration-admin", "password": "wrong"}
                ).status_code,
                401,
            )
        finally:
            anonymous.close()
        self.assertEqual(self.client.get("/sales").status_code, 200)
        self.assertEqual(self.client.get("/logout", follow_redirects=False).status_code, 303)

    def test_read_pages_and_downloads_good_and_bad_outcomes(self) -> None:
        operation_id = db.record_operation(
            "rimili",
            "delivery",
            "manual",
            [{"article": "A-1", "barcode": "460000000001", "name": "Product", "quantity": 2}],
            self.admin_id,
            "Integration Admin",
            "2026-08-12T10:00:00+00:00",
        )
        pages = (
            "/",
            "/sales",
            "/sales/decision-center",
            "/sales/ephemerides",
            "/sales/rnp",
            "/sales/unit-economics-1c",
            "/sales/unit-economics-1c/cabinet-settings",
            "/sales/unit-economics-1c/ozon",
            "/sales/unit-economics-1c/yandex-market",
            "/supply",
            "/stock",
            "/stock/total",
            "/stock/randomizer",
            "/stock/planning/wb",
            "/stock-2",
            "/stock-2/details/zero",
            "/stock/rimili",
            "/stock/rimili/fbs",
            "/stock/rimili/warehouses",
            "/stock/rimili/operations",
            "/admin",
        )
        for path in pages:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        downloads = (
            "/stock/total.xlsx",
            f"/admin/operations/{operation_id}/xlsx",
            "/stock/rimili/operations/xlsx",
            "/stock/rimili/warehouses/xlsx",
            "/stock/rimili/stock.xlsx",
        )
        for path in downloads:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text[:300])
                self.assertTrue(response.content.startswith(b"PK"))
        self.assertEqual(self.client.get("/stock/unknown").status_code, 404)
        self.assertEqual(self.client.get("/admin/operations/999999/xlsx").status_code, 404)
        self.assertEqual(self.client.get("/stock-2/details/wrong").status_code, 404)

    def test_sales_decision_and_rnp_good_and_bad_outcomes(self) -> None:
        today = date.today().isoformat()
        with mock.patch.object(
            sales_overview.sales_service,
            "dashboard",
            return_value={"marketplace": "WB", "series": [], "totals": {}},
        ):
            response = self.client.get(
                "/api/sales",
                params={"date_from": today, "date_to": today, "marketplace": "WB", "store": "rimili"},
            )
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            sales_overview.sales_service, "dashboard", side_effect=ValueError("bad period")
        ):
            self.assertEqual(self.client.get("/api/sales", params={"store": "rimili"}).status_code, 400)

        with mock.patch.object(
            decision_routes.decision_service,
            "dashboard",
            return_value={"meta": {}, "summary": {}, "opportunities": []},
        ):
            self.assertEqual(
                self.client.get("/api/decision-center", params={"store": "rimili"}).status_code, 200
            )
        with mock.patch.object(
            decision_routes.decision_service, "dashboard", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(self.client.get("/api/decision-center").status_code, 500)
        with mock.patch.object(
            decision_routes.decision_service, "sync_many", return_value={"rimili": {"ok": True}}
        ):
            self.assertEqual(
                self.client.post("/api/decision-center/sync", json={"store": "rimili"}).status_code,
                200,
            )
        with mock.patch.object(
            self.client.app.state.container.decision_commands,
            "set_status",
            return_value=DecisionAction(
                fingerprint="rimili:1:stockout",
                status=DecisionStatus.COMPLETED,
                updated_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ):
            self.assertEqual(
                self.client.post(
                    "/api/decision-center/status",
                    json={"fingerprint": "rimili:1:stockout", "status": "completed"},
                ).status_code,
                200,
            )
        self.assertEqual(
            self.client.post(
                "/api/decision-center/status",
                json={"fingerprint": "unknown:1:x", "status": "completed"},
            ).status_code,
            403,
        )

        with mock.patch.object(
            rnp_routes.rnp_service,
            "dashboard",
            return_value={"products": [], "totals": {}, "pagination": {}},
        ):
            self.assertEqual(
                self.client.get(
                    "/api/rnp", params={"month": today[:7], "marketplace": "WB", "store": "rimili"}
                ).status_code,
                200,
            )
        with mock.patch.object(rnp_routes.rnp_service, "dashboard", side_effect=ValueError("bad month")):
            self.assertEqual(self.client.get("/api/rnp", params={"store": "rimili"}).status_code, 400)
        strategy = {
            "store": "rimili",
            "marketplace": "WB",
            "article": "A-1",
            "strategy": "growth",
            "date_from": today,
            "date_to": today,
        }
        self.assertEqual(self.client.post("/api/rnp/strategy", json=strategy).status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/rnp/action",
                json={
                    "store": "rimili",
                    "marketplace": "WB",
                    "article": "A-1",
                    "note": "Checked",
                    "action_date": today,
                },
            ).status_code,
            200,
        )
        self.assertEqual(self.client.post("/api/rnp/strategy", content=b"[]").status_code, 422)

    def test_stock_query_and_mutation_good_and_bad_outcomes(self) -> None:
        search = self.client.get("/stock/rimili/catalog-search", params={"q": "A-1"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["items"][0]["article"], "A-1")
        self.assertEqual(self.client.get("/stock/unknown/catalog-search").status_code, 404)

        added = self.client.post(
            "/stock/rimili/add-ff-items",
            json={
                "fulfillment": "FF One",
                "marketplace": "WB",
                "items": [{"code": "A-1", "quantity": 5}],
            },
        )
        self.assertEqual(added.status_code, 200, added.text)
        cell = self.client.get("/stock/rimili/ff-cell", params={"ff": "FF One", "mp": "WB"})
        self.assertEqual(cell.json()["stock"]["A-1"], 5)
        self.assertEqual(
            self.client.post(
                "/stock/rimili/add-ff-items",
                json={
                    "fulfillment": "FF One",
                    "marketplace": "WB",
                    "items": [{"code": "missing", "quantity": 1}],
                },
            ).status_code,
            400,
        )

        transferred = self.client.post(
            "/stock/rimili/transfer",
            data={
                "from_fulfillment": "FF One",
                "from_marketplace": "WB",
                "to_fulfillment": "FF Two",
                "to_marketplace": "WB",
                "items": json.dumps([{"code": "A-1", "quantity": 3}]),
            },
        )
        self.assertEqual(transferred.status_code, 200, transferred.text)
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "FF One", "WB"), 2)
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "FF Two", "WB"), 3)
        self.assertEqual(
            self.client.post(
                "/stock/rimili/transfer",
                data={
                    "from_fulfillment": "FF One",
                    "from_marketplace": "WB",
                    "to_fulfillment": "FF Two",
                    "to_marketplace": "WB",
                    "items": "{bad",
                },
            ).status_code,
            400,
        )

        shipped = self.client.post(
            "/stock/rimili/shipment",
            data={
                "fulfillment": "FF Two",
                "marketplace": "WB",
                "items": json.dumps([{"code": "A-1", "quantity": 2}]),
                "note": "Integration shipment",
            },
        )
        self.assertEqual(shipped.status_code, 200, shipped.text)
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "FF Two", "WB"), 1)
        self.assertEqual(
            self.client.post(
                "/stock/rimili/shipment",
                data={
                    "fulfillment": "FF Two",
                    "marketplace": "WB",
                    "items": json.dumps([{"code": "A-1", "quantity": 99}]),
                },
            ).status_code,
            400,
        )

    def test_file_import_and_operation_history_good_and_bad_outcomes(self) -> None:
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["BARCODE", "ARTICLE", "QTY"])
        sheet.append(["460000000001", "A-1", 4])
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        response = self.client.post(
            "/stock/rimili/upload-ff-stock",
            data={"fulfillment": "Imported FF", "marketplace": "WB"},
            files={
                "file": (
                    "delivery.xlsx",
                    output.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "Imported FF", "WB"), 4)
        duplicate = self.client.post(
            "/stock/rimili/upload-ff-stock",
            data={"fulfillment": "Imported FF", "marketplace": "WB"},
            files={
                "file": (
                    "delivery.xlsx",
                    output.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertEqual(duplicate.json()["report"]["added_quantity"], 0)
        self.assertEqual(len(duplicate.json()["report"]["unchanged"]), 1)
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "Imported FF", "WB"), 4)

        changed_workbook = openpyxl.Workbook()
        changed_sheet = changed_workbook.active
        changed_sheet.append(["BARCODE", "ARTICLE", "QTY"])
        changed_sheet.append(["460000000001", "A-1", 7])
        changed_output = io.BytesIO()
        changed_workbook.save(changed_output)
        changed_workbook.close()
        changed = self.client.post(
            "/stock/rimili/upload-ff-stock",
            data={"fulfillment": "Imported FF", "marketplace": "WB"},
            files={
                "file": (
                    "delivery.xlsx",
                    changed_output.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["report"]["added_quantity"], 3)
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "Imported FF", "WB"), 7)
        operations = self.client.get("/stock/rimili/operations")
        self.assertEqual(operations.status_code, 200)
        self.assertIn("delivery.xlsx", operations.text)

    def test_admin_good_and_bad_outcomes(self) -> None:
        created = self.client.post(
            "/admin/users",
            data={
                "full_name": "Employee",
                "google_email": "employee@test",
                "login": "employee",
                "password": "employee-password",
                "role": "user",
                "stores": "rimili",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        target = db.get_user_by_login("employee")
        self.assertEqual(self.client.post(f"/admin/users/{target['id']}/toggle-stock-edit").status_code, 200)
        self.assertEqual(
            self.client.post(
                f"/admin/users/{target['id']}/reset-password", data={"password": "new-password"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"/admin/users/{target['id']}/stores", data={"stores": "rimili"}).status_code,
            200,
        )
        self.assertEqual(self.client.post(f"/admin/users/{target['id']}/toggle-active").status_code, 200)
        self.assertEqual(self.client.post(f"/admin/users/{target['id']}/delete").status_code, 200)
        self.assertEqual(self.client.post(f"/admin/users/{self.admin_id}/delete").status_code, 400)

        with (
            mock.patch.object(admin.wb_catalog, "sync_all", return_value={"rimili": {"ok": True}}),
            mock.patch.object(admin.wb_sync, "sync_all", return_value={"rimili": {"token": True}}),
            mock.patch.object(admin.ozon_catalog, "sync_all", return_value={}),
            mock.patch.object(admin.ozon_sync, "sync_all", return_value={}),
            mock.patch.object(admin.ya_catalog, "sync_all", return_value={}),
            mock.patch.object(admin.ya_sync, "sync_all", return_value={}),
        ):
            self.assertEqual(self.client.post("/admin/sync-stock").status_code, 200)

        limited_id = db.create_user(
            "Limited",
            "limited@test",
            "limited",
            auth.hash_password("limited-password"),
            "user",
            "2026-08-12T10:00:00+00:00",
            ["rimili"],
        )
        limited_client = TestClient(create_app(), raise_server_exceptions=False)
        try:
            self.assertEqual(
                limited_client.post(
                    "/login",
                    data={"login": "limited", "password": "limited-password"},
                    follow_redirects=False,
                ).status_code,
                303,
            )
            self.assertEqual(limited_client.get("/admin").status_code, 403)
            self.assertEqual(limited_client.post("/admin/users").status_code, 403)
        finally:
            limited_client.close()
        self.assertIsNotNone(db.get_user(limited_id))

    def test_all_openapi_services_have_integration_ownership(self) -> None:
        schema_paths = set(create_app().openapi()["paths"])
        owned = {
            "/healthz",
            "/readyz",
            "/",
            "/login",
            "/logout",
            "/profile",
            "/access-denied",
            "/api/activity/heartbeat",
            "/sales",
            "/sales/ephemerides",
            "/sales/orders.xlsx",
            "/api/sales",
            "/api/sales/wb-funnel-orders",
            "/sales/decision-center",
            "/api/decision-center",
            "/api/decision-center/sync",
            "/api/decision-center/status",
            "/sales/rnp",
            "/api/rnp",
            "/api/rnp/sync",
            "/api/rnp/strategy",
            "/api/rnp/action",
            "/sales/unit-economics-1c",
            "/sales/unit-economics-1c/cabinet-settings",
            "/api/unit-economics-1c/cabinet-settings",
            "/api/unit-economics-1c/cabinet-settings/{store_slug}",
            "/api/unit-economics-1c/source-data/sync",
            "/api/unit-economics-1c/prices",
            "/api/unit-economics-1c/prices/preview",
            "/api/unit-economics-1c/prices/sync",
            "/api/unit-economics-1c/sync",
            "/api/unit-economics-1c/product-settings/{store_slug}",
            "/sales/unit-economics-1c/ozon",
            "/sales/unit-economics-1c/yandex-market",
            "/supply",
            "/stock",
            "/stock/total",
            "/stock/total.xlsx",
            "/stock/cost-report",
            "/stock/cost-report.xlsx",
            "/stock/cost-report/operations/{operation_id}/fbs-transfer",
            "/stock/supplies",
            "/stock/randomizer",
            "/stock/randomizer/generate",
            "/stock/planning/wb",
            "/stock/planning/manual",
            "/stock/planning/manual/{supply_id}",
            "/stock/planning/manual/{supply_id}/ready",
            "/stock-2",
            "/stock-2/details/{kind}",
            "/stock/{slug}",
            "/stock/{slug}/fbs",
            "/stock/{slug}/warehouses",
            "/stock/{slug}/warehouses/xlsx",
            "/stock/{slug}/operations",
            "/stock/{slug}/operations/xlsx",
            "/stock/{slug}/stock.xlsx",
            "/stock/{slug}/catalog-search",
            "/stock/{slug}/ff-cell",
            "/stock/{slug}/ff-available",
            "/stock/{slug}/article-detail",
            "/stock/{slug}/add-ff-items",
            "/stock/{slug}/upload-ff-stock",
            "/stock/{slug}/transfer",
            "/stock/{slug}/shipment",
            "/stock/{slug}/trash/checked",
            "/admin",
            "/admin/activity",
            "/admin/activity/data",
            "/admin/users",
            "/admin/users/{user_id}/delete",
            "/admin/users/{user_id}/reset-password",
            "/admin/users/{user_id}/toggle-stock-edit",
            "/admin/users/{user_id}/toggle-active",
            "/admin/users/{user_id}/stores",
            "/admin/users/{user_id}/role",
            "/admin/users/{user_id}/sections",
            "/admin/operations/{operation_id}/xlsx",
            "/admin/sync-stock",
            "/admin/google-export",
            "/admin/google-export/{store_slug}",
            "/admin/google-export/{store_slug}/run",
        }
        self.assertEqual(schema_paths, owned)

    def test_profile_shows_current_user_access(self) -> None:
        response = self.client.get("/profile")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Integration Admin", response.text)
        self.assertIn("integration@test", response.text)
        self.assertIn("integration-admin", response.text)
        self.assertIn("Сток · Аналитика остатков", response.text)
        self.assertIn('href="/profile"', response.text)


if __name__ == "__main__":
    unittest.main()
