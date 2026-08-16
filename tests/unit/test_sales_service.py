import io
import logging
import unittest
import zipfile
from datetime import date, timedelta
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
            mock.patch.object(sales, "_normalize_wb", return_value=[{"line": 1}]),
        ):
            lines, warnings = sales._sync_wb("store", date(2026, 8, 1), date(2026, 8, 2))
        self.assertEqual(lines, [{"line": 1}])
        self.assertEqual(warnings, [])
        with (
            mock.patch.object(sales.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(sales.wb_api, "get_orders", return_value=[]),
            mock.patch.object(sales.wb_api, "get_sales", side_effect=ValueError("sales")),
            mock.patch.object(sales, "_normalize_wb", return_value=[]),
        ):
            _, warnings = sales._sync_wb("store", date(2026, 8, 1), date(2026, 8, 2))
        self.assertEqual(len(warnings), 1)

        with (
            mock.patch.object(sales.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(sales.ozon_api, "get_fbo_postings", return_value=[{"id": 1}]),
            mock.patch.object(sales.ozon_api, "get_fbs_postings", return_value=[{"id": 2}]),
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
            mock.patch.object(sales.ozon_api, "get_fbs_postings", side_effect=ValueError("bad")),
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

    def test_period_validation_and_series_helpers(self) -> None:
        today = date.today()
        start = today - timedelta(days=2)
        self.assertEqual(sales.parse_period(start.isoformat(), today.isoformat(), "wb"), (start, today))
        invalid = [
            (start.isoformat(), today.isoformat(), "wrong"),
            ("bad", today.isoformat(), "WB"),
            (today.isoformat(), start.isoformat(), "WB"),
            ((today - timedelta(days=500)).isoformat(), today.isoformat(), "WB"),
            (today.isoformat(), (today + timedelta(days=1)).isoformat(), "WB"),
        ]
        for date_from, date_to, marketplace in invalid:
            with self.assertRaises(ValueError):
                sales.parse_period(date_from, date_to, marketplace)
        rows = [{"day": start.isoformat(), "orders_amount": 100, "orders_count": 2}]
        series = sales._daily_map(start, today, rows)
        self.assertEqual(len(series), 3)
        self.assertEqual(sales._sum_series(series, "orders"), 100)
        self.assertEqual(sales._delta(120, 100), 20)
        self.assertIsNone(sales._delta(1, 0))

    def test_dashboard(self) -> None:
        today = date.today()
        start = today - timedelta(days=1)
        current = [
            {
                "day": start.isoformat(),
                "orders_amount": 1000,
                "fbo_amount": 600,
                "fbo_count": 6,
                "fbs_amount": 400,
                "fbs_count": 4,
                "cancellations_amount": 100,
                "sales_amount": 800,
                "orders_count": 10,
                "cancellations_count": 2,
                "sales_count": 8,
            }
        ]
        previous = [
            {"day": (start - timedelta(days=2)).isoformat(), "orders_amount": 500, "sales_amount": 400}
        ]
        with (
            mock.patch.object(sales, "STORES", {"store": {}}),
            mock.patch.object(sales.db, "get_sales_daily", side_effect=[current, previous]),
            mock.patch.object(
                sales.db,
                "get_sales_sync_states",
                return_value=[{"store_slug": "store", "ok": 0, "last_success_at": "now"}],
            ),
            mock.patch.object(
                sales.db, "get_sales_available_range", return_value={"date_from": "x", "date_to": "y"}
            ),
            mock.patch.object(sales, "_configured_stores", return_value=["store"]),
        ):
            result = sales.dashboard(start.isoformat(), today.isoformat(), "WB", "store")
        self.assertEqual(result["totals"]["orders_amount"], 1000)
        self.assertEqual(result["series"][0]["fbo_count"], 6)
        self.assertEqual(result["totals"]["cancel_rate"], 16.7)
        self.assertEqual(result["sync"]["errors"], 1)
        with mock.patch.object(sales, "STORES", {"store": {}}):
            with self.assertRaises(ValueError):
                sales.dashboard(start.isoformat(), today.isoformat(), "WB", "wrong")

    def test_export_xlsx_and_xml_helpers(self) -> None:
        today = date.today()
        row = {
            key: (100 if key in {"order_amount", "sale_amount"} else "value")
            for key, _ in sales.EXPORT_HEADERS
        }
        with (
            mock.patch.object(sales, "STORES", {"store": {}}),
            mock.patch.object(sales.db, "get_sales_export_rows", return_value=[row]),
        ):
            content = sales.export_xlsx(today.isoformat(), today.isoformat(), "WB", "store")
        self.assertTrue(content.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("autoFilter", sheet)
        self.assertEqual(sales._column_name(27), "AA")
        self.assertIn("<v>10</v>", sales._xlsx_cell("A1", 10))
        self.assertIn("&lt;", sales._xlsx_cell("A1", "<x>"))
        with mock.patch.object(sales, "STORES", {"store": {}}):
            with self.assertRaises(ValueError):
                sales.export_xlsx(today.isoformat(), today.isoformat(), "WB", "wrong")


if __name__ == "__main__":
    unittest.main()
