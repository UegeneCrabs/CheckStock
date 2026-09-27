"""Stock export checks with an isolated database and synthetic Google responses."""

import importlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


class StockSheetExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.export = importlib.import_module("app.exports.stock_sheet")
            cls.core = importlib.import_module("app.repositories.core")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.orm = importlib.import_module("app.infrastructure.orm")
            cls.routes = importlib.import_module("app.web.routers.google_export")

    def mock(self, obj, name, **kwargs):
        mocker = patch.object(obj, name, **kwargs)
        value = mocker.start()
        self.addCleanup(mocker.stop)
        return value

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-ff-export-")
        self.addCleanup(directory.cleanup)
        self.database = self.database_module.Database(Path(directory.name) / "test.sqlite3")
        self.addCleanup(self.database.dispose)
        self.orm.OrmBase.metadata.create_all(self.database.engine)
        self.mock(self.core, "database_for_path", return_value=self.database)
        self.now = datetime(2026, 9, 27, 12, tzinfo=self.export.MOSCOW_TIMEZONE)
        self.google = MagicMock()
        self.mock(self.export, "_google_service", return_value=self.google)
        self.sheet_name = "Stocks"
        self.sheet = {
            "properties": {"sheetId": 12, "title": self.sheet_name, "gridProperties": {"columnCount": 26}}
        }
        self.google.spreadsheets().get.return_value.execute.return_value = {"sheets": [self.sheet]}
        self.sheet_values = {}
        self.google.spreadsheets().values().get.side_effect = lambda **kw: MagicMock(
            execute=MagicMock(return_value={"values": self.sheet_values.get(kw["range"], [])})
        )
        self.timestamp = self.mock(self.export, "_write_export_timestamp", return_value=self.now.isoformat())
        self.orders = self.mock(
            self.export,
            "_combined_fbs_order_totals",
            side_effect=AssertionError("Orders must not be fetched"),
        )
        self.inbound = self.mock(
            self.export.stock_sheet_inbound,
            "load",
            side_effect=lambda store, marketplace, catalog, **kw: (
                self.export.stock_sheet_inbound.InboundExport(
                    catalog=list(catalog), quantities={row["article"]: 0 for row in catalog}, available=True
                )
            ),
        )
        self.settings = self.configure("rimili")

    def configure(self, slug):
        settings = self.export.default_settings(slug, self.now)
        return replace(
            settings,
            targets=tuple(
                replace(target, sheet_name=self.sheet_name if target.metric != "fbs_orders" else "")
                for target in settings.targets
            ),
            spreadsheets=tuple(
                replace(sheet, spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{slug}-test/edit")
                for sheet in settings.spreadsheets
            ),
        )

    def seed(self, *, store="rimili", marketplace="WB", article="Article-A", stocks=()):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO stock_items (store_slug, marketplace, article, barcode, name, mp_sku) "
                "VALUES (?, ?, ?, '1234567890', 'Test product', 'sku-a')",
                (store, marketplace, article),
            )
            for fulfillment, source_article, quantity in stocks:
                conn.execute(
                    "INSERT INTO ff_stock (store_slug, marketplace, article, fulfillment, quantity) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (store, marketplace, source_article, fulfillment, quantity),
                )
            conn.commit()

    def ff_metric(self, fulfillment):
        return f"{self.export.FF_STOCK_METRIC_PREFIX}{fulfillment}"

    def ff_header(self, fulfillment):
        return f"{self.export.FF_STOCK_HEADER_PREFIX}{fulfillment}"

    def write(self, metrics=None, catalog=None):
        return self.export._write_marketplace(
            self.google,
            "test-doc",
            self.settings,
            "WB",
            catalog
            if catalog is not None
            else [{"article": "Article-A", "barcode": "123", "name": "Product"}],
            metrics if metrics is not None else {self.ff_metric("Альфа"): {"Article-A": 3}},
        )

    def update(self):
        return self.google.spreadsheets().values().batchUpdate.call_args.kwargs["body"]["data"][0]

    def assert_no_writes(self):
        self.google.spreadsheets().values().batchClear.assert_not_called()
        self.google.spreadsheets().values().batchUpdate.assert_not_called()
        self.google.spreadsheets().batchUpdate.assert_not_called()
        self.timestamp.assert_not_called()

    def test_ff_total_and_details_share_alias_mapping_and_marketplace_scope(self):
        for marketplace in ("WB", "OZON", "YANDEX MARKET"):
            with self.subTest(marketplace=marketplace):
                self.seed(
                    marketplace=marketplace,
                    stocks=(("Альфа", "sku-a", 4), ("Альфа", "Article-A", 2), ("Бета", "article-a", 3)),
                )
                self.seed(store="tris", marketplace=marketplace, stocks=(("Альфа", "Article-A", 100),))
                catalog, metrics, _ = self.export._combined_stock_snapshot(
                    ("rimili",), marketplace, now=self.now
                )
                self.assertEqual([row["article"] for row in catalog], ["Article-A"])
                self.assertEqual(metrics["ff_stock"], {"Article-A": 9})
                self.assertEqual(metrics[self.ff_metric("Альфа")], {"Article-A": 6})
                self.assertEqual(metrics[self.ff_metric("Бета")], {"Article-A": 3})

    def test_public_export_combines_companion_stores_and_includes_empty_and_unlisted_centers(self):
        with self.database.connect() as conn:
            conn.execute("INSERT INTO fulfillments (name) VALUES ('Пустой ФФ')")
            conn.commit()
        self.seed(store="rockkiddo", stocks=(("Альфа", "Article-A", 4), ("Бета", "Article-A", 3)))
        self.seed(
            store="toyka",
            article="article-a",
            stocks=(("Альфа", "article-a", 2), ("Вне справочника", "article-a", 5)),
        )
        settings = [self.configure(slug) for slug in ("rockkiddo", "toyka")]
        self.mock(self.export, "get_settings", return_value=settings[0])
        self.mock(self.export, "list_settings", return_value=settings)
        report = self.export.export_store("rockkiddo", self.now, marketplace="WB", export_kind="stocks")
        values = self.update()["values"]
        self.assertEqual(
            values[0],
            [
                *self.export.EXPORT_HEADERS,
                *(self.ff_header(name) for name in ("Альфа", "Бета", "Вне справочника", "Пустой ФФ")),
            ],
        )
        self.assertEqual(values[1][3:], [14, 14, 0, 0, 0, 0, 6, 3, 5, 0])
        self.assertEqual(len(values), 2)
        self.assertEqual(report["marketplaces"][0]["store_slugs"], ("rockkiddo", "toyka"))
        self.assertEqual(report["marketplaces"][0]["updated_cells"], 28)
        self.orders.assert_not_called()

    def test_details_do_not_double_total_and_absent_stock_is_zero(self):
        metrics = {
            "ff_stock": {"Article-A": 8},
            "fbs_stock": {"Article-A": 10},
            "fbo_stock": {"Article-A": 20},
            "ff_transit": {"Article-A": 2},
            "mp_inbound": {"Article-A": 3, "B": 0},
            self.ff_metric("Бета"): {"Article-A": 5},
            self.ff_metric("Альфа"): {"Article-A": 3},
        }
        report = self.write(metrics, catalog=[{"article": "Article-A"}, {"article": "B"}])
        self.assertEqual(self.update()["range"], "'Stocks'!A2:K4")
        self.assertEqual(self.update()["values"][1][3:], [43, 8, 10, 20, 2, 3, 3, 5])
        self.assertEqual(self.update()["values"][2][3:], [0] * 8)
        self.assertEqual(report["updated_cells"], 35)

    def test_unavailable_inbound_keeps_ff_details_but_total_blank(self):
        self.seed(stocks=(("Альфа", "Article-A", 7),))
        self.inbound.side_effect = lambda store, marketplace, catalog, **kw: (
            self.export.stock_sheet_inbound.InboundExport(
                catalog=catalog, quantities={"Article-A": None}, available=False, warnings=("No snapshot",)
            )
        )
        catalog, metrics, warnings = self.export._combined_stock_snapshot(("rimili",), "WB", now=self.now)
        self.write(metrics, catalog)
        row = self.update()["values"][1]
        self.assertEqual((row[3], row[4], row[8], row[9]), ("", 7, "", 7))
        self.assertEqual(len(warnings), 1)

    def test_repeat_export_clears_old_rows_and_obsolete_center_columns(self):
        old_headers = [self.ff_header(name) for name in ("Альфа", "Бета", "Старый ФФ")]
        self.sheet_values["'Stocks'!2:2"] = [[*self.export.EXPORT_HEADERS, *old_headers]]
        self.sheet_values["'Stocks'!J2:L"] = [old_headers, [10, 20, 30], [40, 50, 60]]
        self.write({self.ff_metric("Новый ФФ"): {"Article-A": 1}, self.ff_metric("Альфа"): {}})
        self.assertEqual(
            self.google.spreadsheets().values().batchClear.call_args.kwargs["body"],
            {"ranges": ["'Stocks'!A2:Z"]},
        )
        self.assertEqual(self.update()["range"], "'Stocks'!A2:K3")

    def test_old_data_and_formulas_in_a2_z_are_replaced(self):
        for rows in ([["Custom"], [4]], [[], [4]], [['=""']], [[], ["=SUM(A1:A2)"]]):
            with self.subTest(rows=rows):
                for area in ("H2:I", "J2:J", "A2:Z"):
                    self.sheet_values[f"'Stocks'!{area}"] = rows
                self.write()
                self.assertEqual(
                    self.google.spreadsheets().values().batchClear.call_args.kwargs["body"],
                    {"ranges": ["'Stocks'!A2:Z"]},
                )
                self.assertEqual(self.update()["values"][1][-1], 3)

    def test_data_after_z_is_preserved_when_export_fits_in_z(self):
        self.sheet_values["'Stocks'!2:2"] = [[*self.export.EXPORT_HEADERS, *(["Custom"] * 18)]]
        self.write()
        self.assertEqual(
            self.google.spreadsheets().values().batchClear.call_args.kwargs["body"],
            {"ranges": ["'Stocks'!A2:Z"]},
        )

    def test_merged_cells_within_export_are_unmerged(self):
        self.sheet["merges"] = [
            {"startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 9, "endColumnIndex": 11}
        ]
        self.write()
        self.assertEqual(
            self.google.spreadsheets().batchUpdate.call_args.kwargs["body"],
            {"requests": [{"unmergeCells": {"range": {**self.sheet["merges"][0], "sheetId": 12}}}]},
        )

    def test_merges_crossing_export_boundary_preserve_outside_cells(self):
        for merged in (
            {"startRowIndex": 0, "endRowIndex": 3, "startColumnIndex": 9, "endColumnIndex": 11},
            {"startRowIndex": 1, "endRowIndex": 3, "startColumnIndex": 25, "endColumnIndex": 27},
        ):
            with self.subTest(merged=merged):
                self.sheet["merges"] = [merged]
                with self.assertRaisesRegex(self.export.StockSheetExportError, "выходят за диапазон"):
                    self.write()
                self.assert_no_writes()

    def test_clear_happens_before_current_data_is_written(self):
        values_api = self.google.spreadsheets().values()
        values_api.batchClear.side_effect = lambda **kw: (
            values_api.batchUpdate.assert_not_called() or MagicMock()
        )
        values_api.batchUpdate.side_effect = lambda **kw: (
            values_api.batchClear.assert_called_once() or MagicMock()
        )
        self.write()
        self.assertEqual(values_api.batchUpdate.call_args.kwargs["body"]["valueInputOption"], "RAW")

    def test_narrow_sheet_is_expanded_to_z_even_for_a_short_export(self):
        self.sheet["properties"]["gridProperties"]["columnCount"] = 9
        self.write()
        request = self.google.spreadsheets().batchUpdate.call_args.kwargs["body"]["requests"][0]
        self.assertEqual(request["updateSheetProperties"]["properties"]["gridProperties"]["columnCount"], 26)

    def test_details_expand_narrow_grid_and_support_columns_beyond_z(self):
        metrics = {self.ff_metric(f"ФФ {index:02d}"): {} for index in range(20)}
        self.sheet["properties"]["gridProperties"]["columnCount"] = 9
        self.write(metrics)
        self.assertEqual(self.update()["range"], "'Stocks'!A2:AC3")
        self.assertEqual(
            self.google.spreadsheets().batchUpdate.call_args.kwargs["body"],
            {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": 12, "gridProperties": {"columnCount": 29}},
                            "fields": "gridProperties.columnCount",
                        }
                    }
                ]
            },
        )
        reads = [call.kwargs["range"] for call in self.google.spreadsheets().values().get.call_args_list]
        self.assertNotIn("'Stocks'!AA2:AC", reads)

    def test_expansion_past_z_still_protects_unrelated_columns(self):
        self.sheet["properties"]["gridProperties"]["columnCount"] = 30
        self.sheet_values["'Stocks'!AA2:AC"] = [["Custom", "=SUM(A1:A2)"]]
        with self.assertRaisesRegex(self.export.StockSheetExportError, "за пределами A2:Z"):
            self.write({self.ff_metric(f"ФФ {index:02d}"): {} for index in range(20)})
        self.assert_no_writes()

    def test_empty_export_removes_previous_details_and_preserves_base_headers(self):
        self.sheet_values["'Stocks'!2:2"] = [[*self.export.EXPORT_HEADERS, self.ff_header("Удалённый ФФ")]]
        self.sheet_values["'Stocks'!J2:J"] = [[self.ff_header("Удалённый ФФ")], [7]]
        self.write({}, [])
        self.assertEqual(
            self.update(), {"range": "'Stocks'!A2:I2", "values": [list(self.export.EXPORT_HEADERS)]}
        )
        self.assertEqual(
            self.google.spreadsheets().values().batchClear.call_args.kwargs["body"],
            {"ranges": ["'Stocks'!A2:Z"]},
        )

    def test_scoped_success_clears_old_failure_without_postponing_schedule(self):
        self.seed(stocks=(("Альфа", "Article-A", 3),))
        old_attempt = "2026-09-21T09:00:00+03:00"
        self.export.repository.save_settings(
            replace(self.settings, last_attempt_at=old_attempt, last_error="Old H2:I failure")
        )
        self.export.run_store("rimili", self.now, marketplace="WB", export_kind="stocks")
        saved = self.export.repository.get_settings("rimili")
        self.assertIsNone(saved.last_error)
        self.assertEqual(saved.last_attempt_at, old_attempt)
        self.assertEqual(saved.last_success_at, self.now.isoformat())
        self.assertNotIn("Old H2:I failure", self.routes._render_store_card(saved, active=True))

    def test_legacy_error_is_hidden_when_success_is_newer_even_with_different_timezone(self):
        settings = replace(
            self.settings,
            last_error="Old H2:I failure",
            last_attempt_at="2026-09-21T09:00:00+03:00",
            last_success_at="2026-09-21T07:00:00Z",
        )
        self.assertNotIn("Old H2:I failure", self.routes._render_store_card(settings, active=True))
        current = replace(settings, last_attempt_at="2026-09-22T09:00:00+03:00")
        self.assertIn("Old H2:I failure", self.routes._render_store_card(current, active=True))

    def test_older_success_does_not_erase_newer_failure(self):
        self.export.repository.save_settings(
            replace(self.settings, last_attempt_at="2026-09-28T09:00:00+03:00", last_error="New failure")
        )
        self.export.repository.record_success("rimili", self.now.isoformat())
        self.assertEqual(self.export.repository.get_settings("rimili").last_error, "New failure")

    def test_failed_or_skipped_scoped_export_keeps_last_failure(self):
        self.export.repository.save_settings(
            replace(self.settings, last_attempt_at="2026-09-21T09:00:00+03:00", last_error="Old failure")
        )
        export = self.mock(self.export, "export_store", side_effect=RuntimeError("Write failed"))
        with self.assertRaisesRegex(RuntimeError, "Write failed"):
            self.export.run_store("rimili", self.now, marketplace="WB", export_kind="stocks")
        export.side_effect = None
        export.return_value = {"marketplaces": [{"skipped": True}]}
        self.export.run_store("rimili", self.now, marketplace="WB", export_kind="stocks")
        saved = self.export.repository.get_settings("rimili")
        self.assertEqual(saved.last_error, "Old failure")
        self.assertIsNone(saved.last_success_at)


if __name__ == "__main__":
    unittest.main()
