"""Sheet metadata/selection tests use only a temporary database and synthetic Google responses."""

import importlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


class GoogleSheetCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.catalog = importlib.import_module("app.integrations.google_sheet_catalog")
            cls.week = importlib.import_module("app.integrations.google_week_update")
            cls.repo = importlib.import_module("app.repositories.google_week_freeze")
            cls.week_repo = importlib.import_module("app.repositories.google_week_update")
            cls.core = importlib.import_module("app.repositories.core")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.orm = importlib.import_module("app.infrastructure.orm")

    def mock(self, obj, name, **kwargs):
        patcher = patch.object(obj, name, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-sheet-catalog-")
        self.addCleanup(directory.cleanup)
        db_path = Path(directory.name) / "test.sqlite3"
        self.database = self.database_module.Database(db_path)
        self.addCleanup(self.database.dispose)
        self.orm.OrmBase.metadata.create_all(self.database.engine)
        self.mock(self.core, "DB_PATH", new=db_path)
        self.mock(self.core, "database_for_path", return_value=self.database)
        self.mock(self.week.locks, "database_for_path", return_value=self.database)
        self.now = datetime(2026, 10, 5, 12, tzinfo=self.week.MOSCOW_TIMEZONE)
        self.google = MagicMock()
        self.sheets = [
            {"properties": {"sheetId": i, "title": title, "sheetType": "GRID"}}
            for i, title in enumerate(self.catalog.DEFAULT_SHEET_NAMES)
        ] + [{"properties": {"sheetId": 15, "title": "Другая вкладка"}}]
        self.google.spreadsheets().get.return_value.execute.side_effect = lambda **_: {"sheets": self.sheets}
        self.mock(self.week, "_google_service", return_value=self.google)
        self.week.save_settings(self.week_repo.WeekUpdateSettings(), self.now - timedelta(days=1))
        self.settings = self.week_repo.get_settings()

    def refresh(self, now=None):
        return self.catalog.get_catalog(self.settings, refresh=True, now=now or self.now)

    def test_cached_reads_do_not_contact_google_and_defaults_are_selected_once(self):
        self.assertEqual(self.catalog.get_catalog(self.settings, now=self.now)["sheets"], [])
        self.week._google_service.assert_not_called()
        loaded = self.refresh()
        self.assertEqual(loaded["selected_sheet_ids"], list(range(9)))
        self.assertTrue(loaded["selection_initialized"])
        self.assertFalse(self.repo.get_state().enabled)
        self.assertFalse(loaded["refresh_due"])
        self.week._google_service.assert_called_once_with(read_only=True)
        self.catalog.get_catalog(self.settings, now=self.now)
        self.week._google_service.assert_called_once()

    def test_renaming_removal_and_new_tabs_preserve_explicit_selection(self):
        self.refresh()
        self.week.save_settings(self.settings, self.now, freeze_sheet_ids=(0, 15), freeze_enabled=True)
        self.sheets[0]["properties"]["title"] = "Переименованная вкладка"
        self.sheets = [sheet for sheet in self.sheets if sheet["properties"]["sheetId"] != 15]
        self.sheets.append({"properties": {"sheetId": 16, "title": "Новая вкладка"}})
        loaded = self.refresh(self.now + timedelta(days=1))
        self.assertEqual(loaded["selected_sheet_ids"], [0, 15])
        self.assertEqual(loaded["sheets"][0]["title"], "Переименованная вкладка")
        self.assertTrue(self.repo.get_state().enabled)
        # Missing selections stay removable, and do not block unrelated settings saves.
        self.week.save_settings(self.settings, self.now, freeze_sheet_ids=(0, 15))

    def test_empty_user_selection_survives_refresh(self):
        self.refresh()
        self.week.save_settings(self.settings, self.now, freeze_sheet_ids=(), freeze_enabled=False)
        self.assertEqual(self.refresh(self.now + timedelta(days=1))["selected_sheet_ids"], [])
        self.assertTrue(self.repo.get_state().selection_initialized)
        with self.assertRaisesRegex(ValueError, "хотя бы один"):
            self.week.save_settings(self.settings, self.now, freeze_enabled=True)

    def test_document_change_never_reuses_equal_ids_and_resets_schedule(self):
        self.refresh()
        self.week.save_settings(self.settings, self.now, freeze_sheet_ids=(0, 15), freeze_enabled=True)
        other = replace(self.settings, spreadsheet_url="https://docs.google.com/spreadsheets/d/other/edit")
        self.week.save_settings(other, self.now, freeze_sheet_ids=(0, 15), freeze_enabled=True)
        state = self.repo.get_state()
        self.assertEqual(state.spreadsheet_id, "other")
        self.assertEqual(state.sheet_ids, ())
        self.assertFalse(state.enabled)
        self.assertFalse(state.selection_initialized)
        self.assertEqual(self.catalog.get_catalog(other, now=self.now)["sheets"], [])

    def test_invalid_selection_does_not_partially_save_other_settings(self):
        self.refresh()
        original = self.week_repo.get_settings()
        for invalid in ((999,), (-1,), (True,), ("0",)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.week.save_settings(
                    replace(original, run_time="12:00"), self.now, freeze_sheet_ids=invalid
                )
            self.assertEqual(self.week_repo.get_settings(), original)

    def test_refresh_failure_preserves_cache_and_retries_after_five_minutes(self):
        loaded = self.refresh()
        tomorrow = self.now + timedelta(days=1)
        self.google.spreadsheets().get.return_value.execute.side_effect = TimeoutError()
        with self.assertRaisesRegex(ValueError, "TimeoutError"):
            self.refresh(tomorrow)
        cached = self.catalog.get_catalog(self.settings, now=tomorrow)
        self.assertEqual(cached["sheets"], loaded["sheets"])
        self.assertEqual(cached["updated_at"], loaded["updated_at"])
        self.assertEqual(cached["selected_sheet_ids"], loaded["selected_sheet_ids"])
        self.assertIsNotNone(cached["last_error"])
        self.assertFalse(self.catalog.is_due(tomorrow + timedelta(minutes=4)))
        self.assertTrue(self.catalog.is_due(tomorrow + timedelta(minutes=5)))

    def test_daily_refresh_uses_moscow_day_not_host_or_utc_day(self):
        self.refresh(self.now.replace(hour=23, minute=50))
        self.assertFalse(self.catalog.is_due(datetime.fromisoformat("2026-10-05T20:59:59+00:00")))
        self.assertTrue(self.catalog.is_due(datetime.fromisoformat("2026-10-05T21:00:00+00:00")))

    def test_background_refresh_runs_when_week_actions_disabled_and_uses_shared_lock(self):
        self.assertFalse(self.settings.enabled)
        self.assertTrue(self.catalog.is_due(self.now))
        with self.week.locks.hold(self.week.JOB_NAME):
            with self.assertRaises(self.week.locks.SyncJobBusyError):
                self.catalog.refresh_if_due(self.now)
        self.week._google_service.assert_not_called()
        self.catalog.refresh_if_due(self.now)
        self.assertEqual(self.catalog.refresh_if_due(self.now), {"skipped": True})

    def test_data_source_tabs_are_not_selectable(self):
        self.sheets.append({"properties": {"sheetId": 99, "title": "Data", "sheetType": "DATA_SOURCE"}})
        self.assertNotIn(99, [sheet["sheet_id"] for sheet in self.refresh()["sheets"]])


if __name__ == "__main__":
    unittest.main()
