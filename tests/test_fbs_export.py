"""F09: synthetic API/Google responses and an isolated registry, never live services."""

import importlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


class FbsExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.export = importlib.import_module("app.exports.stock_sheet")
            cls.sales = importlib.import_module("app.stock.sales")
            cls.repo = importlib.import_module("app.repositories.sales")
            cls.core = importlib.import_module("app.repositories.core")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.orm = importlib.import_module("app.infrastructure.orm")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-f09-")
        self.addCleanup(directory.cleanup)
        self.database = self.database_module.Database(Path(directory.name) / "test.sqlite3")
        self.addCleanup(self.database.dispose)
        self.orm.OrmBase.metadata.create_all(self.database.engine)
        self.mock(self.core, "database_for_path", return_value=self.database)
        # Fail closed if a test accidentally forgets to mock an API response.
        self.mock(self.sales.yandex_tokens, "get_api_key", return_value="synthetic-key")
        self.mock(self.sales.yandex_tokens, "get_business_id", return_value=12)
        self.mock(self.sales.ozon_tokens, "get_credentials", return_value=("synthetic-id", "synthetic-key"))
        self.ym_request = self.mock(
            self.sales.yandex_api, "_request", side_effect=AssertionError("Unmocked API")
        )
        self.oz_request = self.mock(
            self.sales.ozon_api, "_request", side_effect=AssertionError("Unmocked API")
        )
        self.mock(self.sales.wb_tokens, "get_token", return_value="synthetic-key")
        self.mock(self.sales.wb_api, "get_fbs_orders", side_effect=AssertionError("Unmocked WB"))
        self.mock(self.sales.wb_api, "get_fbs_order_statuses", side_effect=AssertionError("Unmocked WB"))
        self.now = datetime(2026, 9, 25, 0, 15, tzinfo=self.sales.MOSCOW)
        self.start, self.end = self.export.fbs_completed_period(self.now)
        self.google = MagicMock()
        self.google_factory = self.mock(self.export, "_google_service", return_value=self.google)
        self.mock(self.export, "_sheet_metadata", return_value={"Orders": 1, "Stocks": 2})
        self.mock(self.export, "_check_timestamp_cells")
        self.timestamp = self.mock(self.export, "_write_export_timestamp", return_value=self.now.isoformat())

    def mock(self, obj, name, **kwargs):
        mocker = patch.object(obj, name, **kwargs)
        value = mocker.start()
        self.addCleanup(mocker.stop)
        return value

    @staticmethod
    def ym_order(
        order_id=1, quantity=3, status="PROCESSING", program="FBS", created="2026-09-01T01:00:00+03:00"
    ):
        return {
            "orderId": order_id,
            "creationDate": created,
            "programType": program,
            "status": status,
            "items": [{"id": 11, "offerId": "Article-A", "count": quantity, "buyerPrice": 100}],
        }

    @staticmethod
    def oz_order(order_id="1-1", quantity=3, status="awaiting_packaging", created="2026-09-01T00:00:00Z"):
        return {
            "posting_number": order_id,
            "in_process_at": created,
            "status": status,
            "products": [{"sku": 11, "offer_id": "Article-A", "quantity": quantity, "price": "100"}],
        }

    def ym_pages(self, *responses):
        self.ym_request.side_effect = list(responses)

    def oz_pages(self, *responses):
        self.oz_request.side_effect = list(responses)

    def refresh(self, marketplace):
        statuses = (
            self.export.YANDEX_FBS_EXPORT_STATUSES
            if marketplace == "YANDEX MARKET"
            else self.export.OZON_FBS_EXPORT_STATUSES
        )
        return self.sales.refresh_fbs_order_totals(
            "rimili", marketplace, self.start, self.end, tuple(statuses)
        )

    def rows(self):
        with self.database.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM sales_order_lines ORDER BY order_key, line_key"
                ).fetchall()
            ]

    def configure(self, marketplace, stores=("rimili",), *, stocks=False, urls=None):
        settings = []
        for slug in stores:
            item = self.export.default_settings(slug, self.now)
            item = replace(
                item,
                enabled=True,
                targets=tuple(
                    replace(
                        target,
                        sheet_name=(
                            "Orders" if target.metric == "fbs_orders" else ("Stocks" if stocks else "")
                        )
                        if target.marketplace == marketplace
                        else "",
                    )
                    for target in item.targets
                ),
                spreadsheets=tuple(
                    replace(
                        sheet,
                        spreadsheet_url=(urls or {}).get(
                            slug, "https://docs.google.com/spreadsheets/d/test-doc/edit"
                        ),
                    )
                    for sheet in item.spreadsheets
                ),
            )
            settings.append(item)
        by_slug = {item.store_slug: item for item in settings}
        self.mock(self.export, "get_settings", side_effect=by_slug.__getitem__)
        self.mock(self.export, "list_settings", return_value=settings)
        return settings

    def assert_no_google_writes(self):
        self.google.spreadsheets().values().batchClear.assert_not_called()
        self.google.spreadsheets().values().batchUpdate.assert_not_called()
        self.timestamp.assert_not_called()

    def test_new_registry_yandex_all_pages_before_google(self):
        self.configure("YANDEX MARKET")
        self.ym_pages(
            {"orders": [self.ym_order()], "paging": {"nextPageToken": "second"}},
            {"orders": [self.ym_order(2, quantity=7)]},
        )
        self.google.spreadsheets().values().batchClear.side_effect = lambda **kw: (
            self.assertEqual(self.ym_request.call_count, 2) or MagicMock()
        )
        report = self.export.export_store(
            "rimili", self.now, marketplace="YANDEX MARKET", export_kind="fbs_orders"
        )
        update = self.google.spreadsheets().values().batchUpdate.call_args.kwargs["body"]
        self.assertEqual(
            update["data"][0]["values"], [list(self.export.ORDER_EXPORT_HEADERS), ["Article-A", 10]]
        )
        self.assertEqual(report["marketplaces"][0]["fbs_orders"]["rows"], 1)
        self.assertEqual(len(self.rows()), 2)
        payload = self.ym_request.call_args_list[0].kwargs["payload"]
        self.assertEqual(payload["dates"], {"creationDateFrom": "2026-08-26", "creationDateTo": "2026-09-25"})
        self.assertNotIn("statuses", payload)
        self.assertEqual(self.ym_request.call_args_list[1].kwargs["params"]["pageToken"], "second")

    def test_ozon_30_days_moscow_boundaries_and_short_pages(self):
        self.oz_pages(
            {"postings": [self.oz_order(created="2026-08-25T21:00:00Z")], "has_next": True, "cursor": "next"},
            {
                "postings": [
                    self.oz_order("2", 4, created="2026-08-30T00:00:00Z"),
                    self.oz_order("3", 5, created="2026-09-24T20:59:59Z"),
                    self.oz_order("4", 20, created="2026-09-24T21:00:00Z"),
                    self.oz_order("5", 20, created="2026-08-25T20:59:59Z"),
                ],
                "has_next": False,
            },
        )
        self.assertEqual(self.refresh("OZON"), {"Article-A": 12})
        payload = self.oz_request.call_args_list[0].args[3]
        self.assertEqual(
            datetime.fromisoformat(payload["filter"]["since"]), datetime(2026, 8, 25, 21, tzinfo=UTC)
        )
        self.assertEqual(
            datetime.fromisoformat(payload["filter"]["to"]),
            datetime(2026, 9, 24, 21, tzinfo=UTC) - timedelta(microseconds=1),
        )
        self.assertEqual(self.oz_request.call_args_list[1].args[3]["cursor"], "next")
        self.assertTrue(
            all(call.args[0] == "/v4/posting/fbs/list" for call in self.oz_request.call_args_list)
        )

    def test_status_changes_quantities_idempotence_and_removed_items(self):
        for marketplace, factory in (("YANDEX MARKET", self.ym_order), ("OZON", self.oz_order)):
            with self.subTest(marketplace=marketplace):

                def respond(orders, marketplace=marketplace):
                    if marketplace == "OZON":
                        self.oz_pages({"postings": orders, "has_next": False})
                    else:
                        self.ym_pages({"orders": orders})

                respond([factory(1, 9), factory(2, 6), factory(3, 5), factory(4, 2)])
                self.assertEqual(self.refresh(marketplace), {"Article-A": 22})
                updated = [
                    factory(1, 2),
                    factory(2, 6, "DELIVERED" if marketplace == "YANDEX MARKET" else "delivered"),
                    factory(3, 5, "CANCELLED" if marketplace == "YANDEX MARKET" else "cancelled"),
                ]
                for _ in range(2):
                    respond(updated)
                    self.assertEqual(self.refresh(marketplace), {"Article-A": 2})
                self.assertEqual(len([row for row in self.rows() if row["marketplace"] == marketplace]), 3)

    def test_scheme_and_allowed_status_filters_keep_unit_counts(self):
        orders = [
            self.ym_order(index + 1, 2, status=status)
            for index, status in enumerate(sorted(self.export.YANDEX_FBS_EXPORT_STATUSES))
        ]
        orders.extend(
            [
                self.ym_order(100, 50, program="FBY"),
                self.ym_order(101, 50, status="DELIVERED"),
                self.ym_order(102, 0),
            ]
        )
        self.ym_pages({"orders": orders})
        self.assertEqual(self.refresh("YANDEX MARKET"), {"Article-A": 14})

    def test_unprovided_accounting_fields_are_preserved(self):
        line = self.sales._normalize_ozon("rimili", [self.oz_order()], "fbs")[0]
        line.update(
            barcode="known-barcode",
            name="Known name",
            return_quantity=2,
            return_amount=120,
            returned_at="2026-09-02",
            sale_amount=70,
            sold_at="2026-09-03",
            source_updated_at="2026-09-04",
        )
        self.repo.upsert_sales_order_lines([line], "old")
        self.oz_pages({"postings": [self.oz_order(quantity=1)], "has_next": False})
        self.assertEqual(self.refresh("OZON"), {"Article-A": 1})
        current = self.rows()[0]
        for field in (
            "barcode",
            "name",
            "order_amount",
            "sale_amount",
            "sold_at",
            "return_quantity",
            "return_amount",
            "returned_at",
            "source_updated_at",
        ):
            self.assertEqual(current[field], line[field], field)

    def test_empty_complete_response_clears_only_requested_slice_and_publishes(self):
        seed = self.sales._normalize_yandex(
            "rimili",
            [
                self.ym_order(),
                self.ym_order(2, program="FBY"),
                self.ym_order(3, created="2026-08-01T00:00:00+03:00"),
            ],
        )
        seed += self.sales._normalize_yandex("tris", [self.ym_order()])
        seed += self.sales._normalize_ozon("rimili", [self.oz_order()], "fbs")
        self.repo.upsert_sales_order_lines(seed, "old")
        self.configure("YANDEX MARKET")
        self.ym_pages({"orders": []})
        self.export.export_store("rimili", self.now, marketplace="YANDEX MARKET", export_kind="fbs_orders")
        self.assertEqual(len(self.rows()), 4)
        self.assertEqual(
            self.google.spreadsheets().values().batchUpdate.call_args.kwargs["body"]["data"][0]["values"],
            [list(self.export.ORDER_EXPORT_HEADERS)],
        )
        self.timestamp.assert_called_once()

    def test_incomplete_pages_leave_registry_and_google_untouched(self):
        for marketplace in ("YANDEX MARKET", "OZON"):
            with self.subTest(marketplace=marketplace):
                seed = (
                    self.sales._normalize_yandex("rimili", [self.ym_order()])
                    if marketplace == "YANDEX MARKET"
                    else self.sales._normalize_ozon("rimili", [self.oz_order()], "fbs")
                )
                self.repo.upsert_sales_order_lines(seed, "old")
                before = self.rows()
                self.configure(marketplace, stocks=True)
                if marketplace == "YANDEX MARKET":
                    self.ym_pages(
                        {"orders": [self.ym_order(8)], "paging": {"nextPageToken": "next"}},
                        {"orders": [], "paging": {"nextPageToken": "more"}},
                    )
                else:
                    self.oz_pages(
                        {"postings": [self.oz_order("8")], "has_next": True, "cursor": "next"},
                        {"postings": [], "has_next": True, "cursor": "more"},
                    )
                with self.assertRaisesRegex(self.export.StockSheetExportError, marketplace):
                    self.export.export_store("rimili", self.now, marketplace=marketplace)
                self.assertEqual(self.rows(), before)
                self.assert_no_google_writes()

    def test_missing_payload_unknown_completeness_and_repeated_cursors_fail(self):
        ym_cases = [
            ({},),
            ({"orders": None},),
            (
                {"orders": [self.ym_order()], "paging": {"nextPageToken": "x"}},
                {"orders": [self.ym_order(2)], "paging": {"nextPageToken": "x"}},
            ),
            ({"orders": [self.ym_order()], "paging": {"nextPageToken": "x"}}, {"orders": [self.ym_order()]}),
            ({"orders": [dict(self.ym_order(), creationDate="invalid")]},),
        ]
        oz_cases = [
            ({},),
            ({"postings": []},),
            ({"postings": [], "has_next": "false"},),
            ({"postings": [self.oz_order()], "has_next": True},),
            (
                {"postings": [self.oz_order()], "has_next": True, "cursor": "x"},
                {"postings": [self.oz_order("2")], "has_next": True, "cursor": "x"},
            ),
            (
                {"postings": [self.oz_order()], "has_next": True, "cursor": "x"},
                {"postings": [self.oz_order()], "has_next": False},
            ),
        ]
        for marketplace, cases, response in (
            ("YANDEX MARKET", ym_cases, self.ym_pages),
            ("OZON", oz_cases, self.oz_pages),
        ):
            for case in cases:
                with self.subTest(marketplace=marketplace, case=case):
                    response(*case)
                    with self.assertRaises(self.sales.FbsOrderRefreshError):
                        self.refresh(marketplace)
                    self.assertEqual(self.rows(), [])

    def test_combined_store_failure_blocks_before_stock_clear_and_masks_secrets(self):
        self.configure(
            "YANDEX MARKET",
            stores=("rockkiddo", "toyka"),
            stocks=True,
            urls={"toyka": "https://docs.google.com/spreadsheets/d/other-doc/edit"},
        )
        self.ym_pages({"orders": [self.ym_order()]}, RuntimeError("SECRET-KEY must not be exposed"))
        with self.assertRaises(self.export.StockSheetExportError) as caught:
            self.export.export_store("rockkiddo", self.now, marketplace="YANDEX MARKET")
        self.assertIn("toyka / YANDEX MARKET", str(caught.exception))
        self.assertIn("[2026-08-26, 2026-09-25)", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))
        self.google_factory.assert_not_called()
        self.assert_no_google_writes()

    def test_scheduler_reuses_verified_window_across_destinations(self):
        self.configure(
            "YANDEX MARKET",
            stores=("rockkiddo", "toyka"),
            urls={"toyka": "https://docs.google.com/spreadsheets/d/other-doc/edit"},
        )
        self.ym_pages({"orders": [self.ym_order()]}, {"orders": [self.ym_order()]})
        self.mock(self.export, "is_due", return_value=True)
        self.mock(self.export.repository, "record_attempt")
        self.mock(self.export.repository, "record_result")
        report = self.export.run_due(self.now)
        self.assertTrue(all(item["ok"] for item in report.values()))
        self.assertEqual(self.ym_request.call_count, 2)
        updates = self.google.spreadsheets().values().batchUpdate.call_args_list
        self.assertEqual([call.kwargs["body"]["data"][0]["values"][1][1] for call in updates], [6, 3])

    def test_failure_is_not_recorded_as_success_and_cached_for_same_run(self):
        self.configure("OZON", stores=("rimili", "tris"))
        self.oz_pages(RuntimeError("failed"))
        self.mock(self.export, "is_due", return_value=True)
        self.mock(self.export.repository, "record_attempt")
        records = self.mock(self.export.repository, "record_result")
        with self.assertRaisesRegex(
            self.export.StockSheetExportError, r"OZON / FBS \[2026-08-26, 2026-09-25\)"
        ):
            self.export.run_due(self.now)
        self.assertEqual(self.oz_request.call_count, 1)
        self.assertEqual(records.call_count, 2)
        self.assertTrue(all(call.kwargs["error"] for call in records.call_args_list))
        self.assert_no_google_writes()

    def test_stocks_only_does_not_fetch_orders(self):
        self.configure("OZON", stocks=True)
        self.mock(self.export, "_combined_stock_snapshot", return_value=([], {}, []))
        self.mock(self.export, "_write_marketplace", return_value={"updated_cells": 0})
        self.export.export_store("rimili", self.now, marketplace="OZON", export_kind="stocks")
        self.ym_request.assert_not_called()
        self.oz_request.assert_not_called()

    def test_wb_direct_orders_statuses_and_moscow_period_are_unchanged(self):
        orders = self.mock(
            self.sales.wb_api,
            "get_fbs_orders",
            return_value=[{"id": 1, "nmId": 123}, {"id": 2, "nmId": 123}, {"id": 3, "nmId": 123}],
        )
        statuses = self.mock(
            self.sales.wb_api,
            "get_fbs_order_statuses",
            return_value={
                1: {"supplierStatus": "new", "wbStatus": "waiting"},
                2: {"supplierStatus": "complete", "wbStatus": "sorted"},
                3: {"supplierStatus": "cancel", "wbStatus": "sold"},
            },
        )
        self.assertEqual(self.export._combined_fbs_order_totals(("rimili",), "WB", now=self.now), {"123": 2})
        self.assertEqual(orders.call_args.args[1:], self.export._unix_bounds(self.start, self.end))
        statuses.assert_called_once_with("synthetic-key", [1, 2, 3])
        self.assertEqual(self.rows(), [])

    def test_moscow_period_uses_export_instant_even_across_utc_day(self):
        self.assertEqual(
            self.export.fbs_completed_period(datetime(2026, 9, 24, 21, 15, tzinfo=UTC)),
            (date(2026, 8, 26), date(2026, 9, 25)),
        )
        self.assertEqual(
            self.export.fbs_completed_period(datetime(2026, 9, 24, 20, 59, tzinfo=UTC)),
            (date(2026, 8, 25), date(2026, 9, 24)),
        )

    def test_snapshot_failure_rolls_back_removed_rows(self):
        self.ym_pages({"orders": [self.ym_order()]})
        self.refresh("YANDEX MARKET")
        before = self.rows()
        self.ym_pages({"orders": [self.ym_order(2)]})
        self.mock(self.repo, "_fbs_order_totals", side_effect=RuntimeError("database read failed"))
        with self.assertRaises(self.sales.FbsOrderRefreshError):
            self.refresh("YANDEX MARKET")
        self.assertEqual(self.rows(), before)

    def test_postgres_advisory_locks_share_scopes_and_use_driver_parameters(self):
        driver = MagicMock()
        conn = self.database_module.DatabaseConnection(driver, "postgresql")
        self.repo._lock_sales_scopes(conn, [("tris", "OZON"), ("rimili", "OZON"), ("rimili", "OZON")])
        calls = driver.exec_driver_sql.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0].args, ("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))", ("rimili", "OZON"))
        )

    def test_missing_item_and_reduced_quantity_reconcile_one_order(self):
        order = self.ym_order(quantity=8)
        order["items"].append({"id": 12, "offerId": "Article-B", "count": 5})
        self.ym_pages({"orders": [order]})
        self.assertEqual(self.refresh("YANDEX MARKET"), {"Article-A": 8, "Article-B": 5})
        self.ym_pages({"orders": [self.ym_order(quantity=2)]})
        self.assertEqual(self.refresh("YANDEX MARKET"), {"Article-A": 2})
        self.assertEqual(len(self.rows()), 1)

    def test_every_window_must_finish_before_reconciliation(self):
        for marketplace in ("OZON", "YANDEX MARKET"):
            with self.subTest(marketplace=marketplace):
                before = self.rows()
                windows = {"OZON": 7, "YANDEX MARKET": 7}
                with patch.dict(self.sales.SOURCE_WINDOW_DAYS, windows):
                    if marketplace == "OZON":
                        self.oz_pages({"postings": [], "has_next": False}, TimeoutError("last window failed"))
                    else:
                        self.ym_pages({"orders": []}, TimeoutError("last window failed"))
                    with self.assertRaises(self.sales.FbsOrderRefreshError):
                        self.refresh(marketplace)
                self.assertEqual(self.rows(), before)
        self.assertEqual(
            self.ym_request.call_args_list[1].kwargs["payload"]["dates"],
            {
                "creationDateFrom": "2026-09-02",
                "creationDateTo": "2026-09-09",
            },
        )

    def test_complete_empty_ozon_is_valid_but_missing_quantity_is_not(self):
        self.oz_pages({"postings": [], "has_next": False})
        self.assertEqual(self.refresh("OZON"), {})
        for quantity in (None, -1, 1.5, True):
            with self.subTest(quantity=quantity):
                self.oz_pages({"postings": [self.oz_order(quantity=quantity)], "has_next": False})
                with self.assertRaises(self.sales.FbsOrderRefreshError):
                    self.refresh("OZON")
        self.assertEqual(self.rows(), [])

    def test_totals_are_from_validated_snapshot_even_if_registry_changes_later(self):
        self.configure("YANDEX MARKET")
        self.ym_pages({"orders": [self.ym_order(quantity=3)]})

        def later_job(**kwargs):
            rows = self.sales._normalize_yandex("rimili", [self.ym_order(quantity=99)])
            self.repo.upsert_sales_order_lines(rows, "later")
            return MagicMock()

        self.google.spreadsheets().values().batchClear.side_effect = later_job
        self.export.export_store("rimili", self.now, marketplace="YANDEX MARKET", export_kind="fbs_orders")
        self.assertEqual(self.rows()[0]["quantity"], 99)
        values = self.google.spreadsheets().values().batchUpdate.call_args.kwargs["body"]["data"][0]["values"]
        self.assertEqual(values[1], ["Article-A", 3])

    def test_scheduler_fixes_time_once_when_crossing_midnight(self):
        self.configure("YANDEX MARKET", stores=("rockkiddo", "toyka"))
        self.ym_pages({"orders": []}, {"orders": []})
        self.mock(self.export, "is_due", return_value=True)
        self.mock(self.export.repository, "record_attempt")
        self.mock(self.export.repository, "record_result")
        clock = self.mock(self.export, "datetime", wraps=datetime)
        clock.now.side_effect = [self.now - timedelta(minutes=30), self.now]
        self.export.run_due()
        clock.now.assert_called_once()
        self.assertTrue(
            all(
                call.kwargs["payload"]["dates"]
                == {
                    "creationDateFrom": "2026-08-25",
                    "creationDateTo": "2026-09-24",
                }
                for call in self.ym_request.call_args_list
            )
        )

    def test_export_does_not_guess_yandex_business_when_key_has_multiple(self):
        self.mock(self.sales.yandex_tokens, "get_business_id", return_value=None)
        self.mock(
            self.sales.yandex_api,
            "get_campaigns",
            return_value=[
                {"business": {"id": 111}},
                {"business": {"id": 222}},
            ],
        )
        with self.assertRaisesRegex(self.sales.FbsOrderRefreshError, "business_id"):
            self.refresh("YANDEX MARKET")
        self.ym_request.assert_not_called()
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
