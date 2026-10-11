"""SPP report uses adjacent periods and does not mix stores or missing history."""

import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access.sections import section_for_path
from app.dto.identity import SectionName, User
from app.economics.wb import reference_data, spp_report
from app.infrastructure.database import Database
from app.infrastructure.orm import OrmBase
from app.repositories import core
from app.repositories.schema import _migrate_unit_economics_1c_product_categories
from app.web.routers import spp_report as spp_router


class SppReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Database(path=Path(self.directory.name) / "spp.sqlite")
        self.addCleanup(self.database.dispose)
        OrmBase.metadata.create_all(self.database.engine)
        database_patch = patch.object(core, "database_for_path", return_value=self.database)
        database_patch.start()
        self.addCleanup(database_patch.stop)
        with self.database.connect() as connection:
            for store, article, name in (
                ("rimili", "101", "Лампа"),
                ("rimili", "102", "Чайник"),
                ("rimili", "103", "Книга"),
                ("tris", "101", "Другая лампа"),
            ):
                connection.execute(
                    "INSERT INTO stock_items (store_slug, marketplace, article, barcode, name) "
                    "VALUES (?, 'WB', ?, ?, ?)",
                    (store, article, article, name),
                )
            connection.execute(
                "INSERT INTO unit_economics_1c_product_categories "
                "(stock_item_id, category, brand, synced_at) VALUES (1, 'Лампы', 'Свет', '2026-10-01')"
            )
            connection.execute(
                "INSERT INTO unit_economics_1c_source_values "
                "(stock_item_id, manager, source_sheet_id, source_sheet_title, source_row, synced_at) "
                "VALUES (1, 'Менеджер Один', 1, 'WB', 1, '2026-10-01')"
            )
            for store, article, observed, spp in (
                ("rimili", "101", "2026-08-15T10:00:00+00:00", 18),
                ("rimili", "101", "2026-08-31T10:00:00+00:00", 17),
                ("rimili", "101", "2026-09-01T10:00:00+00:00", 16),
                ("rimili", "101", "2026-09-30T10:00:00+00:00", 12),
                ("rimili", "102", "2026-08-31T10:00:00+00:00", 24),
                ("rimili", "102", "2026-09-30T10:00:00+00:00", 25),
                ("rimili", "103", "2026-09-30T10:00:00+00:00", 20),
                ("tris", "101", "2026-08-31T10:00:00+00:00", 11),
                ("tris", "101", "2026-09-30T10:00:00+00:00", 15),
            ):
                connection.execute(
                    "INSERT INTO wb_hourly_prices "
                    "(store_slug, article, observed_hour, observed_at, nm_id, "
                    "customer_price_with_spp, spp_percent, product_kopecks, "
                    "logistics_kopecks, price_fields_json, sale_conditions) "
                    "VALUES (?, ?, ?, ?, ?, 100, ?, 10000, 0, '{}', 0)",
                    (store, article, observed, observed, article, spp),
                )
            connection.commit()

    def test_adjacent_period_last_valid_observation_and_summary(self):
        orders = [
            {"store_slug": "rimili", "article": "101", "net_orders_amount": 1250},
            {"store_slug": "rimili", "article": "102", "net_orders_amount": 980},
        ]
        with patch.object(spp_report.db, "get_unit_economics_1c_funnel_order_totals", return_value=orders):
            result = asdict(
                spp_report.load(("rimili",), date(2026, 9, 1), date(2026, 9, 30), spp_report.SppFilters())
            )
        self.assertEqual(result["period"]["previous_from"], "2026-08-02")
        self.assertEqual(result["period"]["previous_to"], "2026-08-31")
        self.assertEqual([row["article"] for row in result["rows"]], ["101", "102", "103"])
        self.assertEqual(result["rows"][0]["current_spp"], 12)
        self.assertEqual(result["rows"][0]["previous_spp"], 17)
        self.assertEqual([row["status"] for row in result["rows"]], ["problem", "healthy", "no_data"])
        self.assertEqual(
            result["summary"],
            {
                "problem": 1,
                "healthy": 1,
                "no_data": 1,
                "comparable": 2,
                "health_percent": 50,
            },
        )
        self.assertIsNone(result["rows"][2]["turnover"])

    def test_store_and_manager_scope(self):
        with patch.object(spp_report.db, "get_unit_economics_1c_funnel_order_totals", return_value=[]):
            result = asdict(
                spp_report.load(
                    ("rimili", "tris"),
                    date(2026, 9, 1),
                    date(2026, 9, 30),
                    spp_report.SppFilters(),
                    manager_scope="Менеджер Один",
                )
            )
        self.assertEqual([(row["store_slug"], row["article"]) for row in result["rows"]], [("rimili", "101")])
        self.assertEqual(result["options"]["brands"], ["Свет"])

    def test_each_column_sorts_before_pagination_in_both_directions(self):
        orders = [
            {"store_slug": "rimili", "article": "101", "net_orders_amount": 1250},
            {"store_slug": "rimili", "article": "102", "net_orders_amount": 980},
        ]
        expected = {
            "article": (["101", "102", "103"], ["103", "102", "101"]),
            "name": (["103", "101", "102"], ["102", "101", "103"]),
            "turnover": (["102", "101", "103"], ["101", "102", "103"]),
            "current_spp": (["101", "103", "102"], ["102", "103", "101"]),
            "previous_spp": (["101", "102", "103"], ["102", "101", "103"]),
            "status": (["102", "103", "101"], ["101", "103", "102"]),
        }
        with patch.object(spp_report.db, "get_unit_economics_1c_funnel_order_totals", return_value=orders):
            for field, (ascending, descending) in expected.items():
                for order, articles in (
                    (spp_report.SppSortOrder.ASC, ascending),
                    (spp_report.SppSortOrder.DESC, descending),
                ):
                    with self.subTest(field=field, order=order):
                        result = spp_report.load(
                            ("rimili",),
                            date(2026, 9, 1),
                            date(2026, 9, 30),
                            spp_report.SppFilters(),
                            sort_by=spp_report.SppSortField(field),
                            sort_order=order,
                        )
                        self.assertEqual([row.article for row in result.rows], articles)
                        self.assertEqual(result.summary.health_percent, 50)
                        self.assertEqual(result.sort_by, field)
                        self.assertEqual(result.sort_order, order)

            page_two = spp_report.load(
                ("rimili",),
                date(2026, 9, 1),
                date(2026, 9, 30),
                spp_report.SppFilters(),
                page=2,
                page_size=1,
                sort_by=spp_report.SppSortField.NAME,
                sort_order=spp_report.SppSortOrder.ASC,
            )
            self.assertEqual([row.article for row in page_two.rows], ["101"])
            self.assertEqual(page_two.total, 3)
            self.assertEqual(page_two.summary.health_percent, 50)

    def test_articles_use_numeric_order(self):
        with self.database.connect() as connection:
            for article in ("9", "10"):
                connection.execute(
                    "INSERT INTO stock_items (store_slug, marketplace, article, barcode, name) "
                    "VALUES ('rimili', 'WB', ?, ?, ?)",
                    (article, article, article),
                )
            connection.commit()
        with patch.object(spp_report.db, "get_unit_economics_1c_funnel_order_totals", return_value=[]):
            result = spp_report.load(
                ("rimili",),
                date(2026, 9, 1),
                date(2026, 9, 30),
                spp_report.SppFilters(),
                sort_by=spp_report.SppSortField.ARTICLE,
                sort_order=spp_report.SppSortOrder.ASC,
            )
        self.assertEqual([row.article for row in result.rows], ["9", "10", "101", "102", "103"])

    def test_brand_migration_for_existing_table(self):
        legacy = Database(path=Path(self.directory.name) / "legacy.sqlite")
        self.addCleanup(legacy.dispose)
        with legacy.connect() as connection:
            connection.execute(
                "CREATE TABLE unit_economics_1c_product_categories "
                "(stock_item_id INTEGER PRIMARY KEY, imt_id BIGINT, created_at TEXT, synced_at TEXT)"
            )
            connection.execute(
                "INSERT INTO unit_economics_1c_product_categories "
                "(stock_item_id, synced_at) VALUES (1, '2026-10-10')"
            )
            connection.commit()
        _migrate_unit_economics_1c_product_categories(legacy)
        with legacy.connect() as connection:
            self.assertIn("brand", connection.column_names("unit_economics_1c_product_categories"))
            self.assertEqual(
                connection.execute("SELECT synced_at FROM unit_economics_1c_product_categories").fetchone()[
                    "synced_at"
                ],
                "",
            )

    def test_api_rejects_unavailable_store_and_invalid_period(self):
        user = User(
            id=1,
            login="tester",
            full_name="Тестовый пользователь",
            created_at=datetime.now(UTC),
        )
        app = FastAPI()

        @app.middleware("http")
        async def identify(request, call_next):
            request.state.user = user
            return await call_next(request)

        app.include_router(spp_router.router)
        with TestClient(app) as client:
            page_response = client.get("/sales/unit-economics-1c/reports/spp")
            report_response = client.get(
                "/api/unit-economics-1c/reports/spp?date_from=2026-09-01&date_to=2026-09-30"
            )
            denied = client.get("/api/unit-economics-1c/reports/spp?store=unknown")
            invalid = client.get("/api/unit-economics-1c/reports/spp?date_from=2026-09-30&date_to=2026-09-01")
            invalid_sort = client.get("/api/unit-economics-1c/reports/spp?sort_by=unknown")
            self.assertEqual(page_response.status_code, 200)
            self.assertIn("Отчёт по СПП", page_response.text)
            self.assertEqual(report_response.status_code, 200)
            self.assertEqual(report_response.json()["period"]["previous_from"], "2026-08-02")
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(invalid_sort.status_code, 422)
        self.assertEqual(
            section_for_path("/api/unit-economics-1c/reports/spp"),
            SectionName.REPORT_SPP,
        )

    def test_brand_is_saved_by_existing_card_sync(self):
        cards = [{"nmID": 101, "subjectName": "Лампы", "brand": "Свет"}]
        catalog = [{"id": 1, "article": "101"}]
        with (
            patch.object(
                reference_data.db, "list_unit_economics_1c_active_wb_stock_items", return_value=catalog
            ),
            patch.object(reference_data.wb_tokens, "has_token", return_value=True),
            patch.object(reference_data.wb_tokens, "get_token", return_value="token"),
            patch.object(reference_data.wb_api, "get_cards_list", return_value=cards),
            patch.object(reference_data.db, "replace_unit_economics_1c_product_categories") as save,
        ):
            reference_data.sync_product_categories("rimili")
        self.assertEqual(save.call_args.args[1][0]["brand"], "Свет")


if __name__ == "__main__":
    unittest.main()
