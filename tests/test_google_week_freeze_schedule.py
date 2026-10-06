"""Freeze only after complete dependencies, with real SQLite state and fake Google calls."""

import importlib
import unittest
from dataclasses import replace
from datetime import timedelta

import test_google_week_update as update_tests


class GoogleWeekFreezeScheduleTests(unittest.TestCase):
    mock = update_tests.GoogleWeekUpdateTests.mock
    run_week = update_tests.GoogleWeekUpdateTests.run_week

    @classmethod
    def setUpClass(cls):
        update_tests.GoogleWeekUpdateTests.setUpClass.__func__(cls)
        cls.freeze = importlib.import_module("app.integrations.google_week_freeze")
        cls.freeze_repo = importlib.import_module("app.repositories.google_week_freeze")
        cls.sales = importlib.import_module("app.integrations.google_week_sales")
        cls.stock = importlib.import_module("app.integrations.google_week_stock")
        cls.search = importlib.import_module("app.integrations.google_week_search")

    def setUp(self):
        update_tests.GoogleWeekUpdateTests.setUp(self)
        self.freeze_call = self.mock(self.freeze, "freeze_sheets", return_value={"complete": True})
        self.sales_call = self.mock(self.sales, "export_orders", return_value={"complete": True})
        self.stock_call = self.mock(self.stock, "export_stock", return_value={"complete": True})
        self.mock(self.search, "read_headers", return_value={"sources": [], "complete": True})

    def configure(self, *, update=False, sales=False, stock=False, search=False):
        self.freeze_repo.initialize_selection(
            self.week.spreadsheet_id(self.repo.WeekUpdateSettings().spreadsheet_url), (11, 22)
        )
        self.week.save_settings(
            replace(self.repo.WeekUpdateSettings(), enabled=update),
            self.now - timedelta(days=4),
            sales_enabled=sales,
            stock_enabled=stock,
            search_enabled=search,
            freeze_enabled=True,
            freeze_sheet_ids=(11, 22),
        )
        self.sales_call.return_value = self.export_result()
        self.stock_call.return_value = self.export_result(stock=True)
        return self.repo.get_settings()

    def export_result(self, *, complete=True, stock=False):
        settings = self.repo.get_settings()
        return {
            "complete": complete,
            "spreadsheet_url": settings.spreadsheet_url,
            "source_sheet_name": settings.sheet_name,
            "sheet_name": self.repo.get_stock_state().sheet_name if stock else settings.sheet_name,
            "source_cells": list(settings.cells),
            "matching_key": self.search.MATCHING_KEY,
        }

    def test_freeze_runs_independently_once_per_slot(self):
        self.configure()
        self.assertTrue(self.week.is_due(now=self.now))
        self.assertTrue(self.run_week()["freeze"]["complete"])
        self.assertEqual(self.freeze_repo.get_state().last_success_slot, self.now.isoformat())
        self.assertEqual(self.run_week(now=self.now + timedelta(minutes=10)), {"skipped": True})
        self.assertFalse(self.week.is_due(now=self.now + timedelta(days=1)))
        self.assertTrue(self.week.is_due(now=self.now + timedelta(days=7)))
        self.freeze_call.assert_called_once()
        self.sales_call.assert_not_called()
        self.stock_call.assert_not_called()
        self.google.spreadsheets().values().batchUpdate.assert_not_called()

    def test_number_update_failure_blocks_freeze_until_retry_succeeds(self):
        self.configure(update=True)
        writer = self.google.spreadsheets().values().batchUpdate.return_value.execute
        writer.side_effect = TimeoutError()
        with self.assertRaises(ValueError):
            self.run_week()
        self.freeze_call.assert_not_called()
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=4)))
        self.assertEqual(self.run_week(now=self.now + timedelta(minutes=4)), {"skipped": True})
        writer.side_effect = None
        self.run_week(now=self.now + timedelta(minutes=5))
        self.freeze_call.assert_called_once()

    def test_partial_export_blocks_freeze_even_while_export_retry_is_not_due(self):
        settings = self.configure(update=True, sales=True, stock=True)
        self.sales_call.side_effect = [self.export_result(complete=False), self.export_result()]
        report = self.run_week()
        self.assertFalse(report["sales"]["complete"])
        self.assertTrue(report["stock"]["complete"])
        self.freeze_call.assert_not_called()
        self.assertFalse(self.week.freeze_dependencies_ready(settings, self.now))
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=4)))
        self.assertEqual(self.run_week(now=self.now + timedelta(minutes=4)), {"skipped": True})
        self.freeze_call.assert_not_called()
        self.assertTrue(self.week.is_due(now=self.now + timedelta(minutes=5)))
        self.run_week(now=self.now + timedelta(minutes=5))
        self.freeze_call.assert_called_once()
        self.stock_call.assert_called_once()
        self.assertEqual(self.sales_call.call_count, 2)
        self.google.spreadsheets().values().batchUpdate.assert_called_once()

    def test_failed_export_does_not_block_other_export_but_blocks_freeze(self):
        self.configure(sales=True, stock=True)
        self.sales_call.side_effect = ValueError("sales unavailable")
        with self.assertRaisesRegex(ValueError, "sales unavailable"):
            self.run_week()
        self.stock_call.assert_called_once()
        self.freeze_call.assert_not_called()
        self.sales_call.side_effect = None
        self.run_week(now=self.now + timedelta(minutes=5))
        self.stock_call.assert_called_once()
        self.freeze_call.assert_called_once()

    def test_failed_search_does_not_block_freeze_after_complete_exports(self):
        self.configure(sales=True, stock=True, search=True)
        self.search.read_headers.side_effect = ValueError("search unavailable")
        with self.assertRaisesRegex(ValueError, "search unavailable"):
            self.run_week()
        self.freeze_call.assert_called_once()
        self.assertEqual(self.freeze_repo.get_state().last_success_slot, self.now.isoformat())

    def test_failed_freeze_retries_without_repeating_successful_dependencies(self):
        self.configure(update=True, sales=True, stock=True)
        self.freeze_call.side_effect = [ValueError("freeze unavailable"), {"complete": True}]
        with self.assertRaisesRegex(ValueError, "freeze unavailable"):
            self.run_week()
        self.assertIsNotNone(self.freeze_repo.get_state().last_error)
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=4)))
        self.assertTrue(self.week.is_due(now=self.now + timedelta(minutes=5)))
        self.run_week(now=self.now + timedelta(minutes=5))
        self.assertEqual(self.freeze_call.call_count, 2)
        self.sales_call.assert_called_once()
        self.stock_call.assert_called_once()
        self.google.spreadsheets().values().batchUpdate.assert_called_once()
        self.assertIsNone(self.freeze_repo.get_state().last_error)

    def test_partial_freeze_retries_until_all_selected_sheets_complete(self):
        self.configure()
        self.freeze_call.side_effect = [{"complete": False}, {"complete": True}]
        self.run_week()
        self.assertIsNone(self.freeze_repo.get_state().last_success_slot)
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=4)))
        self.run_week(now=self.now + timedelta(minutes=5))
        self.assertEqual(self.freeze_repo.get_state().last_success_slot, self.now.isoformat())

    def test_latest_incomplete_or_failed_export_overrides_prior_success(self):
        settings = self.configure(sales=True)
        stamp = self.now.isoformat()
        self.repo.record_sales_attempt(stamp)
        self.repo.record_sales_result(stamp, slot=stamp, result=self.export_result())
        self.assertTrue(self.week.freeze_dependencies_ready(settings, self.now))
        newer = (self.now + timedelta(minutes=1)).isoformat()
        self.repo.record_sales_attempt(newer)
        self.repo.record_sales_result(newer, slot=stamp, result=self.export_result(complete=False))
        self.assertFalse(self.week.freeze_dependencies_ready(settings, self.now + timedelta(minutes=1)))
        self.repo.record_sales_result(newer, slot=stamp, result=self.export_result())
        self.repo.record_sales_result(newer, slot=stamp, error="latest export failed")
        self.assertFalse(self.week.freeze_dependencies_ready(settings, self.now + timedelta(minutes=1)))

    def test_previous_week_export_success_does_not_unlock_current_week(self):
        settings = self.configure(stock=True)
        old = (self.now - timedelta(days=7)).isoformat()
        self.repo.record_stock_attempt(old)
        self.repo.record_stock_result(old, slot=old, result=self.export_result(stock=True))
        self.assertFalse(self.week.freeze_dependencies_ready(settings, self.now))

    def test_changed_selection_clears_freeze_slot_and_keeps_manual_available(self):
        self.configure()
        self.run_week()
        self.week.save_settings(
            self.repo.get_settings(),
            self.now + timedelta(hours=1),
            freeze_sheet_ids=(22,),
            freeze_enabled=False,
        )
        self.assertIsNone(self.freeze_repo.get_state().last_success_slot)
        self.assertEqual(self.freeze_repo.get_state().sheet_ids, (22,))
        self.assertFalse(self.week.is_due(now=self.now + timedelta(days=7)))
        self.tracking.run_tracked(
            self.week.JOB_NAME, "manual", lambda: self.freeze.run_now(self.now + timedelta(hours=1))
        )
        self.assertEqual(self.freeze_call.call_count, 2)


if __name__ == "__main__":
    unittest.main()
