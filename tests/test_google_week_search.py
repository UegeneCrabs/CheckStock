"""Search-only and scheduling checks with temporary databases and synthetic Sheets."""

import importlib
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def sample_rows():
    rows = [[] for _ in range(10)]
    rows[3] = ["", "", "", "", "", "W39 2026"]
    rows[9] = [""] * 22 + ["Общий сток"] + [f"W{week} 2026" for week in range(28, 40)]
    rows[9][0] = "ARTICLE"
    rows[9][2] = "BARCODE"
    return rows


class GoogleWeekSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.search = importlib.import_module("app.integrations.google_week_search")
            cls.week = importlib.import_module("app.integrations.google_week_update")
            cls.repo = importlib.import_module("app.repositories.google_week_update")
            cls.core = importlib.import_module("app.repositories.core")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.orm = importlib.import_module("app.infrastructure.orm")
            cls.routes = importlib.import_module("app.web.routers.google_export")
            cls.tracking = importlib.import_module("app.jobs.tracking")

    def mock(self, obj, name, **kwargs):
        mocker = patch.object(obj, name, **kwargs)
        result = mocker.start()
        self.addCleanup(mocker.stop)
        return result

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-week-search-")
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
        self.rows = sample_rows()
        self.google.spreadsheets().values().get.return_value.execute.side_effect = lambda **_: {
            "values": self.rows
        }
        self.mock(self.week, "_google_service", return_value=self.google)

    def configure(self, *, enabled=False, search_enabled=False, **changes):
        settings = replace(self.repo.WeekUpdateSettings(), enabled=enabled, cells=("F4",), **changes)
        self.week.save_settings(settings, self.now - timedelta(days=4), search_enabled=search_enabled)
        return self.repo.get_settings()

    def run_search(self, now=None):
        return self.tracking.run_tracked(
            self.week.JOB_NAME,
            "manual",
            lambda: self.search.run_now(now or self.now),
        )

    def run_due(self, now=None):
        return self.tracking.run_tracked(
            self.week.JOB_NAME,
            "scheduled",
            lambda: self.week.run_due(now or self.now),
        )

    def assert_no_sheet_writes(self):
        self.google.spreadsheets().batchUpdate.assert_not_called()
        self.google.spreadsheets().values().batchUpdate.assert_not_called()
        self.google.spreadsheets().values().update.assert_not_called()
        self.google.spreadsheets().values().batchClear.assert_not_called()

    def test_real_scenario_reads_source_and_finds_all_twelve_headers(self):
        self.configure()
        result = self.run_search()
        self.assertEqual(result["total_cells"], 12)
        self.assertEqual(result["total_matches"], 1)
        source = result["sources"][0]
        self.assertEqual((source["cell"], source["value"]), ("F4", "W39 2026"))
        match = source["matches"][0]
        self.assertEqual((match["cell"], match["range"]), ("AI10", "X10:AI10"))
        self.assertEqual(
            [cell["value"] for cell in match["headers"]], [f"W{w} 2026" for w in range(39, 27, -1)]
        )
        self.assertEqual(
            [cell["cell"] for cell in match["headers"]],
            ["AI10", "AH10", "AG10", "AF10", "AE10", "AD10", "AC10", "AB10", "AA10", "Z10", "Y10", "X10"],
        )
        self.assertEqual(match["stop"], {"cell": "W10", "value": "Общий сток", "reason": "format"})
        self.assertEqual(match["row"], 10)
        self.assertEqual(
            match["columns"],
            {
                "ARTICLE": [{"cell": "A10", "value": "ARTICLE"}],
                "BARCODE": [{"cell": "C10", "value": "BARCODE"}],
            },
        )
        self.assertEqual(self.repo.get_search_state().result, result)
        self.week._google_service.assert_called_once_with(read_only=True)
        self.assert_no_sheet_writes()

    def test_multiple_matches_exclude_origin_and_never_cross_blank(self):
        rows = [["W39 2026"], ["W37 2026", "", "W38 2026", "W39 2026"], ["W38 2026", "W39 2026"]]
        matches = self.search.find_headers(rows, ("A1",))[0]["matches"]
        self.assertEqual([item["range"] for item in matches], ["C2:D2", "A3:B3"])
        self.assertEqual(matches[0]["stop"]["cell"], "B2")
        self.assertIsNone(matches[1]["stop"])

    def test_columns_use_entire_week_row_not_source_or_neighboring_rows(self):
        rows = [
            ["W39 2026", "ARTICLE", "BARCODE"],
            ["ARTICLE", "BARCODE"],
            [" article ", "Общий сток", "W38 2026", "W39 2026", " Barcode "],
        ]
        match = self.search.find_headers(rows, ("A1",))[0]["matches"][0]
        self.assertEqual(match["row"], 3)
        self.assertEqual(match["range"], "C3:D3")
        self.assertEqual(match["stop"]["cell"], "B3")
        self.assertEqual(
            match["columns"],
            {
                "ARTICLE": [{"cell": "A3", "value": " article "}],
                "BARCODE": [{"cell": "E3", "value": " Barcode "}],
            },
        )

    def test_columns_keep_duplicates_and_do_not_accept_partial_names_or_other_rows(self):
        rows = [
            ["W39 2026", "BARCODE"],
            ["ARTICLE", "W39 2026", "ARTICLE", "BARCODE_OLD", "MY ARTICLE"],
            ["BARCODE", "W39 2026"],
        ]
        matches = self.search.find_headers(rows, ("A1",))[0]["matches"]
        self.assertEqual([cell["cell"] for cell in matches[0]["columns"]["ARTICLE"]], ["A2", "C2"])
        self.assertEqual(matches[0]["columns"]["BARCODE"], [])
        self.assertEqual(matches[1]["columns"]["ARTICLE"], [])
        self.assertEqual(matches[1]["columns"]["BARCODE"], [{"cell": "A3", "value": "BARCODE"}])

    def test_column_output_handles_missing_headers_and_older_saved_results(self):
        from app.web.google_week_search import render_result

        self.configure()
        self.rows[9][2] = "BARCODE_OLD"
        result = self.run_search()
        content = render_result(result)
        self.assertIn("Столбцы в строке 10", content)
        self.assertIn("range=A10", content)
        self.assertIn("<strong>BARCODE</strong> — не найден", content)
        del result["sources"][0]["matches"][0]["columns"]
        self.assertIn("повторите поиск", render_result(result))

    def test_iso_year_boundary_and_invalid_week_stops_scan(self):
        rows = [["W01 2027"], ["W53 2025", "W52 2026", "W53 2026", "W01 2027"]]
        match = self.search.find_headers(rows, ("A1",))[0]["matches"][0]
        self.assertEqual(match["range"], "B2:D2")
        self.assertEqual(match["stop"]["value"], "W53 2025")

    def test_source_itself_is_excluded_even_when_it_is_left_of_match(self):
        match = self.search.find_headers([["W39 2026", "W38 2026", "W39 2026"]], ("A1",))[0]["matches"][0]
        self.assertEqual(match["range"], "B1:C1")
        self.assertEqual(match["stop"]["reason"], "source")

    def test_invalid_source_and_no_match_are_explicit_results(self):
        result = self.search.find_headers([["W39 2026", "W99 2026", "текст"]], ("A1", "B1", "C1", "Z20"))
        self.assertEqual(result[0]["matches"], [])
        for source in result[1:]:
            self.assertIn("issue", source)
        self.assertEqual(result[-1]["value"], "")

    def test_multiple_sources_keep_their_own_matches(self):
        result = self.search.find_headers([["W39 2026"], ["W38 2026", "W39 2026"]], ("A1", "B2"))
        self.assertEqual(result[0]["matches"][0]["range"], "A2:B2")
        self.assertEqual(result[1]["matches"][0]["cell"], "A1")

    def test_search_schedule_works_with_week_writing_disabled_and_deduplicates(self):
        self.configure(search_enabled=True)
        self.assertFalse(self.week.is_due(now=self.now - timedelta(seconds=1)))
        self.assertTrue(self.week.is_due(now=self.now + timedelta(days=1)))
        report = self.run_due(now=self.now + timedelta(days=1))
        self.assertEqual(report["search"]["total_cells"], 12)
        self.assertFalse(self.week.is_due(now=self.now + timedelta(days=2)))
        self.assertEqual(self.run_due(now=self.now + timedelta(days=2)), {"skipped": True})
        self.assertTrue(self.week.is_due(now=self.now + timedelta(days=7)))
        self.assertIsNone(self.repo.get_settings().last_success_at)
        self.assert_no_sheet_writes()

    def test_write_before_search_and_search_retry_does_not_repeat_write(self):
        self.configure(enabled=True, search_enabled=True)
        events = []
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = lambda **_: (
            events.append("write")
        )

        def read(**_):
            events.append("read")
            raise TimeoutError()

        self.google.spreadsheets().values().get.return_value.execute.side_effect = read
        with self.assertRaisesRegex(ValueError, "TimeoutError"):
            self.run_due()
        self.assertEqual(events, ["write", "read"])
        self.assertEqual(self.repo.get_settings().last_value, "W40 2026")
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=4)))
        self.google.spreadsheets().values().get.return_value.execute.side_effect = lambda **_: {
            "values": self.rows
        }
        self.run_due(now=self.now + timedelta(minutes=5))
        self.google.spreadsheets().values().batchUpdate.assert_called_once()
        self.assertIsNone(self.repo.get_search_state().last_error)

    def test_failed_week_write_delays_search_until_successful_retry(self):
        self.configure(enabled=True, search_enabled=True)
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = TimeoutError()
        with self.assertRaises(ValueError):
            self.run_due()
        self.assertFalse(self.week.is_due(now=self.now + timedelta(minutes=1)))
        self.assertEqual(self.run_due(now=self.now + timedelta(minutes=1)), {"skipped": True})
        self.google.spreadsheets().values().get.assert_not_called()
        self.google.spreadsheets().values().batchUpdate.return_value.execute.side_effect = None
        self.run_due(now=self.now + timedelta(minutes=5))
        self.google.spreadsheets().values().get.assert_called_once()

    def test_failed_search_preserves_previous_success_with_error(self):
        self.configure(search_enabled=True)
        first = self.run_search()
        self.google.spreadsheets().values().get.return_value.execute.side_effect = TimeoutError()
        with self.assertRaises(ValueError):
            self.run_search(now=self.now + timedelta(days=1))
        state = self.repo.get_search_state()
        self.assertEqual(state.result, first)
        self.assertEqual(state.last_success_at, self.now.isoformat())
        self.assertIn("TimeoutError", state.last_error)

    def test_missing_sheet_and_unsaved_config_do_not_write(self):
        with self.assertRaisesRegex(ValueError, "Сначала сохраните"):
            self.run_search()
        self.configure(sheet_name="missing")
        with self.assertRaisesRegex(ValueError, "не найден"):
            self.run_search()
        self.google.spreadsheets().values().get.assert_not_called()
        self.assert_no_sheet_writes()

    def test_shared_lock_prevents_search_while_settings_or_update_are_running(self):
        self.configure()
        with self.week.locks.hold(self.week.JOB_NAME), self.assertRaises(self.week.locks.SyncJobBusyError):
            self.run_search()
        self.week._google_service.assert_not_called()

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

    def test_button_uses_saved_cells_and_get_restores_result_after_reload(self):
        self.configure()
        client = self.client()
        response = client.post("/admin/google-week-update/search", data={"cells": "Z99"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["search_result"]["source_cells"], ["F4"])
        restored = client.get("/admin/google-week-update").json()
        self.assertIn("Столбцы в строке 10", restored["search_html"])
        self.assertIn("range=A10", restored["search_html"])
        self.assertIn("range=C10", restored["search_html"])
        self.assertIn("X10:AI10", restored["search_html"])
        self.assertIn("W10", restored["search_html"])
        self.assertIn("X10:AI10", self.routes._render_week_update())
        self.assert_no_sheet_writes()

    def test_search_permissions(self):
        client = self.client()
        self.user = self.user.model_copy(update={"role": "manager"})
        self.assertEqual(client.post("/admin/google-week-update/search").status_code, 403)
        self.user = None
        self.assertEqual(client.post("/admin/google-week-update/search").status_code, 401)
        self.week._google_service.assert_not_called()

    def test_changed_destination_does_not_show_old_headers_as_current(self):
        self.configure()
        self.run_search()
        self.week.save_settings(replace(self.repo.get_settings(), sheet_name="Другой лист"), self.now)
        state = self.client().get("/admin/google-week-update").json()
        self.assertIn("выполните новый поиск", state["search_status_text"])
        self.assertNotIn("X10:AI10", state["search_html"])

    def test_sheet_content_is_escaped_in_rendered_result(self):
        self.configure()
        self.rows[9][22] = '<img src=x onerror="alert(1)">'
        self.run_search()
        content = self.routes._render_week_update()
        self.assertNotIn("<img src=x", content)
        self.assertIn("&lt;img", content)

    def test_saving_search_flag_preserves_week_settings_without_google_calls(self):
        client = self.client()
        response = client.post(
            "/admin/google-week-update",
            data={
                "spreadsheet_url": self.repo.WeekUpdateSettings().spreadsheet_url,
                "sheet_name": "2. Факт продаж",
                "cells": "F4",
                "weekday": "2",
                "run_time": "11:15",
                "search_enabled": "1",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.repo.get_search_state().enabled)
        self.assertFalse(self.repo.get_settings().enabled)
        self.assertEqual((self.repo.get_settings().weekday, self.repo.get_settings().run_time), (2, "11:15"))
        self.week._google_service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
