"""Local-only checks: temporary SQLite, synthetic Google responses, real route auth."""

import importlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class GoogleWeekUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.week = importlib.import_module("app.integrations.google_week_update")
            cls.repo = importlib.import_module("app.repositories.google_week_update")
            cls.core = importlib.import_module("app.repositories.core")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.orm = importlib.import_module("app.infrastructure.orm")
            cls.routes = importlib.import_module("app.web.routers.google_export")
            cls.background = importlib.import_module("app.jobs.background")
            cls.tracking = importlib.import_module("app.jobs.tracking")

    def mock(self, obj, name, **kwargs):
        mocker = patch.object(obj, name, **kwargs)
        result = mocker.start()
        self.addCleanup(mocker.stop)
        return result

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-week-test-")
        self.addCleanup(directory.cleanup)
        db_path = Path(directory.name) / "test.sqlite3"
        self.database = self.database_module.Database(db_path)
        self.addCleanup(self.database.dispose)
        self.orm.OrmBase.metadata.create_all(self.database.engine)
        self.mock(self.core, "DB_PATH", new=db_path)
        self.mock(self.core, "database_for_path", return_value=self.database)
        self.mock(self.week.locks, "database_for_path", return_value=self.database)
        self.now = datetime(2026, 10, 5, 0, 5, tzinfo=self.week.MOSCOW_TIMEZONE)
        self.google = MagicMock()
        self.google.spreadsheets().get.return_value.execute.return_value = {
            "sheets": [
                {
                    "properties": {
                        "sheetId": 616709520,
                        "title": "2. Факт продаж",
                        "gridProperties": {"rowCount": 985, "columnCount": 43},
                    }
                }
            ]
        }
        self.mock(self.week, "_google_service", return_value=self.google)

    def configure(self, **changes):
        settings = replace(self.repo.WeekUpdateSettings(), enabled=True, **changes)
        self.week.save_settings(settings, self.now - timedelta(days=4))
        return self.repo.get_settings()

    def run_week(self, *, now=None, manual=False):
        return self.tracking.run_tracked(
            self.week.JOB_NAME,
            "manual" if manual else "scheduled",
            lambda: (self.week.run_now if manual else self.week.run_due)(now or self.now),
        )

    def test_iso_week_and_year_follow_moscow_completed_week(self):
        cases = [
            ("2026-10-01T12:00:00+03:00", "W39 2026"),
            ("2026-10-04T23:59:59+03:00", "W39 2026"),
            ("2026-10-04T21:00:00+00:00", "W40 2026"),
            ("2027-01-01T12:00:00+03:00", "W52 2026"),
            ("2027-01-04T00:05:00+03:00", "W53 2026"),
            ("2027-01-11T00:05:00+03:00", "W01 2027"),
        ]
        for stamp, expected in cases:
            with self.subTest(stamp=stamp):
                self.assertEqual(self.week.last_completed_week(datetime.fromisoformat(stamp)), expected)

    def test_default_is_disabled_and_never_contacts_google(self):
        self.assertFalse(self.week.is_due(now=self.now))
        self.assertEqual(self.run_week(), {"skipped": True})
        with self.assertRaisesRegex(ValueError, "Сначала сохраните"):
            self.run_week(manual=True)
        self.week._google_service.assert_not_called()

    def test_exact_schedule_and_only_one_run_per_slot(self):
        settings = self.configure()
        self.assertFalse(self.week.is_due(settings, self.now - timedelta(seconds=1)))
        self.assertTrue(self.week.is_due(settings, self.now))
        self.run_week()
        self.assertFalse(self.week.is_due(now=self.now + timedelta(days=1)))
        self.assertTrue(self.week.is_due(now=self.now + timedelta(days=7)))
        self.assertEqual(self.run_week(now=self.now + timedelta(minutes=10)), {"skipped": True})
        self.google.spreadsheets().values().batchUpdate.assert_called_once()

    def test_changes_start_at_next_slot_and_restart_catches_up(self):
        self.configure()
        self.assertTrue(self.week.is_due(now=self.now + timedelta(days=2)))
        # Catch up once with the latest completed week after several weeks offline.
        self.assertEqual(self.run_week(now=self.now + timedelta(days=15))["value"], "W42 2026")
        self.assertFalse(self.week.is_due(now=self.now + timedelta(days=16)))
        self.week.save_settings(replace(self.repo.get_settings(), weekday=2, run_time="09:30"), self.now)
        future = self.now.replace(hour=9, minute=30) + timedelta(days=2)
        self.assertEqual(self.week.next_run_at(self.repo.get_settings(), self.now), future)

    def test_batch_writes_only_selected_cells_raw_and_preserves_other_data(self):
        self.configure()
        result = self.run_week()
        self.assertEqual(result["value"], "W40 2026")
        self.google.spreadsheets().values().batchUpdate.assert_called_once_with(
            spreadsheetId="1ifWg6lhhbLANLArnqSAI5QJDksgBdNVcnyoHmkY8CjE",
            body={
                "valueInputOption": "RAW",
                "data": [
                    {"range": "'2. Факт продаж'!F4", "values": [["W40 2026"]]},
                    {"range": "'2. Факт продаж'!AI10", "values": [["W40 2026"]]},
                ],
            },
        )
        self.google.spreadsheets().batchUpdate.assert_not_called()
        self.google.spreadsheets().values().batchClear.assert_not_called()
        settings = self.repo.get_settings()
        self.assertEqual(settings.last_value, "W40 2026")
        self.assertEqual(settings.last_success_slot, self.now.isoformat())

    def test_missing_sheet_or_invalid_cell_does_not_write(self):
        for changes in ({"sheet_name": "Missing"}, {"cells": ("AR1",)}, {"cells": ("A986",)}):
            with self.subTest(changes=changes):
                self.configure(**changes)
                with self.assertRaises(ValueError):
                    self.run_week(manual=True)
                self.google.spreadsheets().values().batchUpdate.assert_not_called()
                self.assertIsNotNone(self.repo.get_settings().last_error)

    def test_quoted_sheet_names_and_deduplicated_cells(self):
        title = "Продажи 'WB'"
        self.google.spreadsheets().get.return_value.execute.return_value["sheets"][0]["properties"][
            "title"
        ] = title
        self.configure(sheet_name=title, cells=("f4", "F4", "ai10"))
        self.run_week()
        data = self.google.spreadsheets().values().batchUpdate.call_args.kwargs["body"]["data"]
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["range"], "'Продажи ''WB'''!F4")

    def test_merged_cells_require_top_left(self):
        self.google.spreadsheets().get.return_value.execute.return_value["sheets"][0]["merges"] = [
            {"startRowIndex": 3, "endRowIndex": 5, "startColumnIndex": 5, "endColumnIndex": 7}
        ]
        self.configure(cells=("G4",))
        with self.assertRaisesRegex(ValueError, "верхнюю левую"):
            self.run_week()
        self.google.spreadsheets().values().batchUpdate.assert_not_called()
        self.configure(cells=("F4",))
        self.run_week(manual=True)
        self.google.spreadsheets().values().batchUpdate.assert_called_once()

    def test_failure_retries_and_preserves_previous_success(self):
        self.configure()
        self.run_week()
        next_week = self.now + timedelta(days=7)
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = TimeoutError()
        with self.assertRaisesRegex(ValueError, "TimeoutError"):
            self.run_week(now=next_week)
        failed = self.repo.get_settings()
        self.assertEqual(failed.last_value, "W40 2026")
        self.assertEqual(failed.last_success_at, self.now.isoformat())
        self.assertFalse(self.week.is_due(now=next_week + timedelta(minutes=4)))
        self.assertEqual(self.week.next_run_at(failed, next_week), next_week + timedelta(minutes=5))
        self.assertTrue(self.week.is_due(now=next_week + timedelta(minutes=5)))
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = None
        self.run_week(now=next_week + timedelta(minutes=5))
        self.assertEqual(self.repo.get_settings().last_value, "W41 2026")
        self.assertIsNone(self.repo.get_settings().last_error)

    def test_manual_run_when_disabled_and_save_preserves_history(self):
        self.week.save_settings(self.repo.WeekUpdateSettings(), self.now - timedelta(days=1))
        self.run_week(manual=True)
        self.week.save_settings(replace(self.repo.get_settings(), run_time="10:30"), self.now)
        self.assertEqual(self.repo.get_settings().last_value, "W40 2026")
        self.assertFalse(self.week.is_due(now=self.now + timedelta(days=7)))

    def test_shared_lock_blocks_duplicate_run_and_settings_change(self):
        self.configure()
        with self.week.locks.hold(self.week.JOB_NAME):
            with self.assertRaises(self.week.locks.SyncJobBusyError):
                self.run_week()
            with self.assertRaises(self.week.locks.SyncJobBusyError):
                self.week.save_settings(self.repo.get_settings())
        self.week._google_service.assert_not_called()

    def test_validation_rejects_ranges_urls_and_bad_schedule(self):
        settings = self.repo.WeekUpdateSettings()
        for changes in (
            {"cells": ("A1:B2",)},
            {"cells": ("Other!A1",)},
            {"cells": ("A0",)},
            {"cells": ()},
            {"spreadsheet_url": "https://evil.example/spreadsheets/d/123/edit"},
            {"spreadsheet_url": "javascript:alert(1)"},
            {"sheet_name": ""},
            {"weekday": 7},
            {"run_time": "24:00"},
            {"run_time": "00:60"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.week.save_settings(replace(settings, **changes))
        self.assertEqual(self.repo.get_settings().updated_at, "")

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.access.auth import SESSION_COOKIE
        from app.dto.identity import User
        from app.web.middleware import authentication_middleware

        self.user = User(
            id=1, login="admin", full_name="Admin", role="superadmin", created_at=datetime.now(UTC)
        )
        app = FastAPI()
        app.state.container = SimpleNamespace(identity=SimpleNamespace(user_for_token=lambda _: self.user))
        app.middleware("http")(authentication_middleware)
        app.include_router(self.routes.router)
        client = TestClient(app, headers={"accept": "application/json"})
        client.cookies.set(SESSION_COOKIE, "test-session")
        self.addCleanup(client.close)
        self.mock(self.routes.db, "log_action")
        return client

    def test_routes_require_superadmin_for_status_save_and_run(self):
        client = self.client()
        self.user = self.user.model_copy(update={"role": "manager"})
        for path, method in (("", "get"), ("", "post"), ("/run", "post")):
            self.assertEqual(getattr(client, method)("/admin/google-week-update" + path).status_code, 403)
        self.user = None
        self.assertEqual(client.post("/admin/google-week-update/run").status_code, 401)
        self.week._google_service.assert_not_called()

    def test_routes_save_without_writing_google_and_manual_uses_saved_settings(self):
        client = self.client()
        response = client.post(
            "/admin/google-week-update",
            data={
                "spreadsheet_url": self.repo.WeekUpdateSettings().spreadsheet_url,
                "sheet_name": "2. Факт продаж",
                "cells": "f4, AI10",
                "weekday": "0",
                "run_time": "00:05",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["saved"])
        self.week._google_service.assert_not_called()
        with patch.object(self.week, "_now", return_value=self.now):
            response = client.post("/admin/google-week-update/run", data={"cells": "Z99"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["report"]["cells"], ["F4", "AI10"])
        self.assertEqual(client.get("/admin/google-week-update").headers["cache-control"], "no-store")

    def test_routes_invalid_input_and_busy_conflict(self):
        client = self.client()
        self.assertEqual(client.post("/admin/google-week-update", data={"weekday": "bad"}).status_code, 400)
        self.configure()
        with self.week.locks.hold(self.week.JOB_NAME):
            self.assertEqual(client.post("/admin/google-week-update/run").status_code, 409)

    def test_separate_card_shows_defaults_and_escapes_settings(self):
        content = self.routes._render_week_update()
        for expected in ("Автообновление недели", 'value="F4, AI10"', 'value="00:05"', "2. Факт продаж"):
            self.assertIn(expected, content)
        self.configure(sheet_name='<img src=x onerror="alert(1)">')
        self.assertNotIn("<img src=x", self.routes._render_week_update())

    def test_background_job_does_not_wait_for_marketplace_catalogs(self):
        import asyncio

        job = next(job for job in self.background._jobs(asyncio.Event()) if job.name == self.week.JOB_NAME)
        self.assertIsNone(job.ready_event)
        self.assertFalse(job.is_enabled())
        self.assertEqual(job.next_delay(), 60)


if __name__ == "__main__":
    unittest.main()
