"""Exact Sunday FBO snapshots, independent stock destination and safe write lifecycle."""

import copy
import importlib
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import test_google_week_sales as sales_tests


class GoogleWeekStockTests(unittest.TestCase):
    mock = sales_tests.GoogleWeekSalesTests.mock
    configure = sales_tests.GoogleWeekSalesTests.configure
    client = sales_tests.GoogleWeekSalesTests.client
    writes = sales_tests.GoogleWeekSalesTests.writes
    assert_no_sheet_writes = sales_tests.GoogleWeekSalesTests.assert_no_sheet_writes
    run_due = sales_tests.GoogleWeekSalesTests.run_due

    @classmethod
    def setUpClass(cls):
        sales_tests.GoogleWeekSalesTests.setUpClass.__func__(cls)
        cls.stock = importlib.import_module("app.integrations.google_week_stock")

    def setUp(self):
        sales_tests.GoogleWeekSalesTests.setUp(self)
        self.title = "Сток на складах ВБ"
        self.week.save_settings(
            self.repo.get_settings(), self.now - timedelta(days=8), stock_sheet_name=self.title
        )
        self.target_rows = [[] for _ in range(9)]
        self.target_rows[7] = [""] * 48
        self.target_rows[7][3:5] = ["Проект", "ARTICLE"]
        self.target_rows[7][45:47] = ["W38 2026", "W39 2026"]
        self.target_rows[8] = ["", "", "", "RIMILI", "123"] + [""] * 43
        self.target_sheet = {
            "properties": {
                "sheetId": 223987387,
                "title": self.title,
                "gridProperties": {"rowCount": 1000, "columnCount": 60},
            }
        }
        self.google.spreadsheets().get.return_value.execute.return_value["sheets"].append(self.target_sheet)
        self.target_formulas = None

        def read(**kwargs):
            destination = kwargs["range"] == "'" + self.title.replace("'", "''") + "'"
            rows = self.target_rows if destination else self.rows
            if destination and kwargs["valueRenderOption"] == "FORMULA" and self.target_formulas:
                rows = self.target_formulas
            request = MagicMock()
            request.execute.return_value = {"values": copy.deepcopy(rows)}
            return request

        self.google.spreadsheets().values().get.side_effect = read
        with self.database.connect() as conn:
            for marketplace, scheme, day, quantity in (
                ("WB", "fbo", "2026-09-26", 77),
                ("WB", "fbo", "2026-09-27", 25),
                ("WB", "fbo", "2026-09-28", 88),
                ("WB", "fbs", "2026-09-27", 900),
                ("OZON", "fbo", "2026-09-27", 800),
            ):
                conn.execute(
                    "INSERT INTO marketplace_stock_daily_history (store_slug,marketplace,article,scheme,day,quantity,captured_at) VALUES ('rimili',?,'123',?,?,?,?)",
                    (marketplace, scheme, day, quantity, day + "T20:04:00+00:00"),
                )
            conn.commit()

    def export(self):
        return self.tracking.run_tracked(self.week.JOB_NAME, "manual", lambda: self.stock.run_now(self.now))

    def test_only_fbo_exact_sunday_and_only_source_week_column(self):
        result = self.export()
        self.assertEqual(self.writes(), {"AU9": 25})
        self.assertEqual(result["periods"][0]["snapshot_day"], "2026-09-27")
        self.assertEqual(len(result["periods"]), 1)
        self.assertTrue(result["complete"])
        self.assertEqual(result["source_sheet_name"], "2. Факт продаж")
        self.assertEqual(result["sheet_name"], self.title)
        self.assertEqual(self.repo.get_stock_state().result, result)
        self.assertIn("2026-09-27T20:04:00", result["snapshot_times"][0]["captured_at"])
        self.google.spreadsheets().values().batchClear.assert_not_called()
        self.assertIsNone(self.repo.get_sales_state().last_success_at)

    def test_same_article_in_different_projects_uses_each_projects_snapshot(self):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO stock_items (store_slug,marketplace,article,barcode,name) VALUES ('tris','WB','123','other','Tris product')"
            )
            conn.execute(
                "INSERT INTO marketplace_stock_daily_history (store_slug,marketplace,article,scheme,day,quantity,captured_at) VALUES ('tris','WB','123','fbo','2026-09-27',80,'2026-09-27T20:00:00+00:00')"
            )
            conn.commit()
        self.target_rows[7][1:3] = ["BARCODE", "BARCODE"]
        self.target_rows[8][1:4] = ["other", "wrong", "ХОЧУШАР"]
        self.target_rows.append(["", "001234", "", " TrIs ", "123"])
        self.export()
        self.assertEqual(self.writes(), {"AU9": 25, "AU10": 80})

    def test_project_change_during_stock_calculation_blocks_write(self):
        original = self.google.spreadsheets().values().get.side_effect
        reads = []

        def read(**kwargs):
            if kwargs["range"] == "'" + self.title + "'" and kwargs["valueRenderOption"] == "FORMATTED_VALUE":
                reads.append(1)
                if len(reads) == 2:
                    self.target_rows[8][3] = "TRIS"
            return original(**kwargs)

        self.google.spreadsheets().values().get.side_effect = read
        with self.assertRaisesRegex(ValueError, "изменилась"):
            self.export()
        self.assert_no_sheet_writes()

    def test_missing_sunday_writes_zero_without_neighbor_day_or_current_stock(self):
        with self.database.connect() as conn:
            conn.execute(
                "DELETE FROM marketplace_stock_daily_history WHERE day='2026-09-27' AND marketplace='WB' AND scheme='fbo'"
            )
            conn.execute(
                "INSERT INTO mp_stock (store_slug,marketplace,article,scheme,quantity) VALUES ('rimili','WB','123','fbo',999)"
            )
            conn.commit()
        result = self.export()
        self.assertEqual(self.writes(), {"AU9": 0})
        self.assertEqual(result["checked_cells"], 1)
        self.assertEqual(result["zeroed_cells"], 1)
        self.assertEqual(result["zero_filled"][0]["missing_days"], ["2026-09-27"])
        self.assertEqual(result["missing_data"], [])
        self.assertFalse(result["complete"])
        self.assertIsNone(self.repo.get_stock_state().last_success_slot)
        self.target_rows[8][46] = 0
        self.google.spreadsheets().values().batchUpdate.reset_mock()
        current = self.export()
        self.assertEqual(current["unchanged_cells"], 1)
        self.assertEqual(current["zeroed_cells"], 0)
        self.assert_no_sheet_writes()

    def test_explicit_zero_snapshot_is_written_and_same_value_is_not_rewritten(self):
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE marketplace_stock_daily_history SET quantity=0 WHERE marketplace='WB' AND scheme='fbo' AND day='2026-09-27'"
            )
            conn.commit()
        self.export()
        self.assertEqual(self.writes(), {"AU9": 0})
        self.target_rows[8][46] = 0
        self.google.spreadsheets().values().batchUpdate.reset_mock()
        report = self.export()
        self.assertTrue(report["complete"])
        self.assertEqual(report["unchanged_cells"], 1)
        self.assert_no_sheet_writes()

    def test_iso_year_boundary_and_future_sunday(self):
        self.rows[3][5] = self.target_rows[7][46] = "W01 2026"
        self.now = datetime(2026, 1, 10, tzinfo=self.week.MOSCOW_TIMEZONE)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO marketplace_stock_daily_history (store_slug,marketplace,article,scheme,day,quantity,captured_at) VALUES ('rimili','WB','123','fbo','2026-01-04',12,'2026-01-04T20:00:00+00:00')"
            )
            conn.commit()
        report = self.export()
        self.assertEqual(self.writes(), {"AU9": 12})
        self.assertEqual(report["periods"][0]["date_from"], "2025-12-29")
        self.now = datetime(2026, 1, 4, 23, 59, tzinfo=self.week.MOSCOW_TIMEZONE)
        self.google.spreadsheets().values().batchUpdate.reset_mock()
        self.assertFalse(self.export()["complete"])
        self.assert_no_sheet_writes()

    def test_week_is_read_from_original_sheet_not_same_address_on_target(self):
        self.target_rows[3] = ["", "", "", "", "", "W01 2025"]
        self.export()
        self.assertEqual(self.writes(), {"AU9": 25})
        # A week header at the same A1 address on a different sheet is valid.
        self.target_rows = [[] for _ in range(5)]
        self.target_rows[3] = ["ARTICLE", "", "Проект", "", "", "W39 2026"]
        self.target_rows[4] = ["123", "", "RIMILI", "", "", ""]
        self.export()
        self.assertEqual(self.writes(), {"F5": 25})

    def test_original_week_change_during_calculation_blocks_write(self):
        original = self.google.spreadsheets().values().get.side_effect
        reads = []

        def read(**kwargs):
            if kwargs["range"] == "'2. Факт продаж'":
                reads.append(1)
                if len(reads) == 2:
                    self.rows[3][5] = "W40 2026"
            return original(**kwargs)

        self.google.spreadsheets().values().get.side_effect = read
        with self.assertRaisesRegex(ValueError, "Исходная неделя изменилась"):
            self.export()
        self.assert_no_sheet_writes()

    def test_invalid_source_missing_week_or_ambiguous_columns_block_write(self):
        self.rows[3][5] = "not a week"
        with self.assertRaisesRegex(ValueError, "исходной ячейке"):
            self.export()
        self.rows[3][5] = "W40 2026"
        with self.assertRaisesRegex(ValueError, "найден"):
            self.export()
        self.rows[3][5] = "W39 2026"
        self.target_rows[7][2] = "Проект"
        with self.assertRaisesRegex(ValueError, "ровно один"):
            self.export()
        self.assert_no_sheet_writes()

    def test_missing_product_and_merged_target_are_not_written(self):
        self.target_rows[8][3] = "unknown"
        result = self.export()
        self.assertEqual(len(result["issues"]), 1)
        self.assert_no_sheet_writes()
        self.target_rows[8][3] = "RIMILI"
        self.target_sheet["merges"] = [
            {"startRowIndex": 8, "endRowIndex": 9, "startColumnIndex": 46, "endColumnIndex": 48}
        ]
        with self.assertRaisesRegex(ValueError, "объединена"):
            self.export()
        self.assert_no_sheet_writes()

    def test_missing_snapshot_zeroes_formula_with_backup_before_write(self):
        with self.database.connect() as conn:
            conn.execute("DELETE FROM marketplace_stock_daily_history WHERE day='2026-09-27'")
            conn.commit()
        self.target_formulas = copy.deepcopy(self.target_rows)
        self.target_formulas[8][46] = "=1+1"

        def execute(**kwargs):
            paths = list((self.core.DB_PATH.parent / "backups/google-week-stock").glob("*.json"))
            self.assertEqual(len(paths), 1)
            data = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(data["sheet_name"], self.title)
            self.assertEqual(data["cells"], [{"cell": "AU9", "value": 0, "previous": "=1+1"}])
            return {}

        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = execute
        self.export()
        self.assertEqual(self.writes(), {"AU9": 0})

    def test_dry_run_never_writes_or_changes_run_state(self):
        result = self.stock.export_stock(
            self.repo.get_settings(), self.repo.get_stock_state(), now=self.now, dry_run=True
        )
        self.assertEqual(result["writes"], [{"cell": "AU9", "value": 25}])
        self.assertIsNone(self.repo.get_stock_state().last_attempt_at)
        self.week._google_service.assert_called_once_with(read_only=True)
        self.assert_no_sheet_writes()

    def test_schedule_runs_independently_and_deduplicates(self):
        self.week.save_settings(self.repo.get_settings(), self.now - timedelta(days=8), stock_enabled=True)
        self.assertTrue(self.week.is_due(now=self.now))
        self.run_due(self.now)
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=10)))
        self.assertIsNone(self.repo.get_settings().last_success_at)

    def test_missing_snapshot_retries_after_five_minutes(self):
        self.week.save_settings(self.repo.get_settings(), self.now - timedelta(days=8), stock_enabled=True)
        with self.database.connect() as conn:
            conn.execute("DELETE FROM marketplace_stock_daily_history")
            conn.commit()
        self.run_due(self.now)
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=4)))
        self.assertTrue(self.week.is_due(now=self.now + timedelta(minutes=5)))

    def test_sales_failure_does_not_block_independent_stock_export(self):
        self.week.save_settings(
            self.repo.get_settings(), self.now - timedelta(days=8), stock_enabled=True, sales_enabled=True
        )
        self.mock(self.sales, "run_due", side_effect=ValueError("sales failure"))
        with self.assertRaisesRegex(ValueError, "sales failure"):
            self.run_due(self.now)
        self.assertEqual(self.writes(), {"AU9": 25})
        self.assertIsNotNone(self.repo.get_stock_state().last_success_slot)

    def test_week_write_failure_blocks_stock_until_success(self):
        self.week.save_settings(
            replace(self.repo.get_settings(), enabled=True), self.now - timedelta(days=8), stock_enabled=True
        )
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = TimeoutError()
        with self.assertRaises(ValueError):
            self.run_due(self.now)
        self.google.spreadsheets().values().get.assert_not_called()
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = None
        self.run_due(self.now + timedelta(minutes=5))
        self.assertEqual(self.writes(), {"AU9": 25})

    def test_save_new_sheet_preserves_existing_schedules_and_rejects_blank(self):
        client = self.client()
        form = {
            "spreadsheet_url": self.repo.get_settings().spreadsheet_url,
            "sheet_name": "2. Факт продаж",
            "cells": "F4",
            "weekday": "0",
            "run_time": "00:05",
            "stock_sheet_name": "Другой 'лист'",
            "stock_enabled": "1",
        }
        response = client.post("/admin/google-week-update", data=form)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.repo.get_stock_state().sheet_name, "Другой 'лист'")
        self.assertTrue(self.repo.get_stock_state().enabled)
        self.assertFalse(self.repo.get_settings().enabled)
        self.week._google_service.assert_not_called()
        form["stock_sheet_name"] = " "
        self.assertEqual(client.post("/admin/google-week-update", data=form).status_code, 400)
        self.assertEqual(self.repo.get_stock_state().sheet_name, "Другой 'лист'")

    def test_route_saved_destination_lock_permissions_and_persisted_report(self):
        client = self.client()
        response = client.post("/admin/google-week-update/stock", data={"stock_sheet_name": "ignored"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stock_result"]["sheet_name"], self.title)
        payload = client.get("/admin/google-week-update").json()
        self.assertIn("снимок за 2026-09-27", payload["stock_html"])
        self.assertIn("Время сохранения снимков FBO", payload["stock_html"])
        with self.week.locks.hold(self.week.JOB_NAME):
            self.assertEqual(client.post("/admin/google-week-update/stock").status_code, 409)
        self.week.save_settings(self.repo.get_settings(), stock_sheet_name="Другой лист")
        changed = client.get("/admin/google-week-update").json()
        self.assertNotIn("2026-09-27", changed["stock_html"])
        self.assertIn("Настройки назначения изменены", changed["stock_status_text"])
        self.user = self.user.model_copy(update={"role": "manager"})
        self.assertEqual(client.post("/admin/google-week-update/stock").status_code, 403)
        self.user = None
        self.assertEqual(client.post("/admin/google-week-update/stock").status_code, 401)


if __name__ == "__main__":
    unittest.main()
