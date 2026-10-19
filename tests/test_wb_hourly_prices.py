"""Hourly history stores observations, including shared products and partial responses."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from app.dto.wb_hourly_prices import WbCatalogItem, WbHourlyCollection, WbHourlyStoreReport
from app.infrastructure.database import Database
from app.infrastructure.orm import OrmBase
from app.infrastructure.wb_hourly_price_repository import (
    SqlAlchemyDailyPriceCloser,
    SqlAlchemyHourlyPriceWriter,
)
from app.jobs import background
from app.repositories.schema import _migrate_wb_hourly_spp_percent, _migrate_wb_hourly_turnover
from app.wb import api
from app.wb.hourly_prices import (
    WbHourlyPriceService,
    seller_prices_from_payload,
    storefront_result_from_payload,
)


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
        self.seller_goods = [
            {
                "nmID": 123,
                "sizes": [
                    {"sizeID": 11, "techSizeName": "M", "discountedPrice": 1200},
                    {"sizeID": 12, "techSizeName": "L", "discountedPrice": 1375},
                ],
            }
        ]
        self.requested = []
        self.seller_requested = []
        self.service = WbHourlyPriceService(
            catalog_items=lambda store: (
                (WbCatalogItem("123 / M"), WbCatalogItem("123 / L"), WbCatalogItem("456"))
                if store == "rimili"
                else (WbCatalogItem("123 / M"),)
            ),
            storefront_products=lambda ids: storefront_result_from_payload(self._response(ids)),
            seller_prices=lambda store, ids: self._seller_response(store, ids),
            wallet_discount=lambda: 5,
            wallet_price=lambda price, discount, conditions: price * (1 - discount / 100),
            writer=SqlAlchemyHourlyPriceWriter(lambda: Path(self.directory.name) / "prices.sqlite"),
            clock=lambda: self.now,
        )

    def _response(self, ids):
        self.requested.append(ids)
        return self.response

    def _seller_response(self, store, ids):
        self.seller_requested.append((store, ids))
        return seller_prices_from_payload(self.seller_goods)

    def rows(self):
        with self.database.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM wb_hourly_prices ORDER BY store_slug, article, observed_hour"
                ).fetchall()
            ]

    def test_all_stores_sizes_and_failed_product(self):
        with self.assertLogs("app.wb.hourly_prices", level="INFO") as captured:
            report = self.service.collect(("rimili", "tris"))
        messages = "\n".join(captured.output)
        self.assertIn("начало сбора, магазинов=2", messages)
        self.assertIn("ответ витрины, размеров с ценой=2", messages)
        self.assertIn("магазин=tris, сохранено=1, с процентом СПП=1", messages)
        self.assertIn("сбор завершён, магазинов=2, сохранено=3", messages)
        self.assertEqual(self.requested, [("123", "456")])
        self.assertEqual(report.for_store("rimili").rows, 2, report)
        self.assertFalse(report.for_store("rimili").ok)
        self.assertEqual(report.for_store("rimili").failed_nm_ids, ("456",))
        self.assertTrue(report.for_store("tris").ok)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["customer_price_with_spp"], 1100)
        self.assertEqual(rows[1]["customer_price_with_spp"], 1000)
        self.assertAlmostEqual(rows[1]["spp_percent"], (1200 - 1000) / 1200 * 100)
        self.assertEqual(rows[0]["spp_percent"], 20)
        self.assertEqual(self.seller_requested, [("rimili", ("123",)), ("tris", ("123",))])
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
        self.assertEqual(self.rows()[0]["spp_percent"], 25)
        self.now = datetime(2026, 10, 9, 11, 1, tzinfo=UTC)
        self.service.collect(("tris",))
        self.assertEqual(len(self.rows()), 2)

    def test_hourly_price_keeps_daily_turnover_at_collection_time(self):
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO wb_funnel_daily_orders
                   (store_slug, day, article, orders_count, orders_amount,
                    cancel_count, cancel_amount, updated_at)
                   VALUES ('rimili', '2026-10-09', '123', 3, 3000, 1, 1000,
                           '2026-10-09T10:00:00+00:00')"""
            )
            connection.commit()
        self.service.collect(("rimili",))
        rows = self.rows()
        self.assertEqual([row["orders_turnover_rub"] for row in rows], [2000, 2000])
        self.assertEqual(rows[0]["orders_turnover_updated_at"], "2026-10-09T10:00:00+00:00")

        with self.database.connect() as connection:
            connection.execute(
                """UPDATE wb_funnel_daily_orders
                   SET orders_amount=3500, updated_at='2026-10-09T11:00:00+00:00'
                   WHERE store_slug='rimili' AND day='2026-10-09' AND article='123'"""
            )
            connection.commit()
        self.now = datetime(2026, 10, 9, 11, 15, tzinfo=UTC)
        self.service.collect(("rimili",))
        rows = self.rows()
        self.assertEqual([row["orders_turnover_rub"] for row in rows], [2000, 2500, 2000, 2500])

    def test_missing_daily_turnover_stays_unknown(self):
        self.service.collect(("tris",))
        self.assertIsNone(self.rows()[0]["orders_turnover_rub"])
        self.assertIsNone(self.rows()[0]["orders_turnover_updated_at"])

    def test_turnover_day_follows_moscow_date(self):
        self.now = datetime(2026, 10, 9, 21, 15, tzinfo=UTC)
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO wb_funnel_daily_orders
                   (store_slug, day, article, orders_count, orders_amount,
                    cancel_count, cancel_amount, updated_at)
                   VALUES ('tris', '2026-10-10', '123', 1, 500, 0, 0,
                           '2026-10-09T21:05:00+00:00')"""
            )
            connection.commit()
        self.service.collect(("tris",))
        self.assertEqual(self.rows()[0]["orders_turnover_rub"], 500)

    def test_missing_price_is_not_replaced_with_estimate(self):
        self.response["products"] = []
        report = self.service.collect(("tris",))
        self.assertEqual(report.for_store("tris").missing_prices, 1)
        self.assertFalse(report.for_store("tris").ok)
        self.assertEqual(self.rows(), [])

    def test_wallet_failure_does_not_prevent_spp_history(self):
        self.service.wallet_discount = lambda: (_ for _ in ()).throw(ConnectionError("wallet"))
        report = self.service.collect(("tris",))
        self.assertTrue(report.for_store("tris").ok)
        self.assertEqual(self.rows()[0]["customer_price_with_spp"], 1000)
        self.assertIsNone(self.rows()[0]["customer_price_with_wallet"])

    def test_seller_failure_does_not_prevent_price_history(self):
        self.service.seller_prices = lambda store, ids: (_ for _ in ()).throw(ConnectionError("seller"))
        with self.assertLogs("app.wb.hourly_prices", level="WARNING") as captured:
            self.service.collect(("tris",))
        self.assertIn("цены продавца недоступны, магазин=tris", captured.output[0])
        self.assertEqual(self.rows()[0]["customer_price_with_spp"], 1000)
        self.assertIsNone(self.rows()[0]["spp_percent"])

    def test_api_does_not_send_a_fixed_spp_percent(self):
        with patch("app.wb.api._public_request", return_value={"products": [{"id": 123}]}) as request:
            api.get_storefront_products(("123",))
        self.assertNotIn("spp", request.call_args.kwargs["params"])

    def test_legacy_history_remains_without_calculated_spp(self):
        legacy = Database(path=Path(self.directory.name) / "legacy.sqlite")
        with legacy.connect() as connection:
            connection.execute(
                "CREATE TABLE wb_hourly_prices (store_slug TEXT, article TEXT, observed_hour TEXT)"
            )
            connection.execute(
                "INSERT INTO wb_hourly_prices VALUES ('tris', '123 / M', '2026-10-09T10:00:00+00:00')"
            )
            connection.commit()
        _migrate_wb_hourly_spp_percent(legacy)
        _migrate_wb_hourly_spp_percent(legacy)
        _migrate_wb_hourly_turnover(legacy)
        _migrate_wb_hourly_turnover(legacy)
        with legacy.connect() as connection:
            row = connection.execute(
                "SELECT spp_percent, orders_turnover_rub, orders_turnover_updated_at FROM wb_hourly_prices"
            ).fetchone()
        self.assertIsNone(row["spp_percent"])
        self.assertIsNone(row["orders_turnover_rub"])
        self.assertIsNone(row["orders_turnover_updated_at"])

    def test_unmatched_or_invalid_seller_price_keeps_spp_unknown(self):
        self.seller_goods[0]["sizes"] = [
            {"sizeID": 99, "techSizeName": "M", "discountedPrice": 1200},
        ]
        self.service.collect(("tris",))
        self.assertIsNone(self.rows()[0]["spp_percent"])
        self.seller_goods[0]["sizes"] = [
            {"sizeID": 99, "techSizeName": "XL", "discountedPrice": 1200},
            {"sizeID": 98, "techSizeName": "S", "discountedPrice": 1300},
        ]
        self.service.collect(("tris",))
        self.assertIsNone(self.rows()[0]["spp_percent"])
        self.seller_goods[0]["sizes"] = [
            {"sizeID": 11, "techSizeName": "M", "discountedPrice": 900},
        ]
        self.service.collect(("tris",))
        self.assertIsNone(self.rows()[0]["spp_percent"])

    def test_catalog_failure_in_one_store_does_not_block_another(self):
        original = self.service.catalog_items

        def catalog(store):
            if store == "rimili":
                raise RuntimeError("catalog unavailable")
            return original(store)

        self.service.catalog_items = catalog
        report = self.service.collect(("rimili", "tris"))
        self.assertFalse(report.for_store("rimili").ok)
        self.assertEqual(report.for_store("tris").rows, 1)
        self.assertEqual(len(self.rows()), 1)

    def test_empty_catalog_logs_completion_without_storefront_request(self):
        self.service.catalog_items = lambda store: ()
        with self.assertLogs("app.wb.hourly_prices", level="INFO") as captured:
            report = self.service.collect(("tris",))
        self.assertEqual(self.requested, [])
        self.assertTrue(report.for_store("tris").ok)
        self.assertIn("нет nmID для запроса витрины", "\n".join(captured.output))
        self.assertIn("сбор завершён, магазинов=1, сохранено=0", "\n".join(captured.output))

    def test_hourly_job_is_independent_of_price_sync(self):
        service = Mock()
        service.collect.return_value = WbHourlyCollection(
            (WbHourlyStoreReport(store_slug="rimili", ok=False, rows=0, error="catalog unavailable"),)
        )
        job = background._wb_hourly_price_job(service)
        self.assertEqual(job.name, "wb_hourly_prices_sync")
        self.assertEqual(job.next_delay(), background.settings.wb_hourly_prices_interval_seconds)
        with patch.object(background.sync_settings, "enabled_stores", return_value=("rimili",)):
            result = job.callback()
        service.collect.assert_called_once_with(("rimili",))
        self.assertEqual(result["rimili"]["error"], "catalog unavailable")

    def test_container_wires_collection_to_wb_and_database(self):
        from app.container import ApplicationContainer

        with (
            patch("app.container.wb_catalog_items", return_value=(WbCatalogItem("123 / M"),)),
            patch("app.container.wb_api.get_storefront_products", return_value=self.response) as fetch,
            patch(
                "app.container._wb_seller_prices", return_value=seller_prices_from_payload(self.seller_goods)
            ),
            patch("app.container.wb_api.get_default_wallet_discount_percent", return_value=5),
        ):
            container = ApplicationContainer(
                database_path=lambda: Path(self.directory.name) / "prices.sqlite"
            )
            report = container.wb_hourly_prices.collect(("tris",))
        self.assertEqual(report.for_store("tris").rows, 1)
        fetch.assert_called_once_with(("123",))
        self.assertEqual(self.rows()[0]["customer_price_with_spp"], 1000)
        self.assertAlmostEqual(self.rows()[0]["spp_percent"], (1200 - 1000) / 1200 * 100)

    def test_seller_api_adapter_returns_named_prices(self):
        from app.container import _wb_seller_prices

        with (
            patch("app.container.wb_tokens.get_token", return_value="token"),
            patch("app.container.wb_api.get_goods_prices_by_nm_ids", return_value=self.seller_goods) as fetch,
        ):
            prices = _wb_seller_prices("tris", ("123",))
        fetch.assert_called_once_with("token", [123])
        self.assertEqual(prices[0].nm_id, "123")
        self.assertEqual(prices[0].size_id, 11)
        self.assertEqual(prices[0].discounted_price, 1200)

    def test_close_uses_turnover_weights_and_keeps_new_moscow_day(self):
        with self.database.connect() as connection:
            for observed, spp, turnover, price, article in (
                ("2026-10-09T20:00:00+00:00", 10, 10, 100, "123 / M"),
                ("2026-10-09T20:30:00+00:00", 30, 30, 200, "123 / M"),
                ("2026-10-09T20:30:00+00:00", 20, None, 300, "123 / L"),
                ("2026-10-09T20:45:00+00:00", 40, 0, 100, "123 / L"),
                ("2026-10-09T21:00:00+00:00", 40, 40, 400, "123 / M"),
            ):
                connection.execute(
                    "INSERT INTO wb_hourly_prices (store_slug, article, observed_hour, observed_at, nm_id, "
                    "customer_price_with_spp, spp_percent, orders_turnover_rub, customer_price_with_wallet, "
                    "wallet_discount_percent, product_kopecks, logistics_kopecks, price_fields_json, "
                    "sale_conditions) VALUES ('tris', ?, ?, ?, '123', ?, ?, ?, ?, 5, ?, 0, '{}', 0)",
                    (article, observed, observed, price, spp, turnover, price - 10, price * 100),
                )
            connection.commit()
        closer = SqlAlchemyDailyPriceCloser(lambda: Path(self.directory.name) / "prices.sqlite")
        self.assertEqual(closer.close(date(2026, 10, 10)), 2)
        self.assertEqual(closer.close(date(2026, 10, 10)), 0)
        with self.database.connect() as connection:
            daily = connection.execute(
                "SELECT article, observed_day, customer_price_with_spp, spp_percent, "
                "product_kopecks FROM wb_daily_prices ORDER BY article"
            ).fetchall()
            hourly = connection.execute("SELECT observed_at FROM wb_hourly_prices").fetchall()
        self.assertEqual(len(daily), 2)
        self.assertEqual(daily[0]["observed_day"], "2026-10-09")
        self.assertEqual(daily[0]["customer_price_with_spp"], 200)
        self.assertEqual(daily[0]["spp_percent"], 30)
        self.assertEqual(daily[1]["customer_price_with_spp"], 175)
        self.assertEqual(daily[1]["spp_percent"], 25)
        self.assertEqual(daily[1]["product_kopecks"], 17500)
        self.assertEqual([row["observed_at"] for row in hourly], ["2026-10-09T21:00:00+00:00"])

    def test_close_rolls_back_if_daily_row_already_exists(self):
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO wb_hourly_prices (store_slug, article, observed_hour, observed_at, nm_id, "
                "customer_price_with_spp, product_kopecks, logistics_kopecks, price_fields_json, "
                "sale_conditions) VALUES ('tris', '123', '2026-10-09T20:00:00+00:00', "
                "'2026-10-09T20:00:00+00:00', '123', 100, 10000, 0, '{}', 0)"
            )
            connection.execute(
                "INSERT INTO wb_daily_prices (store_slug, article, observed_day, observed_at, nm_id, "
                "customer_price_with_spp, product_kopecks, logistics_kopecks, price_fields_json, "
                "sale_conditions) VALUES ('tris', '123', '2026-10-09', "
                "'2026-10-09T19:00:00+00:00', '123', 100, 10000, 0, '{}', 0)"
            )
            connection.commit()
        closer = SqlAlchemyDailyPriceCloser(lambda: Path(self.directory.name) / "prices.sqlite")
        with self.assertRaisesRegex(RuntimeError, "Late hourly price"):
            closer.close(date(2026, 10, 10))
        with self.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) AS count FROM wb_hourly_prices").fetchone()
        self.assertEqual(count["count"], 1)


if __name__ == "__main__":
    unittest.main()
