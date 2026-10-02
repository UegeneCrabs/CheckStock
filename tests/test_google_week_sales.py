"""Net order totals, precise write destinations, partial data and scheduled exports."""

import copy
import importlib
import json
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import test_google_week_search as search_tests


class GoogleWeekSalesTests(unittest.TestCase):
    mock = search_tests.GoogleWeekSearchTests.mock
    configure = search_tests.GoogleWeekSearchTests.configure
    client = search_tests.GoogleWeekSearchTests.client
    assert_no_sheet_writes = search_tests.GoogleWeekSearchTests.assert_no_sheet_writes
    run_due = search_tests.GoogleWeekSearchTests.run_due

    @classmethod
    def setUpClass(cls):
        search_tests.GoogleWeekSearchTests.setUpClass.__func__(cls)
        cls.sales = importlib.import_module("app.integrations.google_week_sales")

    def setUp(self):
        search_tests.GoogleWeekSearchTests.setUp(self)
        self.configure()
        self.now = self.now - timedelta(days=3)  # Friday 2 October; W39 is complete.
        self.rows.append(["123", "", "RIMILI"] + [""] * 32)
        self.formulas = None

        def read(**kwargs):
            request = MagicMock()
            values = (
                self.formulas if kwargs["valueRenderOption"] == "FORMULA" and self.formulas else self.rows
            )
            request.execute.return_value = {"values": copy.deepcopy(values)}
            return request

        self.google.spreadsheets().values().get.side_effect = read
        self.mock(self.sales, "app_settings", new=SimpleNamespace(database_path=self.core.DB_PATH))
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO stock_items (store_slug,marketplace,article,barcode,name) VALUES ('rimili','WB','123','001234','Product')"
            )
            for i in range(84):
                day = (date(2026, 7, 6) + timedelta(days=i)).isoformat()
                conn.execute(
                    "INSERT INTO economics_source_days (marketplace,store_slug,source,day,updated_at) VALUES ('WB','rimili','orders',?,?)",
                    (day, self.now.isoformat()),
                )
            for i in range(7):
                day = (date(2026, 9, 21) + timedelta(days=i)).isoformat()
                conn.execute(
                    "INSERT INTO wb_funnel_daily_orders (store_slug,article,day,orders_count,cancel_count,source_version,updated_at) VALUES ('rimili','123',?,10,2,4,?)",
                    (day, self.now.isoformat()),
                )
            conn.commit()

    def export(self):
        return self.tracking.run_tracked(self.week.JOB_NAME, "manual", lambda: self.sales.run_now(self.now))

    def writes(self):
        call = self.google.spreadsheets().values().batchUpdate.call_args
        return {item["range"].split("!")[1]: item["values"][0][0] for item in call.kwargs["body"]["data"]}

    def test_net_orders_write_to_correct_cells_and_backup_precedes_write(self):
        self.formulas = copy.deepcopy(self.rows)
        self.formulas[10][34] = "=SUM(A1:A2)"

        def verify_backup(**kwargs):
            backups = list((self.core.DB_PATH.parent / "backups/google-week-sales").glob("*.json"))
            self.assertEqual(len(backups), 1)
            saved = json.loads(backups[0].read_text(encoding="utf-8"))
            self.assertEqual(
                next(c for c in saved["cells"] if c["cell"] == "AI11")["previous"], "=SUM(A1:A2)"
            )

        self.google.spreadsheets().values().batchUpdate.side_effect = verify_backup
        # Return a normal request after verifying the on-disk backup.
        request = MagicMock()
        self.google.spreadsheets().values().batchUpdate.side_effect = lambda **kw: (
            verify_backup(**kw),
            request,
        )[1]
        report = self.export()
        self.assertEqual(self.writes()["AI11"], 56)
        self.assertEqual(self.writes()["AH11"], 0)
        self.assertEqual(
            set(self.writes()),
            {f"{col}11" for col in ("AI", "AH", "AG", "AF", "AE", "AD", "AC", "AB", "AA", "Z", "Y", "X")},
        )
        self.assertTrue(report["complete"])
        self.assertEqual(report["written_cells"], 12)
        self.assertEqual(self.repo.get_sales_state().result, report)
        self.google.spreadsheets().batchUpdate.assert_not_called()
        self.google.spreadsheets().values().batchClear.assert_not_called()
        self.assertEqual(
            self.google.spreadsheets().values().batchUpdate.call_args.kwargs["body"]["valueInputOption"],
            "RAW",
        )

    def test_iso_weeks_include_both_boundary_days_and_previous_calendar_year(self):
        self.assertEqual(self.sales.week_dates("W39 2026"), (date(2026, 9, 21), date(2026, 9, 27)))
        self.assertEqual(self.sales.week_dates("W01 2026"), (date(2025, 12, 29), date(2026, 1, 4)))
        self.assertEqual(self.sales.week_dates("W53 2026"), (date(2026, 12, 28), date(2027, 1, 3)))
        with self.assertRaises(ValueError):
            self.sales.week_dates("W53 2025")
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO wb_funnel_daily_orders (store_slug,article,day,orders_count,cancel_count,source_version,updated_at) VALUES ('rimili','123','2026-09-28',999,0,4,?)",
                (self.now.isoformat(),),
            )
            conn.execute(
                "UPDATE wb_funnel_daily_orders SET orders_count=1,cancel_count=5 WHERE day='2026-09-21'"
            )
            conn.commit()
        self.export()
        self.assertEqual(self.writes()["AI11"], 44)  # -4 + six days at 8; no clamping or second subtraction.

    def test_missing_day_counts_as_zero_and_partial_run_can_retry(self):
        with self.database.connect() as conn:
            conn.execute("DELETE FROM wb_funnel_daily_orders WHERE day='2026-09-23'")
            conn.execute("DELETE FROM economics_source_days WHERE day='2026-09-23'")
            conn.commit()
        report = self.export()
        self.assertEqual(self.writes()["AI11"], 48)
        self.assertEqual(report["zero_filled"][0]["missing_days"], ["2026-09-23"])
        self.assertEqual(report["missing_data"], [])
        self.assertFalse(report["complete"])
        self.assertIsNone(self.repo.get_sales_state().last_success_slot)

    def test_missing_week_zeroes_stale_blank_formula_text_and_boolean_cells(self):
        with self.database.connect() as conn:
            conn.execute("DELETE FROM wb_funnel_daily_orders")
            conn.execute("DELETE FROM economics_source_days")
            conn.commit()
        self.rows[10][23:35] = [999, "", 11, "0", False, 0] + [0] * 6
        self.formulas = copy.deepcopy(self.rows)
        self.formulas[10][25] = "=5+6"
        report = self.export()
        self.assertEqual(self.writes(), {"X11": 0, "Y11": 0, "Z11": 0, "AA11": 0, "AB11": 0})
        self.assertTrue(all(type(value) is int for value in self.writes().values()))
        self.assertEqual(report["checked_cells"], 12)
        self.assertEqual(report["written_cells"], 5)
        self.assertEqual(report["zeroed_cells"], 5)
        self.assertEqual(report["unchanged_cells"], 7)
        self.assertEqual(len(report["zero_filled"]), 12)
        backup = self.core.DB_PATH.parent / "backups/google-week-sales" / report["backup"]
        previous = {
            item["cell"]: item["previous"] for item in json.loads(backup.read_text(encoding="utf-8"))["cells"]
        }
        self.assertEqual(previous, {"X11": 999, "Y11": "", "Z11": "=5+6", "AA11": "0", "AB11": False})
        html = self.client().get("/admin/google-week-update").json()["sales_html"]
        self.assertIn("Проверено ячеек: <strong>12</strong>", html)
        self.assertIn("обнулено: <strong>5</strong>", html)
        self.assertIn("Ноль вместо отсутствующих данных", html)
        self.assertNotIn("Соответствующие ячейки не изменены", html)

    def test_unfinished_week_is_not_zeroed(self):
        self.now -= timedelta(days=5)  # Sunday 27 September: W39 is not complete yet.
        report = self.export()
        self.assertNotIn("AI11", self.writes())
        self.assertEqual(report["missing_data"][0]["cell"], "AI11")
        self.assertEqual(report["zero_filled"], [])

    def test_already_current_numbers_do_not_trigger_another_write_or_backup(self):
        self.rows[10][23:35] = [0] * 11 + [56]
        report = self.export()
        self.assertTrue(report["complete"])
        self.assertEqual(report["unchanged_cells"], 12)
        self.assertEqual(report["written_cells"], 0)
        self.assertIsNone(report["backup"])
        self.assert_no_sheet_writes()

    def test_saving_sales_schedule_does_not_enable_other_actions_or_contact_google(self):
        client = self.client()
        response = client.post(
            "/admin/google-week-update",
            data={
                "spreadsheet_url": self.repo.get_settings().spreadsheet_url,
                "sheet_name": "2. Факт продаж",
                "cells": "F4",
                "weekday": "0",
                "run_time": "00:05",
                "sales_enabled": "1",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.repo.get_sales_state().enabled)
        self.assertFalse(self.repo.get_search_state().enabled)
        self.assertFalse(self.repo.get_settings().enabled)
        self.week._google_service.assert_not_called()

    def test_legacy_daily_rows_are_valid_only_when_raw_cancellations_are_available(self):
        with self.database.connect() as conn:
            conn.execute("DELETE FROM economics_source_days")
            conn.execute("UPDATE wb_funnel_daily_orders SET source_version=3")
            conn.commit()
        report = self.export()
        self.assertEqual(self.writes()["AI11"], 56)
        self.assertEqual(len(self.writes()), 12)
        self.assertEqual(len(report["zero_filled"]), 11)
        with self.database.connect() as conn:
            conn.execute("UPDATE wb_funnel_daily_orders SET source_version=2 WHERE day='2026-09-21'")
            conn.commit()
        self.google.spreadsheets().values().batchUpdate.reset_mock()
        report = self.export()
        self.assertEqual(self.writes()["AI11"], 48)
        self.assertEqual(len(report["zero_filled"]), 12)

    def test_article_and_project_isolate_stores_and_ignore_conflicting_barcodes(self):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO stock_items (store_slug,marketplace,article,barcode,name) VALUES ('tris','WB','123','other','Other store')"
            )
            conn.execute(
                "INSERT INTO catalog_article_aliases (store_slug,marketplace,article,target_article,identity,updated_at) VALUES ('rimili','WB','999','123','alias',?)",
                (self.now.isoformat(),),
            )
            conn.execute(
                "INSERT INTO wb_funnel_daily_orders (store_slug,article,day,orders_count,cancel_count,source_version,updated_at) SELECT 'tris',article,day,3,0,4,updated_at FROM wb_funnel_daily_orders WHERE store_slug='rimili'"
            )
            conn.commit()
        self.rows[9][1] = self.rows[9][3] = "BARCODE"
        self.rows[10][0:4] = ["999", "other", " ХоЧуШар ", "invalid barcode"]
        self.rows.append(["123", "001234", " tris ", ""])  # Same ARTICLE, different project.
        self.export()
        self.assertEqual(self.writes()["AI11"], 56)
        self.assertEqual(self.writes()["AI12"], 21)

    def test_project_aliases_match_existing_store_labels(self):
        products = [
            {"store_slug": slug, "article": "123"}
            for slug in ("rimili", "tris", "trusthome", "gogol", "sokoloff", "rockkiddo", "toyka")
        ]
        self.mock(self.sales.source, "products", return_value=products)
        for project, store in (
            (" ХОЧУШАР ", "rimili"),
            ("RIMILI", "rimili"),
            ("TRIS", "tris"),
            (" bth ", "trusthome"),
            ("Гоголь", "gogol"),
            ("Ракета", "gogol"),
            ("Sokoloff", "sokoloff"),
            ("rockkiddo", "rockkiddo"),
            ("TOYKA", "toyka"),
        ):
            with self.subTest(project=project):
                matched, issues = self.sales.match_products(
                    [["ARTICLE", "Проект"], ["123", project]], 0, 0, 1
                )
                self.assertEqual(issues, [])
                self.assertEqual(matched[0]["store_slug"], store)

    def test_missing_unknown_or_wrong_project_never_guesses_store_from_article(self):
        for project in ("", "unknown", "TRIS"):
            with self.subTest(project=project):
                self.rows[10][2] = project
                report = self.export()
                self.assertEqual(report["written_cells"], 0)
                self.assertEqual(len(report["issues"]), 1)
                self.assert_no_sheet_writes()
        self.rows[10][0:3] = ["", "", "RIMILI"]
        self.assertIn("Нужно заполнить", self.export()["issues"][0]["reason"])

    def test_ambiguous_article_alias_in_one_project_is_not_written(self):
        self.mock(
            self.sales.source,
            "products",
            return_value=[
                {"store_slug": "rimili", "article": "123"},
                {"store_slug": "rimili", "article": "456", "article_aliases": ["123"]},
            ],
        )
        report = self.export()
        self.assertIn("несколькими", report["issues"][0]["reason"])
        self.assert_no_sheet_writes()

    def test_project_change_during_calculation_stops_write(self):
        original = self.google.spreadsheets().values().get.side_effect
        reads = []

        def mutate(**kwargs):
            if kwargs["valueRenderOption"] == "FORMATTED_VALUE":
                reads.append(1)
                if len(reads) == 2:
                    self.rows[10][2] = "TRIS"
            return original(**kwargs)

        self.google.spreadsheets().values().get.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "изменилась"):
            self.export()
        self.assert_no_sheet_writes()

    def test_old_identity_report_is_hidden_until_export_is_repeated(self):
        report = self.export()
        report.pop("matching_key")
        self.repo.record_sales_result(self.now.isoformat(), slot=self.now.isoformat(), result=report)
        payload = self.client().get("/admin/google-week-update").json()
        self.assertIn("Ключ сопоставления изменён", payload["sales_status_text"])
        self.assertNotIn("Записано ячеек", payload["sales_html"])

    def test_unknown_and_duplicate_product_rows_remain_untouched(self):
        self.rows.append(copy.deepcopy(self.rows[10]))
        self.rows.append(["unknown", "", "other"])
        report = self.export()
        self.assertEqual(len(report["issues"]), 3)
        self.assertEqual(report["written_cells"], 0)
        self.assert_no_sheet_writes()

    def test_ambiguous_header_rows_and_missing_columns_block_all_writes(self):
        self.rows[9][2] = "OTHER"
        with self.assertRaisesRegex(ValueError, "ARTICLE.*Проект"):
            self.export()
        self.rows[9][2] = "Проект"
        self.rows.append(["W39 2026"])
        with self.assertRaisesRegex(ValueError, "одной строке"):
            self.export()
        self.assert_no_sheet_writes()

    def test_merged_destination_and_source_cell_are_never_written(self):
        sheet = self.google.spreadsheets().get.return_value.execute.return_value["sheets"][0]
        sheet["merges"] = [
            {"startRowIndex": 10, "endRowIndex": 11, "startColumnIndex": 34, "endColumnIndex": 36}
        ]
        with self.assertRaisesRegex(ValueError, "объединена"):
            self.export()
        self.assert_no_sheet_writes()

    def test_sheet_changed_during_calculation_stops_write(self):
        original = self.google.spreadsheets().values().get.side_effect
        reads = []

        def mutate(**kwargs):
            if kwargs["valueRenderOption"] == "FORMATTED_VALUE":
                reads.append(1)
                if len(reads) == 2:
                    self.rows[10][0] = "changed"
            return original(**kwargs)

        self.google.spreadsheets().values().get.side_effect = mutate
        with self.assertRaisesRegex(ValueError, "изменилась"):
            self.export()
        self.assert_no_sheet_writes()

    def test_backup_failure_prevents_google_write_and_failed_write_keeps_backup(self):
        backup = self.mock(self.sales, "save_backup", side_effect=OSError("disk"))
        with self.assertRaisesRegex(ValueError, "OSError"):
            self.export()
        self.assert_no_sheet_writes()
        backup.side_effect = None
        backup.return_value = "saved.json"
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = TimeoutError()
        with self.assertRaisesRegex(ValueError, "TimeoutError"):
            self.export()
        self.assertIsNone(self.repo.get_sales_state().last_success_slot)
        self.assertIn("TimeoutError", self.repo.get_sales_state().last_error)

    def test_dry_run_reads_real_plan_but_never_writes_or_changes_state(self):
        report = self.sales.export_orders(self.repo.get_settings(), now=self.now, dry_run=True)
        self.assertEqual(next(v["value"] for v in report["writes"] if v["cell"] == "AI11"), 56)
        self.assertIsNone(self.repo.get_sales_state().last_attempt_at)
        self.week._google_service.assert_called_once_with(read_only=True)
        self.assert_no_sheet_writes()

    def test_sales_schedule_independent_from_search_and_week_write(self):
        settings = self.repo.get_settings()
        self.week.save_settings(settings, self.now - timedelta(days=8), sales_enabled=True)
        self.assertTrue(self.week.is_due(now=self.now))
        self.run_due(self.now)
        self.assertIsNone(self.repo.get_settings().last_success_at)
        self.assertIsNone(self.repo.get_search_state().last_success_at)
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=10)))

    def test_week_write_precedes_sales_and_failure_delays_export(self):
        settings = self.repo.get_settings()
        from dataclasses import replace

        self.week.save_settings(
            replace(settings, enabled=True), self.now - timedelta(days=8), sales_enabled=True
        )
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = TimeoutError()
        with self.assertRaises(ValueError):
            self.run_due(self.now)
        self.google.spreadsheets().values().get.assert_not_called()
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=4)))
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = None
        self.run_due(self.now + timedelta(minutes=5))
        calls = self.google.spreadsheets().values().batchUpdate.call_args_list
        self.assertEqual(calls[-2].kwargs["body"]["data"][0]["range"], "'2. Факт продаж'!F4")
        self.assertEqual(calls[-1].kwargs["body"]["data"][0]["range"], "'2. Факт продаж'!AI11")

    def test_endpoint_saved_destination_permissions_lock_and_persisted_status(self):
        client = self.client()
        response = client.post("/admin/google-week-update/sales", data={"cells": "Z99"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sales_result"]["source_cells"], ["F4"])
        self.assertIn("Записано ячеек", client.get("/admin/google-week-update").json()["sales_html"])
        with self.week.locks.hold(self.week.JOB_NAME):
            self.assertEqual(client.post("/admin/google-week-update/sales").status_code, 409)
        self.user = self.user.model_copy(update={"role": "manager"})
        self.assertEqual(client.post("/admin/google-week-update/sales").status_code, 403)
        self.user = None
        self.assertEqual(client.post("/admin/google-week-update/sales").status_code, 401)


if __name__ == "__main__":
    unittest.main()
