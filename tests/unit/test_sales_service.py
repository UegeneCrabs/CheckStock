import logging
import unittest
from datetime import date
from unittest import mock

from app import sales


class SalesServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_marketplace_loaders_success_and_failures(self) -> None:
        with (
            mock.patch.object(sales.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(sales.wb_api, "get_orders", return_value=[]),
            mock.patch.object(sales.wb_api, "get_sales", return_value=[]),
            mock.patch.object(sales, "_load_wb_fbs_lines", return_value=([], [])),
            mock.patch.object(sales, "_normalize_wb", return_value=[{"line": 1}]),
        ):
            lines, warnings = sales._sync_wb("store", date(2026, 8, 1), date(2026, 8, 2))
        self.assertEqual(lines, [{"line": 1}])
        self.assertEqual(warnings, [])
        with (
            mock.patch.object(sales.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(sales.wb_api, "get_orders", return_value=[]),
            mock.patch.object(sales.wb_api, "get_sales", side_effect=ValueError("sales")),
            mock.patch.object(sales, "_load_wb_fbs_lines", return_value=([], [])),
            mock.patch.object(sales, "_normalize_wb", return_value=[]),
        ):
            _, warnings = sales._sync_wb("store", date(2026, 8, 1), date(2026, 8, 2))
        self.assertEqual(len(warnings), 1)

        order = {
            "id": 10,
            "rid": "rid-10",
            "createdAt": "2026-08-10T10:00:00Z",
            "skus": ["4601"],
        }
        with (
            mock.patch.object(sales.wb_api, "get_fbs_orders", side_effect=[[order], [], []]) as orders,
            mock.patch.object(
                sales.wb_api,
                "get_fbs_order_statuses",
                return_value={10: {"id": 10, "supplierStatus": "new", "wbStatus": "waiting"}},
            ) as statuses,
        ):
            fbs_lines, fbs_warnings = sales._load_wb_fbs_lines(
                "store", "token", date(2026, 6, 1), date(2026, 8, 30)
            )
        self.assertEqual(orders.call_count, 3)
        statuses.assert_called_once_with("token", [10])
        self.assertEqual((fbs_lines[0]["status"], fbs_lines[0]["substatus"]), ("new", "waiting"))
        self.assertEqual(fbs_warnings, [])

        with (
            mock.patch.object(sales.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(sales.ozon_api, "get_fbo_postings", return_value=[{"id": 1}]),
            mock.patch.object(sales.ozon_api, "get_fbs_postings_v4", return_value=[{"id": 2}]),
            mock.patch.object(
                sales, "_normalize_ozon", side_effect=lambda _s, rows, scheme: [{"scheme": scheme, **rows[0]}]
            ),
        ):
            lines, warnings = sales._sync_ozon("store", date(2026, 8, 1), date(2026, 8, 2))
        self.assertEqual(len(lines), 2)
        self.assertEqual(warnings, [])
        with (
            mock.patch.object(sales.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(sales.ozon_api, "get_fbo_postings", side_effect=ValueError("bad")),
            mock.patch.object(sales.ozon_api, "get_fbs_postings_v4", side_effect=ValueError("bad")),
        ):
            with self.assertRaises(RuntimeError):
                sales._sync_ozon("store", date(2026, 8, 1), date(2026, 8, 2))

        with mock.patch.object(sales.yandex_tokens, "get_business_id", return_value=7):
            self.assertEqual(sales._resolve_yandex_business_id("store", "key"), 7)
        with (
            mock.patch.object(sales.yandex_tokens, "get_business_id", return_value=None),
            mock.patch.object(
                sales.yandex_api,
                "get_campaigns",
                return_value=[{"business": {"id": 8}}, {"businessId": 9}],
            ),
        ):
            self.assertEqual(sales._resolve_yandex_business_id("store", "key"), 8)
        with (
            mock.patch.object(sales.yandex_tokens, "get_business_id", return_value=None),
            mock.patch.object(sales.yandex_api, "get_campaigns", return_value=[]),
        ):
            with self.assertRaises(RuntimeError):
                sales._resolve_yandex_business_id("store", "key")

        with (
            mock.patch.object(sales.yandex_tokens, "get_api_key", return_value="key"),
            mock.patch.object(sales, "_resolve_yandex_business_id", return_value=7),
            mock.patch.object(sales.yandex_api, "get_business_orders", return_value=[{"id": 1}]),
            mock.patch.object(sales, "_normalize_yandex", return_value=[{"line": 1}]),
        ):
            lines, warnings = sales._sync_yandex("store", date(2026, 8, 1), date(2026, 8, 2))
        self.assertEqual(lines, [{"line": 1}])
        self.assertEqual(warnings, [])
        with (
            mock.patch.object(sales.yandex_tokens, "get_api_key", return_value="key"),
            mock.patch.object(sales, "_resolve_yandex_business_id", return_value=7),
            mock.patch.object(sales.yandex_api, "get_business_orders", side_effect=ValueError("bad")),
        ):
            with self.assertRaises(RuntimeError):
                sales._sync_yandex("store", date(2026, 8, 1), date(2026, 8, 2))

    def test_sync_store_success_warning_and_error(self) -> None:
        with (
            mock.patch.object(sales, "_sync_wb", return_value=([{"line": 1}], [])),
            mock.patch.object(sales.db, "upsert_sales_order_lines", return_value=1),
            mock.patch.object(sales.db, "record_sales_sync") as record,
            mock.patch.object(sales.db, "record_sync_health") as health,
        ):
            result = sales.sync_store("store", "WB", 5)
        self.assertTrue(result["ok"])
        record.assert_called_once()
        health.assert_called_once()

        with (
            mock.patch.object(sales, "_sync_ozon", return_value=([{"line": 1}], ["partial"])),
            mock.patch.object(sales.db, "upsert_sales_order_lines", return_value=1),
            mock.patch.object(sales.db, "record_sales_sync"),
            mock.patch.object(sales.db, "record_sync_health"),
        ):
            result = sales.sync_store("store", "OZON")
        self.assertFalse(result["ok"])
        self.assertEqual(result["warnings"], ["partial"])

        with (
            mock.patch.object(sales, "_sync_yandex", side_effect=ValueError("boom")),
            mock.patch.object(sales.db, "record_sales_sync") as failed_record,
            mock.patch.object(sales.db, "record_sync_health"),
        ):
            result = sales.sync_store("store", "YANDEX MARKET")
        self.assertFalse(result["ok"])
        self.assertIn("ValueError", result["error"])
        self.assertFalse(failed_record.call_args.args[2])

    def test_sync_store_period_keeps_only_order_dates_inside_half_open_period(self) -> None:
        lines = [
            {"ordered_at": "2026-07-31T23:59:59+03:00"},
            {"ordered_at": "2026-08-01T00:00:00+03:00"},
            {"ordered_at": "2026-08-31T23:59:59+03:00"},
            {"ordered_at": "2026-09-01T00:00:00+03:00"},
        ]
        with (
            mock.patch.object(sales, "_sync_wb", return_value=(lines, [])),
            mock.patch.object(sales.db, "upsert_sales_order_lines", return_value=2) as upsert,
            mock.patch.object(sales.db, "record_sales_sync") as record,
            mock.patch.object(sales.db, "record_sync_health"),
        ):
            result = sales.sync_store_period(
                "tris", "WB", date(2026, 8, 1), date(2026, 9, 1)
            )

        self.assertTrue(result["ok"])
        saved_lines = upsert.call_args.args[0]
        self.assertEqual(
            [line["ordered_at"] for line in saved_lines],
            ["2026-08-01T00:00:00+03:00", "2026-08-31T23:59:59+03:00"],
        )
        self.assertEqual(record.call_args.args[5], 31)

        with self.assertRaises(ValueError):
            sales.sync_store_period("tris", "WB", date(2026, 9, 1), date(2026, 9, 1))

    def test_configured_stores_and_sync_all(self) -> None:
        with mock.patch.object(sales, "STORES", {"a": {}, "b": {}}):
            with mock.patch.object(sales.wb_tokens, "has_token", side_effect=lambda slug: slug == "a"):
                self.assertEqual(sales._configured_stores("WB"), ["a"])
            with mock.patch.object(
                sales.ozon_tokens, "has_credentials", side_effect=lambda slug: slug == "b"
            ):
                self.assertEqual(sales._configured_stores("OZON"), ["b"])
            with mock.patch.object(sales.yandex_tokens, "has_credentials", return_value=True):
                self.assertEqual(sales._configured_stores("YANDEX MARKET"), ["a", "b"])

        with (
            mock.patch.object(sales, "MARKETPLACES", ("WB", "OZON")),
            mock.patch.object(
                sales, "_configured_stores", side_effect=lambda marketplace: [marketplace.lower()]
            ),
            mock.patch.object(
                sales.db, "sales_has_history", side_effect=lambda slug, _marketplace: slug == "wb"
            ),
            mock.patch.object(sales, "sync_store", return_value={"ok": True}) as sync,
        ):
            report = sales.sync_all(3)
        self.assertIn("wb", report["WB"])
        self.assertEqual(sync.call_args_list[0].args[2], 3)
        self.assertEqual(sync.call_args_list[1].args[2], sales.INITIAL_LOOKBACK_DAYS["OZON"])





if __name__ == "__main__":
    unittest.main()
