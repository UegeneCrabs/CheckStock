import logging
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from app.wb import api as wb_api
from app.wb import unit_economics


class WildberriesUnitEconomicsTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_value_price_and_order_helpers(self) -> None:
        self.assertEqual(unit_economics._base_article("10 / M"), ("10", "M"))
        self.assertEqual(unit_economics._base_article(None), ("", ""))
        self.assertEqual(unit_economics._number("2.5"), 2.5)
        self.assertIsNone(unit_economics._number(-1))
        self.assertIsNone(unit_economics._number("bad"))
        self.assertIsNone(unit_economics._parse_datetime("bad"))
        self.assertIsNotNone(unit_economics._parse_datetime("2026-01-01T00:00:00"))
        self.assertIn("WB", unit_economics._friendly_error("WB", wb_api.WBApiError(403, "denied")))

        prices = unit_economics._price_map(
            [
                {
                    "nmID": 10,
                    "sizes": [
                        {"techSizeName": "S", "price": 100, "discountedPrice": 80},
                        {"techSizeName": "M", "price": 120, "discountedPrice": 70},
                    ],
                },
                {"nmId": 11, "price": 50},
                {"sizes": []},
                {"nmID": 12, "sizes": [{"price": "bad"}]},
            ]
        )
        self.assertEqual(prices[("10", "")]["discounted_price"], 70)
        self.assertEqual(prices[("11", "")]["list_price"], 50)

        barcode, article = unit_economics._order_price_maps(
            [
                {
                    "finishedPrice": 90,
                    "barcode": "bc",
                    "nmId": 10,
                    "techSize": "S",
                    "lastChangeDate": "2026-01-01",
                    "spp": 10,
                },
                {
                    "finishedPrice": 95,
                    "barcode": "bc",
                    "nmId": 10,
                    "techSize": "S",
                    "lastChangeDate": "2026-01-02",
                },
                {"finishedPrice": 0, "barcode": "skip"},
            ]
        )
        self.assertEqual(barcode["bc"]["buyer_price"], 95)
        self.assertEqual(article[("10", "S")]["buyer_price"], 95)

    def test_sync_prices_success_partial_and_failure(self) -> None:
        stocks = [
            {"article": "10 / S", "barcode": "bc", "name": "A"},
            {"article": "custom", "barcode": "other", "name": "B"},
        ]
        prices = [{"nmID": 10, "sizes": [{"techSizeName": "S", "price": 100, "discountedPrice": 80}]}]
        orders = [
            {
                "finishedPrice": 75,
                "barcode": "bc",
                "nmId": 10,
                "techSize": "S",
                "lastChangeDate": "2026-08-01",
            }
        ]
        with (
            mock.patch.object(unit_economics.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(unit_economics.db, "get_stock_items", return_value=stocks),
            mock.patch.object(unit_economics.wb_api, "get_products_with_prices", return_value=prices),
            mock.patch.object(unit_economics.wb_api, "get_orders", return_value=orders),
            mock.patch.object(unit_economics.db, "get_wb_price_last_sync", return_value=None),
            mock.patch.object(unit_economics.db, "upsert_wb_unit_prices", return_value=2) as upsert,
        ):
            report = unit_economics.sync_prices_store("store")
        self.assertEqual(report["rows"], 2)
        self.assertEqual(report["buyer_prices"], 1)
        self.assertEqual(upsert.call_args.args[1][0]["buyer_price"], 75)

        with (
            mock.patch.object(unit_economics.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(unit_economics.db, "get_stock_items", return_value=stocks),
            mock.patch.object(
                unit_economics.wb_api, "get_products_with_prices", side_effect=ValueError("prices")
            ),
            mock.patch.object(unit_economics.wb_api, "get_orders", return_value=orders),
            mock.patch.object(unit_economics.db, "get_wb_price_last_sync", return_value="bad"),
            mock.patch.object(unit_economics.db, "upsert_wb_unit_prices", return_value=2),
        ):
            report = unit_economics.sync_prices_store("store", force_full=True)
        self.assertEqual(len(report["warnings"]), 1)

        with (
            mock.patch.object(unit_economics.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(unit_economics.db, "get_stock_items", return_value=stocks),
            mock.patch.object(
                unit_economics.wb_api, "get_products_with_prices", side_effect=ValueError("prices")
            ),
            mock.patch.object(unit_economics.wb_api, "get_orders", side_effect=ValueError("orders")),
            mock.patch.object(unit_economics.db, "get_wb_price_last_sync", return_value=None),
        ):
            with self.assertRaises(RuntimeError):
                unit_economics.sync_prices_store("store")

    def test_sync_references(self) -> None:
        stocks = [{"article": "10 / S"}, {"article": "custom"}]
        cards = [
            {
                "nmID": 10,
                "subjectID": 2,
                "subjectName": "Category",
                "dimensions": {"length": 10, "width": 20, "height": 30, "weightBrutto": 1.2},
            }
        ]
        commissions = [{"subjectID": 2, "kgvpSupplier": 15}, {"subjectID": None}]
        with (
            mock.patch.object(unit_economics.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(unit_economics.db, "get_stock_items", return_value=stocks),
            mock.patch.object(unit_economics.wb_api, "get_cards_list", return_value=cards),
            mock.patch.object(unit_economics.wb_api, "get_category_commissions", return_value=commissions),
            mock.patch.object(unit_economics.db, "upsert_wb_unit_references", return_value=2) as upsert,
        ):
            report = unit_economics.sync_reference_store("store")
        self.assertEqual(report["with_volume"], 1)
        self.assertEqual(report["with_commission"], 1)
        self.assertEqual(upsert.call_args.args[1][0]["volume_l"], 6)

    def test_sync_all_wrappers(self) -> None:
        def service(slug):
            if slug == "bad":
                raise ValueError("boom")
            return {"rows": 1, "updated_at": "now"}

        with (
            mock.patch.object(unit_economics, "STORES", {"good": {}, "bad": {}, "none": {}}),
            mock.patch.object(unit_economics.wb_tokens, "has_token", side_effect=lambda slug: slug != "none"),
            mock.patch.object(unit_economics.db, "record_sync_health") as health,
        ):
            report = unit_economics._sync_all(service, "scope")
        self.assertTrue(report["good"]["ok"])
        self.assertFalse(report["bad"]["ok"])
        self.assertEqual(health.call_count, 2)
        with mock.patch.object(unit_economics, "_sync_all", return_value={}) as sync_all:
            self.assertEqual(unit_economics.sync_prices_all(), {})
            self.assertEqual(unit_economics.sync_references_all(), {})
        self.assertEqual(sync_all.call_count, 2)

    def test_load_fbs_data_warnings_and_price_freshness(self) -> None:
        fresh = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        stale = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        self.assertTrue(
            unit_economics._fresh_buyer_price({"buyer_price": 10, "buyer_price_observed_at": fresh})
        )
        self.assertFalse(
            unit_economics._fresh_buyer_price({"buyer_price": 10, "buyer_price_observed_at": stale})
        )
        stocks = [
            {"article": "10 / S", "name": "A", "fbs_stock": 2, "fbo_stock": 1},
            {"article": "11", "name": "B", "fbs_stock": 0, "fbo_stock": 0},
            {"article": "12", "name": "C", "fbs_stock": 0, "fbo_stock": 0},
        ]
        metrics = {
            "10 / S": {
                "buyer_price": 90,
                "buyer_price_observed_at": fresh,
                "volume_l": 1,
                "commission_fbs_rate": 10,
            },
            "11": {"buyer_price": 80, "buyer_price_observed_at": stale, "discounted_price": 70},
        }
        costs = {"10": {"purchase_price": 30, "updated_at": "now"}}
        with (
            mock.patch.object(unit_economics.db, "get_stock_items", return_value=stocks),
            mock.patch.object(unit_economics.db, "get_wb_unit_metrics", return_value=metrics),
            mock.patch.object(unit_economics.db, "get_unit_costs", return_value=costs),
        ):
            result = unit_economics.load_wb_fbs_data("store")
        self.assertEqual(result["rows"][0]["price_source"], "finishedPrice")
        self.assertEqual(result["rows"][1]["price_source"], "discountedPrice")
        self.assertEqual(result["rows"][2]["price_source"], "")
        self.assertEqual(len(result["warnings"]), 5)


if __name__ == "__main__":
    unittest.main()
