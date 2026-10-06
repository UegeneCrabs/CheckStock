"""Unified export contracts with temporary SQLite and synthetic Google responses."""

import importlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError


class ProjectSheetExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.export = importlib.import_module("app.exports.project_sheet")
            cls.legacy = importlib.import_module("app.exports.stock_sheet")
            cls.repository = importlib.import_module("app.repositories.project_sheet_export")
            cls.core = importlib.import_module("app.repositories.core")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.orm = importlib.import_module("app.infrastructure.orm")
            cls.stores = importlib.import_module("app.core.stores").STORES
            cls.locks = importlib.import_module("app.jobs.locks")
            cls.schema = importlib.import_module("app.repositories.schema")

    def mock(self, obj, name, **kwargs):
        mocker = patch.object(obj, name, **kwargs)
        value = mocker.start()
        self.addCleanup(mocker.stop)
        return value

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-project-export-")
        self.addCleanup(directory.cleanup)
        self.database = self.database_module.Database(Path(directory.name) / "test.sqlite3")
        self.addCleanup(self.database.dispose)
        self.orm.OrmBase.metadata.create_all(self.database.engine)
        self.mock(self.core, "database_for_path", return_value=self.database)
        self.mock(self.core, "DB_PATH", new=Path(directory.name) / "test.sqlite3")
        self.mock(self.locks, "database_for_path", return_value=self.database)
        self.now = datetime(2026, 10, 5, 12, tzinfo=self.legacy.MOSCOW_TIMEZONE)
        self.google = MagicMock()
        self.google_factory = self.mock(self.legacy, "_google_service", return_value=self.google)
        self.sheets = [
            {"properties": {"sheetId": index, "title": name, "gridProperties": {"columnCount": 26}}}
            for index, name in enumerate(
                ("Stocks", "Orders", "Ozon Stocks", "Ozon Orders", "YM Stocks", "YM Orders"), 1
            )
        ]
        self.google.spreadsheets().get.return_value.execute.return_value = {"sheets": self.sheets}
        self.sheet_values = {}
        self.google.spreadsheets().values().get.side_effect = lambda **kw: MagicMock(
            execute=MagicMock(return_value={"values": self.sheet_values.get(kw["range"], [])})
        )
        self.original_timestamp = self.legacy._write_export_timestamp
        self.timestamp = self.mock(self.legacy, "_write_export_timestamp", return_value=self.now.isoformat())
        self.orders = self.mock(self.legacy, "_combined_fbs_order_totals", return_value={})
        self.original_inbound_load = self.legacy.stock_sheet_inbound.load
        self.inbound = self.mock(
            self.legacy.stock_sheet_inbound,
            "load",
            side_effect=lambda store, marketplace, catalog, **kw: (
                self.legacy.stock_sheet_inbound.InboundExport(
                    catalog=list(catalog), quantities={row["article"]: 0 for row in catalog}, available=True
                )
            ),
        )

    def configure(self, *, stocks="Stocks", orders="", enabled=False, **changes):
        settings = self.export.default_settings(self.now - timedelta(days=1))
        settings = replace(
            settings,
            enabled=enabled,
            targets=tuple(
                replace(
                    target,
                    spreadsheet_url="https://docs.google.com/spreadsheets/d/test-doc/edit#gid=0"
                    if target.marketplace == "WB"
                    else "",
                    stock_sheet_name=stocks if target.marketplace == "WB" else "",
                    orders_sheet_name=orders if target.marketplace == "WB" else "",
                )
                for target in settings.targets
            ),
            **changes,
        )
        self.export.save_settings(settings)
        return self.export.get_settings()

    @staticmethod
    def destination(settings, marketplace, **changes):
        return replace(
            settings,
            targets=tuple(
                replace(target, **changes) if target.marketplace == marketplace else target
                for target in settings.targets
            ),
        )

    def seed(self, store, quantity, *, article="Shared-Article", marketplace="WB", barcode="1234567890"):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO stock_items (store_slug, marketplace, article, barcode, name, mp_sku) "
                "VALUES (?, ?, ?, ?, 'Synthetic product', ?)",
                (store, marketplace, article, barcode, f"{store}-sku"),
            )
            conn.execute(
                "INSERT INTO ff_stock (store_slug, marketplace, article, fulfillment, quantity) "
                "VALUES (?, ?, ?, 'Основной ФФ', ?)",
                (store, marketplace, article, quantity),
            )
            conn.commit()

    def updates(self):
        return [
            item
            for call in self.google.spreadsheets().values().batchUpdate.call_args_list
            for item in call.kwargs["body"]["data"]
        ]

    def update_for(self, sheet):
        return next(item for item in self.updates() if item["range"].startswith(f"'{sheet}'!A2:"))

    def assert_no_google_writes(self):
        self.google.spreadsheets().values().batchClear.assert_not_called()
        self.google.spreadsheets().values().batchUpdate.assert_not_called()
        self.google.spreadsheets().batchUpdate.assert_not_called()
        self.timestamp.assert_not_called()

    def test_first_use_is_disabled_and_does_not_adopt_old_store_destination(self):
        legacy = replace(self.legacy.default_settings("rimili", self.now), enabled=True)
        self.legacy.repository.save_settings(legacy)
        settings = self.export.get_settings()
        self.assertFalse(settings.enabled)
        self.assertEqual({target.spreadsheet_url for target in settings.targets}, {""})
        self.assertEqual({target.marketplace for target in settings.targets}, {"WB", "OZON", "YANDEX MARKET"})
        self.assertEqual(self.legacy.repository.get_settings("rimili").spreadsheets, legacy.spreadsheets)
        self.assertFalse(self.export.run_due(self.now))
        self.google_factory.assert_not_called()

    def test_settings_round_trip_keeps_each_marketplace_destination(self):
        settings = self.configure(enabled=True)
        names = {
            "WB": ("Stocks", "Orders"),
            "OZON": ("Ozon Stocks", "Ozon Orders"),
            "YANDEX MARKET": ("YM Stocks", "YM Orders"),
        }
        columns = {"WB": "C", "OZON": "K", "YANDEX MARKET": "AA"}
        settings = replace(
            settings,
            schedule_kind="weekly",
            weekday=2,
            run_time="23:17",
            targets=tuple(
                replace(
                    target,
                    spreadsheet_url=f"https://docs.google.com/spreadsheets/d/doc-{index}/edit",
                    stock_sheet_name=names[target.marketplace][0],
                    orders_sheet_name=names[target.marketplace][1],
                    orders_quantity_column=columns[target.marketplace],
                )
                for index, target in enumerate(settings.targets)
            ),
        )
        self.export.save_settings(settings)
        saved = self.repository.get_settings()
        self.assertEqual(
            (saved.enabled, saved.schedule_kind, saved.weekday, saved.run_time), (True, "weekly", 2, "23:17")
        )
        self.assertEqual(
            [target.spreadsheet_url for target in saved.targets],
            [target.spreadsheet_url for target in settings.targets],
        )
        for marketplace, (stock, orders) in names.items():
            self.assertEqual(saved.target(marketplace).orders_quantity_column, columns[marketplace])
            self.assertEqual(
                (saved.target(marketplace).stock_sheet_name, saved.target(marketplace).orders_sheet_name),
                (stock, orders),
            )

    def test_saving_stale_form_does_not_erase_newer_execution_status(self):
        settings = self.configure()
        self.repository.record_attempt(self.now.isoformat())
        self.repository.record_result(self.now.isoformat(), error="Synthetic failure")
        self.export.save_settings(replace(settings, run_time="22:00"))
        saved = self.repository.get_settings()
        self.assertEqual(saved.run_time, "22:00")
        self.assertEqual(saved.last_attempt_at, self.now.isoformat())
        self.assertEqual(saved.last_error, "Synthetic failure")

    def test_settings_and_targets_roll_back_together_when_target_insert_fails(self):
        original = self.configure()
        duplicate = replace(original, run_time="22:00", targets=(*original.targets, original.targets[0]))
        with self.assertRaises(IntegrityError):
            self.repository.save_settings(duplicate)
        self.assertEqual(self.repository.get_settings(), original)

    def test_busy_export_cannot_change_shared_destinations(self):
        original = self.configure()
        changed = self.destination(
            original, "WB", spreadsheet_url="https://docs.google.com/spreadsheets/d/other-test/edit"
        )
        with self.locks.hold("stock_sheet_export"), self.assertRaises(self.locks.SyncJobBusyError):
            self.export.save_settings(changed)
        self.assertEqual(self.repository.get_settings(), original)
        self.export.save_settings(changed)
        self.assertEqual(self.repository.get_settings(), changed)

    def test_initialization_cannot_replace_saved_settings_or_targets(self):
        original = self.configure(orders="Orders", enabled=True, run_time="23:17")
        self.repository.save_settings(self.export.default_settings(self.now), only_if_missing=True)
        self.export.ensure_defaults()
        self.assertEqual(self.repository.get_settings(), original)

    def test_migration_copies_old_common_url_once_and_preserves_new_platform_urls(self):
        previous = self.database_module.Database(self.database.path.parent / "previous-export.sqlite3")
        self.addCleanup(previous.dispose)
        common_url = "https://docs.google.com/spreadsheets/d/previous-doc/edit"
        with previous.connect() as conn:
            conn.execute(
                "CREATE TABLE project_sheet_export_settings (id INTEGER PRIMARY KEY, spreadsheet_url TEXT)"
            )
            conn.execute(
                "CREATE TABLE project_sheet_export_targets (marketplace TEXT PRIMARY KEY, stock_sheet_name TEXT, orders_sheet_name TEXT)"
            )
            conn.execute("INSERT INTO project_sheet_export_settings VALUES (1, ?)", (common_url,))
            conn.executemany(
                "INSERT INTO project_sheet_export_targets VALUES (?, ?, ?)",
                (
                    (marketplace, f"{marketplace} stocks", f"{marketplace} orders")
                    for marketplace in ("WB", "OZON", "YANDEX MARKET")
                ),
            )
            conn.commit()
        self.schema._migrate_project_sheet_export_urls(previous)
        with previous.connect() as conn:
            self.assertEqual(
                {
                    row["spreadsheet_url"]
                    for row in conn.execute("SELECT * FROM project_sheet_export_targets").fetchall()
                },
                {common_url},
            )
            conn.execute(
                "UPDATE project_sheet_export_targets SET spreadsheet_url = ? WHERE marketplace = 'WB'",
                ("https://docs.google.com/spreadsheets/d/new-wb-doc/edit",),
            )
            conn.execute(
                "UPDATE project_sheet_export_targets SET spreadsheet_url = '' WHERE marketplace = 'OZON'"
            )
            conn.commit()
        self.schema._migrate_project_sheet_export_urls(previous)
        with previous.connect() as conn:
            rows = {
                row["marketplace"]: dict(row)
                for row in conn.execute("SELECT * FROM project_sheet_export_targets").fetchall()
            }
        self.assertEqual(
            rows["WB"]["spreadsheet_url"], "https://docs.google.com/spreadsheets/d/new-wb-doc/edit"
        )
        self.assertEqual(rows["OZON"]["spreadsheet_url"], "")
        self.assertEqual(rows["YANDEX MARKET"]["spreadsheet_url"], common_url)
        self.assertEqual(rows["WB"]["stock_sheet_name"], "WB stocks")
        self.assertEqual(rows["OZON"]["orders_sheet_name"], "OZON orders")

    def test_orders_column_migration_defaults_to_c_and_keeps_saved_choice_on_repeat(self):
        previous = self.database_module.Database(self.database.path.parent / "previous-orders.sqlite3")
        self.addCleanup(previous.dispose)
        with previous.connect() as conn:
            conn.execute(
                "CREATE TABLE project_sheet_export_targets (marketplace TEXT PRIMARY KEY, "
                "stock_sheet_name TEXT, orders_sheet_name TEXT, spreadsheet_url TEXT)"
            )
            conn.execute(
                "INSERT INTO project_sheet_export_targets VALUES ('WB', 'Stocks', 'Orders', 'saved-url')"
            )
            conn.commit()
        self.schema._migrate_project_sheet_export_orders_column(previous)
        with previous.connect() as conn:
            row = conn.execute("SELECT * FROM project_sheet_export_targets").fetchone()
            self.assertEqual(row["orders_quantity_column"], "C")
            self.assertEqual(row["orders_sheet_name"], "Orders")
            self.assertEqual(row["spreadsheet_url"], "saved-url")
            conn.execute("UPDATE project_sheet_export_targets SET orders_quantity_column = 'K'")
            conn.commit()
        self.schema._migrate_project_sheet_export_orders_column(previous)
        with previous.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT orders_quantity_column FROM project_sheet_export_targets").fetchone()[
                    "orders_quantity_column"
                ],
                "K",
            )

    def test_combined_output_expands_rows_before_clear_and_never_shrinks_larger_grids(self):
        catalog = [{"article": f"sku-{index}"} for index in range(1001)]
        self.mock(
            self.legacy,
            "_combined_stock_snapshot",
            side_effect=lambda slugs, marketplace, **kwargs: (
                catalog if slugs == ("rimili",) else [],
                {},
                [],
            ),
        )
        self.orders.side_effect = lambda slugs, marketplace, **kwargs: (
            {item["article"]: 1 for item in catalog} if slugs == ("rimili",) else {}
        )
        self.configure(orders="Orders")
        for export_kind, sheet_name in (("stocks", "Stocks"),):
            for current_rows in (1000, 5000):
                with self.subTest(export_kind=export_kind, current_rows=current_rows):
                    self.google.reset_mock()
                    for sheet in self.sheets:
                        sheet["properties"]["gridProperties"]["rowCount"] = current_rows

                    def before_clear(current_rows=current_rows, **kwargs):
                        preparation = self.google.spreadsheets().batchUpdate
                        if current_rows == 1000:
                            requests = preparation.call_args.kwargs["body"]["requests"]
                            growth = [
                                item["updateSheetProperties"]
                                for item in requests
                                if "updateSheetProperties" in item
                            ]
                            self.assertEqual(growth[0]["properties"]["gridProperties"]["rowCount"], 1003)
                            self.assertIn("gridProperties.rowCount", growth[0]["fields"])
                        else:
                            preparation.assert_not_called()
                        return MagicMock()

                    self.google.spreadsheets().values().batchClear.side_effect = before_clear
                    self.export.run_export(self.now, marketplace="WB", export_kind=export_kind)
                    self.assertEqual(len(self.update_for(sheet_name)["values"]), 1002)

    def test_same_file_destination_collisions_are_rejected_across_platforms_and_kinds(self):
        settings = self.configure()
        for marketplace, field in (
            ("WB", "orders_sheet_name"),
            ("OZON", "stock_sheet_name"),
            ("YANDEX MARKET", "orders_sheet_name"),
        ):
            with self.subTest(marketplace=marketplace, field=field):
                conflicting = replace(
                    settings,
                    targets=tuple(
                        replace(
                            target,
                            spreadsheet_url="https://docs.google.com/spreadsheets/d/test-doc/view?gid=99",
                            **{field: "Stocks"},
                        )
                        if target.marketplace == marketplace
                        else target
                        for target in settings.targets
                    ),
                )
                with self.assertRaises(ValueError):
                    self.export.save_settings(conflicting)
        self.assertEqual(self.repository.get_settings().target("WB").orders_sheet_name, "")

    def test_non_google_spreadsheet_links_are_rejected(self):
        settings = self.configure()
        for url in (
            "https://evil.example/spreadsheets/d/test-doc/edit",
            "https://docs.google.com.evil.example/spreadsheets/d/test-doc/edit",
            "https://docs.google.com@evil.example/spreadsheets/d/test-doc/edit",
            "https://evil.example/?next=https://docs.google.com/spreadsheets/d/test-doc/edit",
            "https://docs.google.com/document/d/test-doc/edit",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.export.validate_settings(self.destination(settings, "WB", spreadsheet_url=url))

    def test_missing_file_url_with_a_configured_sheet_is_rejected_even_when_disabled(self):
        settings = self.configure()
        with self.assertRaises(ValueError):
            self.export.save_settings(self.destination(settings, "WB", spreadsheet_url=""))
        self.assertEqual(self.repository.get_settings(), settings)

    def test_blank_platform_is_skipped_without_accessing_other_files(self):
        self.configure()
        self.export.run_export(self.now, marketplace="OZON", export_kind="stocks")
        self.google_factory.assert_not_called()
        self.inbound.assert_not_called()
        self.orders.assert_not_called()

    def test_each_platform_writes_all_projects_to_its_own_file_with_same_sheet_names(self):
        settings = self.configure()
        ids = {"WB": "wb-doc", "OZON": "ozon-doc", "YANDEX MARKET": "ym-doc"}
        self.export.save_settings(
            replace(
                settings,
                targets=tuple(
                    replace(
                        target,
                        spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{ids[target.marketplace]}/edit",
                        stock_sheet_name="Stocks",
                        orders_sheet_name="Orders",
                    )
                    for target in settings.targets
                ),
            )
        )
        quantities = {slug: index for index, slug in enumerate(self.stores, 1)}
        self.mock(
            self.legacy,
            "_combined_stock_snapshot",
            side_effect=lambda slugs, marketplace, **kwargs: (
                [{"article": "Same-SKU", "barcode": "1234567890", "name": "Synthetic product"}],
                {"ff_stock": {"Same-SKU": quantities[slugs[0]]}, "mp_inbound": {"Same-SKU": 0}},
                [],
            ),
        )
        self.orders.side_effect = lambda slugs, marketplace, **kwargs: {"Same-SKU": quantities[slugs[0]]}
        self.sheet_values["'Orders'"] = [
            ["Existing notes"],
            ["ПРОЕКТ", "АРТИКУЛ", "FBS"],
            *[[store.name, "Same-SKU", 99] for store in self.stores.values()],
        ]
        report = self.export.run_export(self.now)
        self.assertEqual(report["spreadsheet_ids"], ids)
        writes = self.google.spreadsheets().values().batchUpdate.call_args_list
        self.assertEqual(len(writes), 6)
        for spreadsheet_id in ids.values():
            file_writes = [
                call.kwargs["body"]["data"][0]
                for call in writes
                if call.kwargs["spreadsheetId"] == spreadsheet_id
            ]
            self.assertEqual(len(file_writes), 2)
            stock = next(update for update in file_writes if update["range"].startswith("'Stocks'"))
            orders = next(update for update in file_writes if update["range"].startswith("'Orders'"))
            self.assertEqual(stock["values"][0], ["КЛЮЧ", "ПРОЕКТ", *self.legacy.EXPORT_HEADERS])
            self.assertEqual(len(stock["values"]), 8)
            for row, (slug, store) in zip(stock["values"][1:], self.stores.items(), strict=True):
                quantity = quantities[slug]
                self.assertEqual(row[:4], [f"{store.name} 1234567890", store.name, "Same-SKU", 1234567890])
                self.assertEqual(row[4:7], ["Synthetic product", quantity, quantity])
            self.assertEqual(orders["range"], "'Orders'!C3:C9")
            self.assertEqual(orders["values"], [[quantities[slug]] for slug in self.stores])

    def test_all_seven_projects_keep_identical_articles_and_separate_totals(self):
        self.configure()
        self.mock(
            self.legacy, "get_settings", side_effect=AssertionError("Legacy settings must not select stores")
        )
        self.mock(
            self.legacy, "list_settings", side_effect=AssertionError("Legacy flags must not select stores")
        )
        for quantity, slug in enumerate(self.stores, 1):
            self.seed(slug, quantity, article="SKU-00123", barcode="1234567890")
        self.export.run_export(self.now, marketplace="WB", export_kind="stocks")
        values = self.update_for("Stocks")["values"]
        self.assertEqual(values[0][:11], ["КЛЮЧ", "ПРОЕКТ", *self.legacy.EXPORT_HEADERS])
        self.assertEqual(values[0][11], f"{self.legacy.FF_STOCK_HEADER_PREFIX}Основной ФФ")
        self.assertEqual(len(values), 8)
        by_project = {row[1]: row for row in values[1:]}
        self.assertEqual(set(by_project), {store.name for store in self.stores.values()})
        for quantity, (_, store) in enumerate(self.stores.items(), 1):
            self.assertEqual(
                by_project[store.name][:5],
                [f"{store.name} 1234567890", store.name, "SKU-00123", 1234567890, "Synthetic product"],
            )
            self.assertEqual(
                (by_project[store.name][5], by_project[store.name][6], by_project[store.name][11]),
                (quantity, quantity, quantity),
            )
        self.orders.assert_not_called()

    def test_stock_keys_preserve_barcode_text_and_align_with_exported_products(self):
        self.configure()
        catalog = [
            {"article": "leading-zero", "barcode": "  '0012345678901  "},
            {"article": " ", "barcode": "skipped"},
            {"article": "long-barcode", "barcode": "12345678901234567890"},
            {"article": "text-barcode", "barcode": "CODE-001"},
            {"article": "numeric-barcode", "barcode": 1234567890},
            {"article": "missing-barcode", "barcode": None},
        ]
        self.mock(
            self.legacy,
            "_combined_stock_snapshot",
            side_effect=lambda slugs, marketplace, **kwargs: (
                catalog if slugs == ("rimili",) else [],
                {},
                [],
            ),
        )
        self.export.run_export(self.now, marketplace="WB", export_kind="stocks")
        update = self.update_for("Stocks")
        self.assertEqual(update["range"], "'Stocks'!A2:K7")
        self.assertEqual(
            [(row[0], row[2]) for row in update["values"][1:]],
            [
                ("RIMILI 0012345678901", "leading-zero"),
                ("RIMILI 12345678901234567890", "long-barcode"),
                ("RIMILI CODE-001", "text-barcode"),
                ("RIMILI 1234567890", "numeric-barcode"),
                ("RIMILI ", "missing-barcode"),
            ],
        )
        self.assertEqual(
            self.google.spreadsheets().values().batchUpdate.call_args.kwargs["body"]["valueInputOption"],
            "RAW",
        )

    def test_stock_sheet_replaces_existing_cells_and_writes_timestamp_and_sum_formulas(self):
        self.configure()
        self.seed("rimili", 5, barcode="2050292584830")
        self.timestamp.side_effect = self.original_timestamp
        self.mock(
            self.legacy, "_check_timestamp_cells", side_effect=AssertionError("Stock sheet is replaced")
        )
        self.sheet_values["'Stocks'!A1:B1"] = [["Old label", "=NOW()"]]
        self.sheet_values["'Stocks'!AA2:AD"] = [["Old formulas", "=SUM(A1:A2)"]]
        stock_sheet = self.sheets[0]
        stock_sheet["properties"]["gridProperties"].update(columnCount=30, rowCount=1000)
        stock_sheet["merges"] = [
            {"startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": 2},
            {"startRowIndex": 0, "endRowIndex": 3, "startColumnIndex": 26, "endColumnIndex": 30},
        ]
        values_api = self.google.spreadsheets().values()

        def write_data(**kwargs):
            values_api.batchClear.assert_called_once_with(
                spreadsheetId="test-doc", body={"ranges": ["'Stocks'!A1:AD"]}
            )
            self.timestamp.assert_not_called()
            return MagicMock()

        values_api.batchUpdate.side_effect = write_data
        report = self.export.run_export(self.now, marketplace="WB", export_kind="stocks")
        requests = self.google.spreadsheets().batchUpdate.call_args_list
        self.assertEqual(
            requests[0].kwargs["body"]["requests"],
            [{"unmergeCells": {"range": {"sheetId": 1, **merged}}} for merged in stock_sheet["merges"]],
        )
        summary, formatting = requests[-1].kwargs["body"]["requests"]
        summary = summary["updateCells"]
        self.assertEqual(
            summary["range"],
            {"sheetId": 1, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 12},
        )
        cells = summary["rows"][0]["values"]
        self.assertEqual(cells[0], {"userEnteredValue": {"stringValue": "Выгрузка (МСК)"}})
        exported_at = datetime.fromisoformat(report["marketplaces"][0]["exported_at"])
        serial = (exported_at.replace(tzinfo=None) - datetime(1899, 12, 30)).total_seconds() / 86400
        self.assertEqual(cells[1], {"userEnteredValue": {"numberValue": serial}})
        self.assertEqual(cells[2:5], [{}, {}, {}])
        self.assertEqual(
            [cell["userEnteredValue"]["formulaValue"] for cell in cells[5:]],
            [
                "=SUM(F3:F)",
                "=SUM(G3:G)",
                "=SUM(H3:H)",
                "=SUM(I3:I)",
                "=SUM(J3:J)",
                "=SUM(K3:K)",
                "=SUM(L3:L)",
            ],
        )
        self.assertEqual(
            formatting["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"],
            {"type": "DATE_TIME", "pattern": "dd.mm.yyyy hh:mm:ss"},
        )
        self.assertEqual(summary["fields"], "userEnteredValue")
        update = self.update_for("Stocks")
        self.assertEqual(update["range"], "'Stocks'!A2:L3")
        self.assertEqual(update["values"][0][0:2], ["КЛЮЧ", "ПРОЕКТ"])
        self.assertEqual(update["values"][1][0], "RIMILI 2050292584830")
        self.assertEqual(report["marketplaces"][0]["updated_cells"], 36)

    def test_empty_stock_sheet_keeps_sum_formulas_through_last_center_beyond_z(self):
        self.configure()
        self.timestamp.side_effect = self.original_timestamp
        self.sheets[0]["properties"]["gridProperties"].update(columnCount=35, rowCount=2)
        self.mock(
            self.legacy,
            "_combined_stock_snapshot",
            return_value=([], {f"ff_stock:ФФ {index:02d}": {} for index in range(20)}, []),
        )
        self.export.run_export(self.now, marketplace="WB", export_kind="stocks")
        self.google.spreadsheets().values().batchClear.assert_called_once_with(
            spreadsheetId="test-doc", body={"ranges": ["'Stocks'!A1:AI"]}
        )
        requests = self.google.spreadsheets().batchUpdate.call_args_list
        self.assertEqual(
            requests[0].kwargs["body"]["requests"],
            [
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": 1, "gridProperties": {"rowCount": 3}},
                        "fields": "gridProperties.rowCount",
                    }
                }
            ],
        )
        cells = requests[-1].kwargs["body"]["requests"][0]["updateCells"]["rows"][0]["values"]
        self.assertEqual(len(cells), 31)
        self.assertEqual(cells[5], {"userEnteredValue": {"formulaValue": "=SUM(F3:F)"}})
        self.assertEqual(cells[-1], {"userEnteredValue": {"formulaValue": "=SUM(AE3:AE)"}})
        self.assertEqual(self.update_for("Stocks")["range"], "'Stocks'!A2:AE2")
        self.assertEqual(len(self.update_for("Stocks")["values"]), 1)

    def test_failed_stock_data_write_does_not_publish_summary_or_success(self):
        self.configure()
        self.seed("rimili", 5)
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = RuntimeError(
            "Synthetic write failure"
        )
        with self.assertRaisesRegex(RuntimeError, "Synthetic write failure"):
            self.export.run_export(self.now, marketplace="WB", export_kind="stocks")
        self.timestamp.assert_not_called()
        self.assertIsNone(self.repository.get_settings().last_success_at)

    def test_unknown_inbound_only_blanks_the_affected_project(self):
        self.configure()
        self.seed("rimili", 3)
        self.seed("tris", 7)

        def inbound(store, marketplace, catalog, **kwargs):
            available = store != "rimili"
            return self.legacy.stock_sheet_inbound.InboundExport(
                catalog=list(catalog),
                quantities={row["article"]: 0 if available else None for row in catalog},
                available=available,
                warnings=() if available else ("No confirmed snapshot",),
            )

        self.inbound.side_effect = inbound
        self.export.run_export(self.now, marketplace="WB", export_kind="stocks")
        rows = {row[1]: row for row in self.update_for("Stocks")["values"][1:]}
        self.assertEqual((rows["RIMILI"][5], rows["RIMILI"][10], rows["RIMILI"][6]), ("", "", 3))
        self.assertEqual((rows["TRIS"][5], rows["TRIS"][10], rows["TRIS"][6]), (7, 0, 7))

    def test_saved_inbound_after_failed_refresh_keeps_quantities_and_total_with_warning(self):
        from app.dto.inbound_supplies import InboundItem, InboundSnapshot, InboundSupply

        self.configure()
        self.seed("rimili", 3)
        self.seed("tris", 7)
        self.inbound.side_effect = self.original_inbound_load
        service = MagicMock()
        self.mock(self.legacy.stock_sheet_inbound.inbound_supplies, "build_service", return_value=service)

        def snapshot(targets):
            store, marketplace = targets[0]
            if store != "rimili":
                return [
                    InboundSnapshot(
                        store_slug=store,
                        marketplace=marketplace,
                        status="ok",
                        last_success=self.now.isoformat(),
                    )
                ]
            return [
                InboundSnapshot(
                    store_slug=store,
                    marketplace=marketplace,
                    status="error",
                    last_success="2026-10-01T09:00:00+00:00",
                    error="Synthetic failed refresh",
                    supplies=(
                        InboundSupply(
                            key="saved-supply",
                            supply_id="1",
                            status="IN_TRANSIT",
                            status_label="В пути",
                            stage="transit",
                            items=(InboundItem(article="Shared-Article", quantity=5),),
                        ),
                    ),
                )
            ]

        service.report.side_effect = snapshot
        report = self.export.run_export(self.now, marketplace="WB", export_kind="stocks")
        rows = {row[1]: row for row in self.update_for("Stocks")["values"][1:]}
        self.assertEqual((rows["RIMILI"][5], rows["RIMILI"][6], rows["RIMILI"][10]), (8, 3, 5))
        self.assertEqual((rows["TRIS"][5], rows["TRIS"][6], rows["TRIS"][10]), (7, 7, 0))
        warning = " ".join(report["marketplaces"][0]["warnings"])
        self.assertIn("RIMILI", warning)
        self.assertIn("01.10.2026 12:00:00 МСК", warning)
        self.assertTrue(all(call.kwargs["allow_saved_snapshot"] for call in self.inbound.call_args_list))
        self.assertEqual(service.report.call_count, 7)

    def test_order_matching_keeps_row_order_and_writes_only_selected_column(self):
        settings = self.configure(stocks="", orders="Orders")
        self.export.save_settings(self.destination(settings, "WB", orders_quantity_column=" k "))
        self.assertEqual(self.export.get_settings().target("WB").orders_quantity_column, "K")
        self.orders.side_effect = lambda slugs, marketplace, **kwargs: {
            "Same-SKU": 7 if slugs == ("rimili",) else 2,
            "Not-in-sheet": 3,
        }
        self.sheet_values["'Orders'"] = [
            ["User title", "=NOW()"],
            ["Notes"],
            ["Article", "Name", "Проект"],
            ["same-sku", "First", "TRIS"],
            ["Same-SKU", "Second", "RIMILI"],
            ["Same-SKU", "Unknown", "Unknown project"],
            ["No-orders", "Third", "RIMILI"],
            ["", "No article", "RIMILI"],
        ]
        values_api = self.google.spreadsheets().values()

        def after_clear(**kwargs):
            self.assertEqual(self.orders.call_count, len(self.stores))
            values_api.batchClear.assert_called_once_with(
                spreadsheetId="test-doc", body={"ranges": ["'Orders'!K4:K8"]}
            )
            return MagicMock()

        values_api.batchUpdate.side_effect = after_clear
        report = self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
        self.assertEqual(
            self.updates(),
            [
                {"range": "'Orders'!K4:K5", "values": [[2], [7]]},
                {"range": "'Orders'!K7:K7", "values": [[0]]},
            ],
        )
        self.google.spreadsheets().batchUpdate.assert_not_called()
        self.timestamp.assert_not_called()
        self.inbound.assert_not_called()
        self.assertEqual(report["marketplaces"][0]["fbs_orders"]["updated_cells"], 3)
        self.assertIn("не найденных в листе", " ".join(report["marketplaces"][0]["warnings"]))
        self.assertIsNotNone(self.repository.get_settings().last_success_at)

    def test_order_matching_expands_only_columns_and_accepts_project_aliases(self):
        settings = self.configure(stocks="", orders="Orders")
        self.export.save_settings(self.destination(settings, "WB", orders_quantity_column="AA"))
        self.sheets[1]["properties"]["gridProperties"].update(columnCount=3, rowCount=10)
        self.sheet_values["'Orders'"] = [["PROJECT", "АРТИКУЛ"], ["Хочушар", 123.0]]
        self.orders.side_effect = lambda slugs, marketplace, **kwargs: (
            {"123": 4} if slugs == ("rimili",) else {}
        )
        self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
        self.assertEqual(self.updates(), [{"range": "'Orders'!AA2:AA2", "values": [[4]]}])
        self.assertEqual(
            self.google.spreadsheets().batchUpdate.call_args.kwargs["body"],
            {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {"sheetId": 2, "gridProperties": {"columnCount": 27}},
                            "fields": "gridProperties.columnCount",
                        }
                    }
                ]
            },
        )
        self.timestamp.assert_not_called()

    def test_order_matching_rejects_ambiguous_headers_and_identifier_column(self):
        self.configure(stocks="", orders="Orders")
        for rows, error in (
            ([], "нужна одна шапка"),
            ([["Проект", "АРТИКУЛ", "Article"]], "неоднозначные"),
            ([["Проект", "Article"], ["Проект", "Article"]], "нужна одна шапка"),
            ([["Name", "Article", "Проект"], ["name", "sku", "RIMILI"]], "данные товара"),
        ):
            with self.subTest(rows=rows):
                self.sheet_values["'Orders'"] = rows
                with self.assertRaisesRegex(self.export.StockSheetExportError, error):
                    self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
                self.assert_no_google_writes()

    def test_order_matching_rejects_merged_destination_before_stock_write(self):
        self.configure(orders="Orders")
        self.sheet_values["'Orders'"] = [["Проект", "Article"], ["RIMILI", "sku"]]
        self.sheets[1]["merges"] = [
            {"startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 2, "endColumnIndex": 4}
        ]
        with self.assertRaisesRegex(self.export.StockSheetExportError, "объединена"):
            self.export.run_export(self.now, marketplace="WB")
        self.assert_no_google_writes()

    def test_order_matching_with_no_valid_rows_does_not_write_or_record_success(self):
        self.configure(stocks="", orders="Orders")
        self.sheets[1]["properties"]["gridProperties"]["rowCount"] = 1
        self.sheet_values["'Orders'"] = [["Проект", "Article"]]
        report = self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
        self.assert_no_google_writes()
        self.assertTrue(report["marketplaces"][0]["fbs_orders"]["skipped"])
        self.assertIsNone(self.repository.get_settings().last_success_at)
        self.assertNotIn(
            "'Orders'!C2:C",
            [call.kwargs["range"] for call in self.google.spreadsheets().values().get.call_args_list],
        )

    def test_order_matching_fills_each_occurrence_of_the_same_product(self):
        self.configure(stocks="", orders="Orders")
        self.sheet_values["'Orders'"] = [["ARTICLE", "Проект"], ["sku", "RIMILI"], ["SKU", "rimili"]]
        self.orders.side_effect = lambda slugs, marketplace, **kwargs: (
            {"sku": 6} if slugs == ("rimili",) else {}
        )
        self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
        self.assertEqual(self.updates(), [{"range": "'Orders'!C2:C3", "values": [[6], [6]]}])

    def test_order_column_clear_includes_unmatched_rows_and_trailing_empty_result_formulas(self):
        self.configure(stocks="", orders="Orders")
        self.sheet_values["'Orders'"] = [["ARTICLE", "Проект"], ["sku", "Unknown", 99]]
        self.sheet_values["'Orders'!C2:C"] = [[99], [], ['=IFERROR(1/0, "")']]
        self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
        self.google.spreadsheets().values().batchClear.assert_called_once_with(
            spreadsheetId="test-doc", body={"ranges": ["'Orders'!C2:C4"]}
        )
        self.google.spreadsheets().values().batchUpdate.assert_not_called()
        self.timestamp.assert_not_called()
        self.assertIsNotNone(self.repository.get_settings().last_success_at)

    def test_orders_header_can_be_below_first_25_rows(self):
        self.configure(stocks="", orders="Orders")
        self.sheet_values["'Orders'"] = [["notes"] for _ in range(30)] + [
            ["ARTICLE", "Проект"],
            ["sku", "RIMILI", 99],
        ]
        self.orders.side_effect = lambda slugs, marketplace, **kwargs: (
            {"sku": 6} if slugs == ("rimili",) else {}
        )
        self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
        self.assertEqual(self.updates(), [{"range": "'Orders'!C32:C32", "values": [[6]]}])
        self.google.spreadsheets().values().batchClear.assert_called_once_with(
            spreadsheetId="test-doc", body={"ranges": ["'Orders'!C32:C32"]}
        )

    def test_failed_order_clear_does_not_write_values_or_record_success(self):
        self.configure(stocks="", orders="Orders")
        self.sheet_values["'Orders'"] = [["ARTICLE", "Проект"], ["sku", "RIMILI", 99]]
        self.google.spreadsheets().values().batchClear.return_value.execute.side_effect = RuntimeError(
            "Clear failed"
        )
        with self.assertRaisesRegex(RuntimeError, "Clear failed"):
            self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
        self.google.spreadsheets().values().batchUpdate.assert_not_called()
        self.assertIsNone(self.repository.get_settings().last_success_at)

    def test_invalid_order_column_is_rejected_before_google_access(self):
        settings = self.configure(stocks="", orders="Orders")
        for column in ("", "K2", "A:K", "11", "К", "AAAA"):
            with self.subTest(column=column), self.assertRaisesRegex(ValueError, "столбец"):
                self.export.save_settings(self.destination(settings, "WB", orders_quantity_column=column))
        self.google_factory.assert_not_called()

    def test_late_project_order_failure_leaves_selected_marketplace_sheets_untouched(self):
        self.configure(orders="Orders")
        self.seed("rimili", 4)

        def orders(slugs, marketplace, **kwargs):
            if slugs == ("toyka",):
                raise RuntimeError("Synthetic TOYKA API failure")
            return {"Same-SKU": 2}

        self.orders.side_effect = orders
        with self.assertRaisesRegex(Exception, "TOYKA"):
            self.export.run_export(self.now, marketplace="WB")
        self.assert_no_google_writes()

    def test_only_selected_platform_and_kind_is_exported(self):
        settings = self.configure(orders="Orders")
        self.export.save_settings(
            replace(
                settings,
                targets=tuple(
                    replace(
                        target,
                        spreadsheet_url="https://docs.google.com/spreadsheets/d/ozon-doc/edit",
                        stock_sheet_name="Ozon Stocks",
                        orders_sheet_name="Ozon Orders",
                    )
                    if target.marketplace == "OZON"
                    else target
                    for target in settings.targets
                ),
            )
        )
        self.seed("rimili", 9, marketplace="OZON")
        report = self.export.run_export(self.now, marketplace="OZON", export_kind="stocks")
        self.assertEqual(report["spreadsheet_ids"], {"OZON": "ozon-doc"})
        self.assertEqual(len(self.updates()), 1)
        self.assertEqual(self.update_for("Ozon Stocks")["values"][1][5], 9)
        self.orders.assert_not_called()
        self.assertEqual({call.args[1] for call in self.inbound.call_args_list}, {"OZON"})
        self.assertEqual(
            {
                call.kwargs["spreadsheetId"]
                for call in self.google.spreadsheets().values().batchUpdate.call_args_list
            },
            {"ozon-doc"},
        )
        self.assertEqual(
            {call.kwargs["spreadsheetId"] for call in self.google.spreadsheets().get.call_args_list},
            {"ozon-doc"},
        )

    def test_empty_sheet_names_skip_without_google_access_or_success(self):
        self.configure(stocks="", orders="")
        self.export.run_export(self.now)
        self.google_factory.assert_not_called()
        self.orders.assert_not_called()
        self.inbound.assert_not_called()
        self.assertIsNone(self.repository.get_settings().last_success_at)

    def test_scoped_success_keeps_full_scheduled_export_due(self):
        old_attempt = (self.now - timedelta(days=2)).isoformat()
        self.configure(enabled=True, run_time="01:00", last_attempt_at=old_attempt, last_error="Old failure")
        self.seed("rimili", 3)
        self.export.run_export(self.now, marketplace="WB", export_kind="stocks")
        saved = self.repository.get_settings()
        self.assertEqual(saved.last_attempt_at, old_attempt)
        self.assertEqual(saved.last_success_at, self.now.isoformat())
        self.assertIsNone(self.export.current_error(saved))
        runner = self.mock(self.export, "run_export", return_value={"marketplaces": []})
        self.export.run_due(self.now)
        runner.assert_called_once()

    def test_full_scheduled_export_records_attempt_and_does_not_repeat_same_period(self):
        self.configure(enabled=True, run_time="01:00")
        self.seed("rimili", 3)
        self.export.run_due(self.now)
        saved = self.repository.get_settings()
        self.assertEqual(saved.last_attempt_at, self.now.isoformat())
        self.assertEqual(saved.last_success_at, self.now.isoformat())
        writes = self.google.spreadsheets().values().batchUpdate.call_count
        self.export.run_due(self.now + timedelta(minutes=1))
        self.assertEqual(self.google.spreadsheets().values().batchUpdate.call_count, writes)

    def test_full_failure_is_persisted_without_success(self):
        self.configure(stocks="", orders="Orders")
        self.orders.side_effect = RuntimeError("Synthetic network failure")
        with self.assertRaisesRegex(Exception, "Synthetic network failure"):
            self.export.run_export(self.now)
        saved = self.repository.get_settings()
        self.assertEqual(saved.last_attempt_at, self.now.isoformat())
        self.assertIn("Synthetic network failure", saved.last_error)
        self.assertIsNone(saved.last_success_at)
        self.assert_no_google_writes()

    def configure_fbo(self, **changes):
        settings = self.configure(stocks="", orders="")
        settings = self.destination(settings, "WB", fbo_sheet_name="Orders", **changes)
        self.export.save_settings(settings)
        return settings

    def fbo_totals(self, to=3, back=1):
        return {
            store: self.export.fbo_transit.TransitSnapshot(
                {"sku": to if store == "rimili" else 8}, {"sku": back if store == "rimili" else 2}
            )
            for store in self.stores
        }

    def test_fbo_persists_separate_settings_and_allows_shared_fbs_sheet(self):
        settings = self.configure_fbo(
            orders_sheet_name="Orders",
            orders_quantity_column="K",
            fbo_to_customer_column=" j ",
            fbo_from_customer_column=" l ",
        )
        saved = self.export.get_settings().target("WB")
        self.assertEqual(
            (saved.fbo_sheet_name, saved.fbo_to_customer_column, saved.fbo_from_customer_column),
            ("Orders", "J", "L"),
        )
        for changes in (
            {"fbo_from_customer_column": "j"},
            {"fbo_to_customer_column": "K"},
            {"stock_sheet_name": "Orders"},
            {"fbo_from_customer_column": "L5"},
            {"fbo_to_customer_column": "К"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.export.save_settings(self.destination(settings, "WB", **changes))

    def test_fbo_matches_projects_clears_both_columns_and_preserves_fbs(self):
        self.configure_fbo()
        loader = self.mock(self.export.fbo_transit, "load", return_value=self.fbo_totals())
        self.sheet_values["'Orders'"] = [
            ["Date", "keep me"],
            ["ARTICLE", "Проект", "Name"],
            ["sku", "RIMILI"],
            ["sku", "TRIS"],
            ["sku", "UNKNOWN"],
            ["no-transit", "RIMILI"],
            ["sku", "Хочушар"],
        ]
        self.sheet_values["'Orders'!L3:L"] = [[5], [], [], [], [], [], ['=IFERROR(1/0, "")']]
        report = self.export.run_export(self.now, marketplace="WB", export_kind="fbo_transit")
        loader.assert_called_once_with("WB")
        self.orders.assert_not_called()
        self.inbound.assert_not_called()
        self.google.spreadsheets().values().batchClear.assert_called_once_with(
            spreadsheetId="test-doc", body={"ranges": ["'Orders'!J3:J7", "'Orders'!L3:L9"]}
        )
        self.assertEqual(
            self.updates(),
            [
                {"range": "'Orders'!J3:J4", "values": [[3], [8]]},
                {"range": "'Orders'!J6:J7", "values": [[0], [3]]},
                {"range": "'Orders'!L3:L4", "values": [[1], [2]]},
                {"range": "'Orders'!L6:L7", "values": [[0], [1]]},
            ],
        )
        self.assertEqual(report["marketplaces"][0]["fbo_transit"]["updated_cells"], 8)
        self.assertTrue(self.export.get_settings().last_success_at)
        self.timestamp.assert_not_called()
        self.google.spreadsheets().batchUpdate.assert_not_called()

    def test_fbo_failure_or_bad_second_column_prevents_all_writes(self):
        settings = self.configure_fbo(stock_sheet_name="Stocks", fbo_from_customer_column="B")
        self.sheet_values["'Orders'"] = [["ARTICLE", "Проект"], ["sku", "RIMILI"]]
        loader = self.mock(self.export.fbo_transit, "load", return_value=self.fbo_totals())
        with self.assertRaisesRegex(self.export.StockSheetExportError, "данные товара"):
            self.export.run_export(self.now)
        self.assert_no_google_writes()
        self.export.save_settings(self.destination(settings, "WB", fbo_from_customer_column="L"))
        self.sheets[1]["merges"] = [
            {"startRowIndex": 1, "endRowIndex": 3, "startColumnIndex": 11, "endColumnIndex": 13}
        ]
        with self.assertRaisesRegex(self.export.StockSheetExportError, "объединена"):
            self.export.run_export(self.now)
        self.assert_no_google_writes()
        loader.side_effect = self.export.StockSheetExportError("TRIS / WB / FBO: missing page")
        with self.assertRaisesRegex(self.export.StockSheetExportError, "TRIS"):
            self.export.run_export(self.now)
        self.assert_no_google_writes()

    def test_shared_fbs_fbo_sheet_grows_once_to_largest_column(self):
        self.configure_fbo(orders_sheet_name="Orders", orders_quantity_column="AA")
        self.sheets[1]["properties"]["gridProperties"]["columnCount"] = 2
        self.sheet_values["'Orders'"] = [["ARTICLE", "Проект"], ["sku", "RIMILI"]]
        self.mock(self.export.fbo_transit, "load", return_value=self.fbo_totals())
        self.export.run_export(self.now)
        batches = self.google.spreadsheets().batchUpdate.call_args_list
        self.assertEqual(len(batches), 1)
        self.assertEqual(
            batches[0].kwargs["body"]["requests"][0]["updateSheetProperties"]["properties"],
            {"sheetId": 2, "gridProperties": {"columnCount": 27}},
        )
        self.assertEqual(
            [row["range"] for row in self.updates()], ["'Orders'!AA2:AA2", "'Orders'!J2:J2", "'Orders'!L2:L2"]
        )

    def test_fbo_scheduled_runs_fetch_new_counts_and_clear_previous_counts(self):
        settings = self.configure_fbo()
        self.export.save_settings(replace(settings, enabled=True, run_time="09:00"))
        self.sheet_values["'Orders'"] = [["ARTICLE", "Проект"], ["sku", "RIMILI"]]
        loader = self.mock(
            self.export.fbo_transit, "load", side_effect=[self.fbo_totals(), self.fbo_totals(0, 0)]
        )
        self.export.run_due(self.now)
        self.export.run_due(self.now + timedelta(days=1))
        self.assertEqual(loader.call_count, 2)
        self.assertEqual(
            self.updates()[-2:],
            [
                {"range": "'Orders'!J2:J2", "values": [[0]]},
                {"range": "'Orders'!L2:L2", "values": [[0]]},
            ],
        )
        self.orders.assert_not_called()

    def test_blank_fbo_sheet_never_calls_api(self):
        self.configure(stocks="", orders="")
        loader = self.mock(self.export.fbo_transit, "load", side_effect=AssertionError("disabled"))
        self.export.run_export(self.now)
        loader.assert_not_called()
        self.assert_no_google_writes()

    def test_fbo_migration_keeps_existing_fbs_settings_and_runs_idempotently(self):
        previous = self.database_module.Database(self.database.path.parent / "previous-fbo.sqlite3")
        self.addCleanup(previous.dispose)
        with previous.connect() as conn:
            conn.execute(
                "CREATE TABLE project_sheet_export_targets (marketplace TEXT PRIMARY KEY, "
                "orders_sheet_name TEXT, orders_quantity_column TEXT)"
            )
            conn.execute("INSERT INTO project_sheet_export_targets VALUES ('WB', 'Orders', 'K')")
            conn.commit()
        self.schema._migrate_project_sheet_export_fbo_columns(previous)
        with previous.connect() as conn:
            row = conn.execute("SELECT * FROM project_sheet_export_targets").fetchone()
            self.assertEqual(
                (row["fbo_sheet_name"], row["fbo_to_customer_column"], row["fbo_from_customer_column"]),
                ("", "J", "L"),
            )
            self.assertEqual(row["orders_quantity_column"], "K")
            conn.execute(
                "UPDATE project_sheet_export_targets SET fbo_sheet_name='Custom', fbo_from_customer_column='Z'"
            )
            conn.commit()
        self.schema._migrate_project_sheet_export_fbo_columns(previous)
        with previous.connect() as conn:
            row = conn.execute("SELECT * FROM project_sheet_export_targets").fetchone()
            self.assertEqual((row["fbo_sheet_name"], row["fbo_from_customer_column"]), ("Custom", "Z"))


if __name__ == "__main__":
    unittest.main()
