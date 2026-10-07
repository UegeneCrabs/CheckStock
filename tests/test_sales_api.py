"""Saved funnel orders, period coverage, cabinet/manager scope and separate page."""

import os
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import dotenv

with (
    patch.object(dotenv, "dotenv_values", return_value={}),
    patch.dict(os.environ, {"CHECKSTOCK_DATABASE_URL": ""}),
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.access.sections import has_access, section_for_path
    from app.analytics import sales_api
    from app.dto.identity import SectionAccessLevel, SectionName, User
    from app.infrastructure.database import Database
    from app.infrastructure.orm import OrmBase
    from app.repositories import core
    from app.web.routers import sales_api as api


class SalesApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="checkstock-orders-")
        self.database = Database(path=Path(self.directory.name) / "isolated.sqlite")
        OrmBase.metadata.create_all(self.database.engine)
        self.database_patch = patch.object(core, "database_for_path", return_value=self.database)
        self.database_patch.start()
        self.user = self.make_user()
        app = FastAPI()

        @app.middleware("http")
        async def identify(request, call_next):
            request.state.user = self.user
            return await call_next(request)

        app.include_router(api.router)
        self.client = TestClient(app)
        for store, article, barcode in (
            ("rimili", "123", "00123"),
            ("rimili", "456", "00456"),
            ("tris", "123", "00999"),
        ):
            self.execute(
                "INSERT INTO stock_items (store_slug,marketplace,article,barcode,name) VALUES (?,'WB',?,?,?)",
                (store, article, barcode, "Товар " + article),
            )
        for product_id, manager in ((1, "Менеджер Один"), (2, "Менеджер Два"), (3, "Менеджер Один")):
            self.execute(
                "INSERT INTO unit_economics_1c_source_values (stock_item_id,manager,source_sheet_id,source_sheet_title,source_row,synced_at) VALUES (?,?,1,'WB',1,'2026-10-05')",
                (product_id, manager),
            )
        self.order("rimili", "123", "2026-10-05", 10, 3, 2)
        self.order("rimili", "123", "2026-10-06", 4, 1, 0)
        self.order("tris", "123", "2026-10-05", 99, 8, 7)

    def tearDown(self):
        self.client.close()
        self.database_patch.stop()
        self.database.dispose()
        self.directory.cleanup()

    def make_user(self, **changes):
        return User(
            id=1,
            login="orders",
            full_name="Менеджер Один",
            created_at=datetime.now(UTC),
            role=changes.pop("role", "superadmin"),
            **changes,
        )

    def execute(self, sql, params=()):
        with self.database.connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def order(self, store, article, day, orders, cancels=0, buyouts=0, version=4):
        self.execute(
            "INSERT INTO wb_funnel_daily_orders (store_slug,article,day,orders_count,cancel_count,buyout_count,source_version,product_name,updated_at) VALUES (?,?,?,?,?,?,?,'Название из воронки','2026-10-07T09:00:00+00:00')",
            (store, article, day, orders, cancels, buyouts, version),
        )

    def load(self, stores=("rimili",), start=date(2026, 10, 5), end=date(2026, 10, 7)):
        return sales_api.load(stores, self.user, start, end, today=date(2026, 10, 7))

    def test_orders_are_raw_funnel_counts_not_buyouts_or_net(self):
        result = self.load(end=date(2026, 10, 6))
        row = next(row for row in result["rows"] if row["article"] == "123")
        self.assertEqual(row["days"], [10, 4])
        self.assertEqual(row["orders"], 14)
        self.assertEqual(row["cancellations"], [3, 1])
        self.assertEqual(row["cancels"], 4)
        self.assertTrue(row["complete"])
        self.assertEqual(result["dates"], ["2026-10-05", "2026-10-06"])

    def test_loaded_absent_product_zero_and_unloaded_day_unknown(self):
        result = self.load()
        row = next(row for row in result["rows"] if row["article"] == "456")
        self.assertEqual(row["days"], [0, 0, None])
        self.assertEqual(row["orders"], 0)
        self.assertFalse(row["complete"])
        self.assertEqual(row["known_days"], 2)
        row = self.load(start=date(2026, 10, 7))["rows"][0]
        self.assertIsNone(row["orders"])
        self.assertIsNone(row["cancels"])

    def test_legacy_row_does_not_prove_other_product_has_zero_orders(self):
        self.order("rimili", "123", "2026-10-04", 8, version=3)
        rows = self.load(start=date(2026, 10, 4), end=date(2026, 10, 4))["rows"]
        self.assertEqual(next(row for row in rows if row["article"] == "123")["orders"], 8)
        self.assertIsNone(next(row for row in rows if row["article"] == "456")["orders"])

    def test_default_period_uses_latest_saved_day_including_empty_load(self):
        result = self.load(start=None, end=None)
        self.assertEqual((result["date_from"], result["date_to"]), ("2026-09-30", "2026-10-06"))
        self.execute(
            "INSERT INTO economics_source_days (marketplace,store_slug,source,day,updated_at) VALUES ('WB','rimili','orders','2026-10-07','2026-10-07')"
        )
        result = self.load(start=None, end=None)
        self.assertEqual((result["date_from"], result["date_to"]), ("2026-10-01", "2026-10-07"))
        self.assertTrue(all(row["days"][-1] == 0 for row in result["rows"]))

    def test_duplicate_sizes_do_not_multiply_article_orders(self):
        self.execute(
            "INSERT INTO stock_items (store_slug,marketplace,article,barcode,name,image_url) VALUES ('rimili','WB','123 / XL','55555','Размер XL','https://example.com/product.jpg')"
        )
        rows = self.load()["rows"]
        self.assertEqual(len(rows), 2)
        row = next(row for row in rows if row["article"] == "123")
        self.assertEqual(row["orders"], 14)
        self.assertEqual(row["barcodes"], ["00123", "55555"])
        self.assertEqual(row["image"], "https://example.com/product.jpg")

    def test_cabinet_join_and_historical_products(self):
        self.order("rimili", "999", "2026-10-05", 17)
        rows = self.load(("rimili", "tris"))["rows"]
        self.assertEqual(next(row for row in rows if row["id"] == "tris:123")["orders"], 99)
        self.assertEqual(next(row for row in rows if row["id"] == "rimili:123")["orders"], 14)
        row = next(row for row in rows if row["id"] == "rimili:999")
        self.assertEqual(row["name"], "Название из воронки")
        self.assertEqual(row["orders"], 17)

    def test_manager_and_store_scope_hides_other_products_and_history(self):
        self.order("rimili", "999", "2026-10-05", 17)
        self.user = self.make_user(role="user", store_slugs=("rimili",))
        response = self.client.get("/api/analytics/sales-api?date_from=2026-10-05&date_to=2026-10-06")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["rows"]], ["rimili:123"])

    def test_no_stores_returns_empty_result(self):
        self.assertEqual(self.load(stores=())["rows"], [])

    def test_date_validation(self):
        for query in (
            "date_from=2026-10-05",
            "date_to=2026-10-05",
            "date_from=2026-10-06&date_to=2026-10-05",
            "date_from=2099-01-01&date_to=2099-01-02",
            "date_from=0001-01-01&date_to=2026-10-05",
            "date_from=2024-01-01&date_to=2025-01-01",
            "date_from=bad&date_to=2026-10-05",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get("/api/analytics/sales-api?" + query).status_code, 422)
        self.assertEqual(
            self.client.get("/api/analytics/sales-api?date_from=2024-01-01&date_to=2024-12-31").status_code,
            200,
        )

    def test_separate_section_and_route_protection(self):
        self.user = self.make_user(
            role="admin", section_access={SectionName.UNIT_ECONOMICS_WB: SectionAccessLevel.NONE}
        )
        self.assertFalse(has_access(self.user, SectionName.ANALYTICS_SALES_API))
        for path in ("/analytics/sales-api", "/api/analytics/sales-api"):
            self.assertEqual(section_for_path(path), SectionName.ANALYTICS_SALES_API)
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_page_and_navigation_are_separate_from_analyzer(self):
        self.user = self.make_user(
            role="admin",
            section_access={
                SectionName.ANALYZER: SectionAccessLevel.NONE,
                SectionName.ANALYTICS_SALES_API: SectionAccessLevel.READ,
            },
        )
        response = self.client.get("/analytics/sales-api")
        self.assertEqual(response.status_code, 200)
        page = response.text
        self.assertIn('href="/analytics/analyzer" hidden', page)
        self.assertIn('href="/analytics/sales-api">Продажи по API', page)
        self.assertIn("/static/analytics/sales-api.js", page)
        self.assertIn("Заказы из воронки WB", page)
        self.assertNotIn("MOCK", page)
        self.assertNotIn("Итого продажи", page)


if __name__ == "__main__":
    unittest.main()
