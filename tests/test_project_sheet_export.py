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
        for export_kind, sheet_name in (("stocks", "Stocks"), ("fbs_orders", "Orders")):
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
            for update in file_writes:
                is_stock = update["range"].startswith("'Stocks'")
                self.assertEqual(
                    update["values"][0],
                    [
                        "ПРОЕКТ",
                        *(self.legacy.EXPORT_HEADERS if is_stock else self.legacy.ORDER_EXPORT_HEADERS),
                    ],
                )
                self.assertEqual(len(update["values"]), 8)
                self.assertEqual(
                    {row[0] for row in update["values"][1:]},
                    {store.name for store in self.stores.values()},
                )
                for row in update["values"][1:]:
                    self.assertEqual(row[1], "Same-SKU")
                    quantity = next(
                        quantities[slug] for slug, store in self.stores.items() if store.name == row[0]
                    )
                    if is_stock:
                        self.assertEqual(row[2:6], [1234567890, "Synthetic product", quantity, quantity])
                    else:
                        self.assertEqual(row[2], quantity)

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
        self.assertEqual(values[0][:10], ["ПРОЕКТ", *self.legacy.EXPORT_HEADERS])
        self.assertEqual(values[0][10], f"{self.legacy.FF_STOCK_HEADER_PREFIX}Основной ФФ")
        self.assertEqual(len(values), 8)
        by_project = {row[0]: row for row in values[1:]}
        self.assertEqual(set(by_project), {store.name for store in self.stores.values()})
        for quantity, (_, store) in enumerate(self.stores.items(), 1):
            self.assertEqual(
                by_project[store.name][:4], [store.name, "SKU-00123", 1234567890, "Synthetic product"]
            )
            self.assertEqual(
                (by_project[store.name][4], by_project[store.name][5], by_project[store.name][10]),
                (quantity, quantity, quantity),
            )
        self.orders.assert_not_called()

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
        rows = {row[0]: row for row in self.update_for("Stocks")["values"][1:]}
        self.assertEqual((rows["RIMILI"][4], rows["RIMILI"][9], rows["RIMILI"][5]), ("", "", 3))
        self.assertEqual((rows["TRIS"][4], rows["TRIS"][9], rows["TRIS"][5]), (7, 0, 7))

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
        rows = {row[0]: row for row in self.update_for("Stocks")["values"][1:]}
        self.assertEqual((rows["RIMILI"][4], rows["RIMILI"][5], rows["RIMILI"][9]), (8, 3, 5))
        self.assertEqual((rows["TRIS"][4], rows["TRIS"][5], rows["TRIS"][9]), (7, 7, 0))
        warning = " ".join(report["marketplaces"][0]["warnings"])
        self.assertIn("RIMILI", warning)
        self.assertIn("01.10.2026 12:00:00 МСК", warning)
        self.assertTrue(all(call.kwargs["allow_saved_snapshot"] for call in self.inbound.call_args_list))
        self.assertEqual(service.report.call_count, 7)

    def test_order_rows_keep_project_and_fetch_every_project_before_first_write(self):
        self.configure(stocks="", orders="Orders")
        quantities = {slug: index for index, slug in enumerate(self.stores, 1)}
        self.orders.side_effect = lambda slugs, marketplace, **kwargs: {"SKU-00123": quantities[slugs[0]]}
        self.google.spreadsheets().values().batchClear.side_effect = lambda **kwargs: (
            self.assertEqual(self.orders.call_count, len(self.stores)) or MagicMock()
        )
        self.export.run_export(self.now, marketplace="WB", export_kind="fbs_orders")
        values = self.update_for("Orders")["values"]
        self.assertEqual(values[0], ["ПРОЕКТ", *self.legacy.ORDER_EXPORT_HEADERS])
        self.assertEqual(
            {row[0]: row[1:] for row in values[1:]},
            {self.stores[slug].name: ["SKU-00123", quantity] for slug, quantity in quantities.items()},
        )
        self.assertEqual(
            {call.args[0] for call in self.orders.call_args_list}, {(slug,) for slug in self.stores}
        )
        self.assertEqual({call.args[1] for call in self.orders.call_args_list}, {"WB"})
        self.inbound.assert_not_called()

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
        self.assertEqual(self.update_for("Ozon Stocks")["values"][1][4], 9)
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


if __name__ == "__main__":
    unittest.main()
