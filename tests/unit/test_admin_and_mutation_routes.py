import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, decision_center, rnp_analytics
from app.dto.identity import ActivityEntry, ActivityLog, Role, User, UserCollection
from app.dto.stock import (
    AddedFulfillmentItem,
    AddedFulfillmentItems,
    StockMovementItem,
    StockMovementItems,
    TransferResult,
)
from app.main import create_app
from app.repositories import core
from app.stores import STORES
from app.web import middleware
from app.web.routers import admin, stock_mutations


class AdminAndMutationRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(core, "DB_PATH", Path(self.temp.name) / "routes.sqlite3")
        self.path_patch.start()
        db.init_db()
        decision_center.init_schema()
        rnp_analytics._SCHEMA_READY = False
        rnp_analytics.init_schema()
        db.seed_defaults()
        self.user = User(
            id=1,
            full_name="Admin",
            google_email="admin@test",
            login="admin",
            role=Role.SUPERADMIN,
            is_active=True,
            can_edit_stock=True,
            can_manage_users=True,
            created_at="2026-08-12T10:00:00+00:00",
            store_slugs=tuple(STORES),
        )
        self.app = create_app()
        self.auth_patch = mock.patch.object(
            self.app.state.container.identity, "user_for_token", return_value=self.user
        )
        self.auth_patch.start()
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.cookies.set(middleware.auth.SESSION_COOKIE, "test-session")

    def tearDown(self) -> None:
        self.client.close()
        self.auth_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()
        rnp_analytics._SCHEMA_READY = False
        logging.disable(logging.NOTSET)

    def create_target(self, login: str = "target", role: str = "user", active: bool = True) -> int:
        user_id = db.create_user(
            "Target",
            f"{login}@test",
            login,
            "hash",
            role,
            "2026-08-12T10:00:00+00:00",
            ["rimili"],
        )
        if not active:
            db.set_user_active(user_id, False)
        return user_id

    def test_mutation_helpers_and_read_endpoints(self) -> None:
        self.assertIsNone(stock_mutations._guard_stock_edit(self.user))
        with mock.patch.object(stock_mutations.auth, "can_edit_stock", return_value=False):
            self.assertIsNotNone(stock_mutations._guard_stock_edit({}))
        self.assertEqual(stock_mutations._source_of(None, None, "url"), ("sheet", None))
        self.assertEqual(stock_mutations._source_of(None, None, ""), ("manual", None))
        upload = mock.Mock(filename="file.xlsx")
        self.assertEqual(stock_mutations._source_of(upload, b"x", ""), ("file", "file.xlsx"))
        with (
            mock.patch.object(stock_mutations.db, "source_fingerprint", return_value="fp"),
            mock.patch.object(stock_mutations.db, "find_used_source", return_value=None),
        ):
            self.assertEqual(
                stock_mutations._guard_used_source("s", "k", "file", "", b"x", "L"), ("fp", None)
            )
        with (
            mock.patch.object(stock_mutations.db, "source_fingerprint", return_value="fp"),
            mock.patch.object(
                stock_mutations.db,
                "find_used_source",
                return_value={"created_at": "2026-01-01", "user_name": "User", "label": "Old"},
            ),
        ):
            self.assertIsNotNone(stock_mutations._guard_used_source("s", "k", "file", "", b"x", "New")[1])

        with mock.patch.object(stock_mutations.db, "search_catalog", return_value=[{"article": "A"}]):
            response = self.client.get("/stock/rimili/catalog-search", params={"q": "A"})
        self.assertEqual(response.json()["items"][0]["article"], "A")
        self.assertEqual(self.client.get("/stock/unknown/catalog-search").status_code, 404)
        self.assertEqual(self.client.get("/stock/rimili/ff-cell").json(), {"stock": {}})
        with mock.patch.object(stock_mutations.db, "get_ff_available_totals", return_value={"A": 2}):
            response = self.client.get("/stock/rimili/ff-cell", params={"ff": "FF", "mp": "WB"})
        self.assertEqual(response.json()["stock"], {"A": 2})

    def test_upload_delivery_success_and_errors(self) -> None:
        report = {
            "items": [{"article": "A", "barcode": "bc", "name": "Product", "quantity": 2}],
            "table_title": "Stock",
            "matched": 1,
            "total_rows": 1,
        }
        with mock.patch.object(
            stock_mutations.ff_stock_import, "import_ff_stock_from_xlsx", return_value=report
        ):
            response = self.client.post(
                "/stock/rimili/upload-ff-stock",
                data={"fulfillment": "FF", "marketplace": "WB"},
                files={
                    "file": (
                        "stock.xlsx",
                        b"content",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])

        self.assertEqual(
            self.client.post(
                "/stock/rimili/upload-ff-stock",
                data={"fulfillment": " ", "marketplace": "WB"},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/stock/rimili/upload-ff-stock",
                data={"fulfillment": "FF", "marketplace": "wrong"},
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                "/stock/rimili/upload-ff-stock",
                data={"fulfillment": "FF", "marketplace": "WB"},
            ).status_code,
            400,
        )
        with mock.patch.object(
            stock_mutations.ff_stock_import,
            "import_ff_stock_from_sheet",
            side_effect=stock_mutations.ff_stock_import.FFImportError("bad"),
        ):
            response = self.client.post(
                "/stock/rimili/upload-ff-stock",
                data={"fulfillment": "FF", "marketplace": "WB", "sheet_url": "url"},
            )
        self.assertEqual(response.status_code, 400)

    def test_manual_add_success_and_bad_requests(self) -> None:
        result = AddedFulfillmentItems(
            (AddedFulfillmentItem(article="A", barcode="bc", name="Product", added=2),)
        )
        with mock.patch.object(self.client.app.state.container.stock, "add_items", return_value=result):
            response = self.client.post(
                "/stock/rimili/add-ff-items",
                json={"fulfillment": "FF", "marketplace": "WB", "items": [{"code": "A", "quantity": 2}]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        bad_payloads = [
            {},
            {"fulfillment": "FF", "marketplace": "wrong", "items": []},
            {"fulfillment": "FF", "marketplace": "WB", "items": {}},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                self.assertEqual(
                    self.client.post("/stock/rimili/add-ff-items", json=payload).status_code, 422
                )
        self.assertEqual(
            self.client.post(
                "/stock/rimili/add-ff-items",
                content=b"{bad",
                headers={"content-type": "application/json"},
            ).status_code,
            422,
        )
        with mock.patch.object(
            self.client.app.state.container.stock,
            "add_items",
            side_effect=stock_mutations.ff_stock_import.FFImportError("bad"),
        ):
            self.assertEqual(
                self.client.post(
                    "/stock/rimili/add-ff-items",
                    json={"fulfillment": "FF", "marketplace": "WB", "items": [{"code": "A", "quantity": 1}]},
                ).status_code,
                400,
            )

    def test_transfer_and_shipment_success_and_errors(self) -> None:
        transfer_result = TransferResult(
            moved=StockMovementItems(
                (StockMovementItem(article="A", barcode="bc", name="Product", quantity=2),)
            ),
            skipped=StockMovementItems(
                (StockMovementItem(article="B", barcode="bb", name="Missing", quantity=1, reason="missing"),)
            ),
        )
        with mock.patch.object(
            self.client.app.state.container.stock, "transfer", return_value=transfer_result
        ):
            response = self.client.post(
                "/stock/rimili/transfer",
                data={
                    "from_fulfillment": "A",
                    "from_marketplace": "WB",
                    "to_fulfillment": "B",
                    "to_marketplace": "OZON",
                    "items": json.dumps([{"code": "A", "quantity": 2}]),
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["skipped"]), 1)
        response = self.client.post(
            "/stock/rimili/transfer",
            data={
                "from_fulfillment": "A",
                "from_marketplace": "WB",
                "to_fulfillment": "B",
                "to_marketplace": "OZON",
                "items": "{bad",
            },
        )
        self.assertEqual(response.status_code, 400)
        with mock.patch.object(
            self.client.app.state.container.stock,
            "transfer",
            side_effect=stock_mutations.ff_stock_import.FFImportError("bad"),
        ):
            response = self.client.post(
                "/stock/rimili/transfer",
                data={
                    "from_fulfillment": "A",
                    "from_marketplace": "WB",
                    "to_fulfillment": "B",
                    "to_marketplace": "OZON",
                    "items": "[]",
                },
            )
        self.assertEqual(response.status_code, 400)

        shipped = StockMovementItems(
            (StockMovementItem(article="A", barcode="bc", name="Product", quantity=2),)
        )
        with mock.patch.object(self.client.app.state.container.stock, "ship", return_value=shipped):
            response = self.client.post(
                "/stock/rimili/shipment",
                data={
                    "fulfillment": "FF",
                    "marketplace": "WB",
                    "items": json.dumps([{"code": "A", "quantity": 2}]),
                    "note": "Done",
                    "to_trash": "true",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(
            self.client.post(
                "/stock/rimili/shipment",
                data={"fulfillment": "FF", "marketplace": "WB", "items": "{}"},
            ).status_code,
            400,
        )
        with mock.patch.object(
            self.client.app.state.container.stock,
            "ship",
            side_effect=stock_mutations.ff_stock_import.FFImportError("bad"),
        ):
            self.assertEqual(
                self.client.post(
                    "/stock/rimili/shipment",
                    data={"fulfillment": "FF", "marketplace": "WB", "items": "[]"},
                ).status_code,
                400,
            )

    def test_admin_helpers_and_renderers(self) -> None:
        target = User(
            id=2,
            full_name="Target <x>",
            google_email="target@test",
            login="target",
            role=Role.USER,
            is_active=True,
            can_edit_stock=False,
            can_manage_users=False,
            created_at="2026-08-12T10:00:00+00:00",
            store_slugs=("rimili",),
        )
        self.assertIsNone(admin._guard_user_action(self.user, target, "edit"))
        self.assertIsNotNone(admin._guard_user_action(self.user, None, "edit"))
        self.assertIsNotNone(admin._guard_user_action(self.user, self.user, "edit"))
        self.assertEqual(admin.creatable_roles(self.user), tuple(Role))
        self.assertTrue(admin.can_manage_user(self.user, target))
        self.assertFalse(admin.can_manage_user(self.user, self.user))
        self.assertTrue(admin.render_role_options(self.user))
        self.assertEqual(admin.assignable_store_slugs(self.user), tuple(STORES))
        self.assertEqual(
            admin.normalize_admin_store_selection(self.user, ["RIMILI", "rimili", "wrong"]),
            ("rimili",),
        )
        self.assertIn("u-note", admin.render_store_badges(()))
        self.assertIn("u-store-badge", admin.render_store_badges(("rimili",)))
        self.assertIn("checkbox", admin.render_store_checkboxes(self.user, ("rimili",), disabled=True))
        self.assertIn("empty-row", admin.render_user_rows(self.user, UserCollection(())))
        rows = admin.render_user_rows(self.user, UserCollection((self.user, target)))
        self.assertIn("u-actions", rows)
        self.assertIn("это вы", rows)
        self.assertIn("empty-row", admin.render_log_rows(self.user, ActivityLog(())))
        activity = ActivityLog(
            (
                ActivityEntry(
                    id=1,
                    created_at="2026-01-01T00:00:00+00:00",
                    user_name="User",
                    action="Action",
                    details="Details",
                    operation_id=1,
                ),
            )
        )
        self.assertIn("xlsx", admin.render_log_rows(self.user, activity))

    def test_admin_user_lifecycle_endpoints(self) -> None:
        response = self.client.post(
            "/admin/users",
            data={
                "full_name": "Created",
                "google_email": "created@test",
                "login": "created",
                "password": "password123",
                "role": "user",
                "stores": "rimili",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        created = db.get_user_by_login("created")
        self.assertIsNotNone(created)
        self.assertEqual(
            self.client.post(
                "/admin/users",
                data={"full_name": "", "google_email": "", "login": "", "password": "", "role": ""},
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/admin/users",
                data={
                    "full_name": "X",
                    "google_email": "x@test",
                    "login": "x",
                    "password": "short",
                    "role": "user",
                    "stores": "rimili",
                },
            ).status_code,
            400,
        )

        target_id = self.create_target("lifecycle")
        self.assertEqual(
            self.client.post(
                f"/admin/users/{target_id}/reset-password", data={"password": "newpassword"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"/admin/users/{target_id}/toggle-stock-edit").status_code,
            200,
        )
        inactive = self.client.post(f"/admin/users/{target_id}/toggle-active")
        self.assertEqual(inactive.status_code, 200)
        self.assertFalse(inactive.json()["is_active"])
        self.assertEqual(
            self.client.post(f"/admin/users/{target_id}/stores", data={"stores": "rimili"}).status_code,
            200,
        )
        self.assertEqual(self.client.post(f"/admin/users/{target_id}/delete").status_code, 200)
        self.assertIsNone(db.get_user(target_id))

    def test_admin_page_download_and_sync(self) -> None:
        self.assertEqual(self.client.get("/admin").status_code, 200)
        with mock.patch.object(admin.ff_export, "build_operation_xlsx", return_value=(b"xlsx", "file.xlsx")):
            response = self.client.get("/admin/operations/999/xlsx")
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(admin.ff_export, "build_operation_xlsx", side_effect=LookupError()):
            self.assertEqual(self.client.get("/admin/operations/999/xlsx").status_code, 404)
        with (
            mock.patch.object(admin.wb_catalog, "sync_all", return_value={"rimili": {"ok": True}}),
            mock.patch.object(admin.wb_sync, "sync_all", return_value={"rimili": {"token": True}}),
            mock.patch.object(admin.ozon_catalog, "sync_all", return_value={"rimili": {"ok": True}}),
            mock.patch.object(
                admin.ozon_sync, "sync_all", return_value={"rimili": {"token": True, "ozon": {"ok": True}}}
            ),
            mock.patch.object(admin.ya_catalog, "sync_all", return_value={"rimili": {"ok": True}}),
            mock.patch.object(
                admin.ya_sync, "sync_all", return_value={"rimili": {"token": True, "yandex": {"ok": True}}}
            ),
            mock.patch.object(admin.db, "get_last_sync_at", return_value="2026-01-01"),
        ):
            response = self.client.post("/admin/sync-stock")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("ozon_catalog", response.json()["report"]["rimili"])


if __name__ == "__main__":
    unittest.main()
