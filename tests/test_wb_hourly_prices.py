"""Hourly history stores observations, including shared products and partial responses."""

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from app.infrastructure.database import Database
from app.infrastructure.orm import OrmBase
from app.infrastructure.wb_hourly_price_repository import SqlAlchemyHourlyPriceWriter
from app.jobs import background
from app.wb.hourly_prices import WbHourlyPriceService


class HourlyPriceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Database(path=Path(self.directory.name) / "prices.sqlite")
        OrmBase.metadata.create_all(self.database.engine)
        self.database_patch = patch(
            "app.infrastructure.wb_hourly_price_repository.database_for_path", return_value=self.database
        )
        self.database_patch.start()
        self.addCleanup(self.database_patch.stop)
        self.now = datetime(2026, 10, 9, 10, 15, tzinfo=UTC)
        self.response = {
            "products": [
                {
                    "id": 123,
                    "sizes": [
                        {
                            "optionId": 11,
                            "origName": "M",
                            "saleConditions": 0,
                            "price": {"product": 99900, "logistics": 100, "basic": 120000},
                        },
                        {
                            "optionId": 12,
                            "origName": "L",
                            "saleConditions": 1,
                            "price": {"product": 109900, "logistics": 100},
                        },
                    ],
                }
            ],
            "failed_nm_ids": ["456"],
            "errors": ["WB недоступен для 456"],
        }
        self.requested = []
        self.service = WbHourlyPriceService(
            catalog_items=lambda store: (
                [{"article": "123 / M"}, {"article": "123 / L"}, {"article": "456"}]
                if store == "rimili"
                else [{"article": "123 / M"}]
            ),
            storefront_products=lambda ids: self._response(ids),
            wallet_discount=lambda: 5,
            wallet_price=lambda price, discount, conditions: price * (1 - discount / 100),
            writer=SqlAlchemyHourlyPriceWriter(lambda: Path(self.directory.name) / "prices.sqlite"),
            clock=lambda: self.now,
        )

    def _response(self, ids):
        self.requested.append(ids)
        return self.response

    def rows(self):
        with self.database.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM wb_hourly_prices ORDER BY store_slug, article, observed_hour"
                ).fetchall()
            ]

    def test_all_stores_sizes_and_failed_product(self):
        report = self.service.collect(("rimili", "tris"))
        self.assertEqual(self.requested, [("123", "456")])
        self.assertEqual(report["rimili"]["rows"], 2, report)
        self.assertFalse(report["rimili"]["ok"])
        self.assertEqual(report["rimili"]["failed_nm_ids"], ["456"])
        self.assertTrue(report["tris"]["ok"])
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["customer_price_with_spp"], 1100)
        self.assertEqual(rows[1]["customer_price_with_spp"], 1000)
        self.assertEqual(rows[1]["wallet_discount_percent"], 5)
        self.assertIn('"basic": 120000', rows[1]["price_fields_json"])
        self.assertEqual(rows[1]["observed_hour"], "2026-10-09T10:00:00+00:00")

    def test_retry_upserts_same_hour_and_next_hour_adds_history(self):
        self.service.collect(("tris",))
        self.response["products"][0]["sizes"][0]["price"]["product"] = 89900
        self.now = datetime(2026, 10, 9, 10, 45, tzinfo=UTC)
        self.service.collect(("tris",))
        self.assertEqual(len(self.rows()), 1, self.rows())
        self.assertEqual(self.rows()[0]["customer_price_with_spp"], 900)
        self.now = datetime(2026, 10, 9, 11, 1, tzinfo=UTC)
        self.service.collect(("tris",))
        self.assertEqual(len(self.rows()), 2)

    def test_missing_price_is_not_replaced_with_estimate(self):
        self.response["products"] = []
        report = self.service.collect(("tris",))
        self.assertEqual(report["tris"]["missing_prices"], 1)
        self.assertFalse(report["tris"]["ok"])
        self.assertEqual(self.rows(), [])

    def test_wallet_failure_does_not_prevent_spp_history(self):
        self.service.wallet_discount = lambda: (_ for _ in ()).throw(ConnectionError("wallet"))
        report = self.service.collect(("tris",))
        self.assertTrue(report["tris"]["ok"])
        self.assertEqual(self.rows()[0]["customer_price_with_spp"], 1000)
        self.assertIsNone(self.rows()[0]["customer_price_with_wallet"])

    def test_catalog_failure_in_one_store_does_not_block_another(self):
        original = self.service.catalog_items

        def catalog(store):
            if store == "rimili":
                raise RuntimeError("catalog unavailable")
            return original(store)

        self.service.catalog_items = catalog
        report = self.service.collect(("rimili", "tris"))
        self.assertFalse(report["rimili"]["ok"])
        self.assertEqual(report["tris"]["rows"], 1)
        self.assertEqual(len(self.rows()), 1)

    def test_hourly_job_is_independent_of_price_sync(self):
        service = Mock()
        job = background._wb_hourly_price_job(service)
        self.assertEqual(job.name, "wb_hourly_prices_sync")
        self.assertEqual(job.next_delay(), background.settings.wb_hourly_prices_interval_seconds)
        with patch.object(background.sync_settings, "enabled_stores", return_value=("rimili",)):
            job.callback()
        service.collect.assert_called_once_with(("rimili",))

    def test_container_wires_collection_to_wb_and_database(self):
        from app.container import ApplicationContainer

        with (
            patch("app.container.wb_catalog_items", return_value=[{"article": "123 / M"}]),
            patch("app.container.wb_api.get_storefront_products", return_value=self.response) as fetch,
            patch("app.container.wb_api.get_default_wallet_discount_percent", return_value=5),
        ):
            container = ApplicationContainer(
                database_path=lambda: Path(self.directory.name) / "prices.sqlite"
            )
            report = container.wb_hourly_prices.collect(("tris",))
        self.assertEqual(report["tris"]["rows"], 1)
        fetch.assert_called_once_with(("123",))
        self.assertEqual(self.rows()[0]["customer_price_with_spp"], 1000)


if __name__ == "__main__":
    unittest.main()
