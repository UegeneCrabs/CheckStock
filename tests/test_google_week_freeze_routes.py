"""Local route auth, catalog reads, selected-document binding and escaped freeze reports."""

import importlib
import unittest
from unittest.mock import patch

import test_google_week_update as week_tests


class GoogleWeekFreezeRouteTests(unittest.TestCase):
    mock = week_tests.GoogleWeekUpdateTests.mock
    setUp = week_tests.GoogleWeekUpdateTests.setUp
    configure = week_tests.GoogleWeekUpdateTests.configure
    client = week_tests.GoogleWeekUpdateTests.client

    @classmethod
    def setUpClass(cls):
        week_tests.GoogleWeekUpdateTests.setUpClass.__func__(cls)
        cls.freeze_repo = importlib.import_module("app.repositories.google_week_freeze")
        cls.freeze = importlib.import_module("app.integrations.google_week_freeze")
        cls.catalog = importlib.import_module("app.integrations.google_sheet_catalog")

    def configure_selection(self):
        settings = self.configure()
        self.doc_id = self.week.spreadsheet_id(settings.spreadsheet_url)
        self.freeze_repo.record_catalog_attempt(self.doc_id, self.now.isoformat())
        self.freeze_repo.record_catalog_result(
            self.doc_id,
            self.now.isoformat(),
            sheets=[{"sheet_id": 11, "title": "Сток"}, {"sheet_id": 12, "title": "Прогноз"}],
        )
        self.freeze_repo.initialize_selection(self.doc_id, (11,))

    def form(self, **changes):
        settings = self.repo.get_settings()
        return {
            "spreadsheet_url": settings.spreadsheet_url,
            "sheet_name": settings.sheet_name,
            "cells": ",".join(settings.cells),
            "weekday": "0",
            "run_time": "00:05",
            "freeze_sheet_ids": "[11, 12]",
            "freeze_selection_initialized": "1",
            "freeze_spreadsheet_id": self.doc_id,
            **changes,
        }

    def test_all_new_routes_require_superadmin_and_authentication(self):
        client = self.client()
        endpoints = [
            ("get", "/sheets"),
            ("post", "/sheets/refresh"),
            ("post", "/freeze/preview"),
            ("post", "/freeze"),
        ]
        self.user = self.user.model_copy(update={"role": "manager"})
        for method, path in endpoints:
            self.assertEqual(getattr(client, method)("/admin/google-week-update" + path).status_code, 403)
        self.user = None
        for method, path in endpoints:
            self.assertEqual(getattr(client, method)("/admin/google-week-update" + path).status_code, 401)
        self.week._google_service.assert_not_called()

    def test_render_status_and_catalog_are_cached_and_do_not_contact_google(self):
        self.configure_selection()
        client = self.client()
        state = client.get("/admin/google-week-update").json()
        catalog = client.get("/admin/google-week-update/sheets")
        self.assertEqual(catalog.headers["cache-control"], "no-store")
        self.assertEqual(catalog.json()["sheet_catalog"]["selected_sheet_ids"], [11])
        self.assertIn("Проверить диапазоны", state["freeze_html"])
        markup = self.routes._render_week_update()
        for expected in (
            "Фиксация прошлых недель",
            'name="freeze_sheet_ids"',
            "data-freeze-search",
            "data-freeze-clear-found",
        ):
            self.assertIn(expected, markup)
        self.week._google_service.assert_not_called()

    def test_refresh_reads_google_metadata_and_uses_shared_lock(self):
        self.configure()
        props = self.google.spreadsheets().get.return_value.execute.return_value["sheets"][0]["properties"]
        props["title"] = self.catalog.DEFAULT_SHEET_NAMES[0]
        client = self.client()
        with self.week.locks.hold(self.week.JOB_NAME):
            self.assertEqual(client.post("/admin/google-week-update/sheets/refresh").status_code, 409)
        self.week._google_service.assert_not_called()
        response = client.post("/admin/google-week-update/sheets/refresh")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sheet_catalog"]["selected_sheet_ids"], [616709520])
        self.week._google_service.assert_called_once_with(read_only=True)
        self.google.spreadsheets().batchUpdate.assert_not_called()
        self.google.spreadsheets().values().batchUpdate.assert_not_called()

    def test_save_and_explicit_empty_selection_preserve_intent(self):
        self.configure_selection()
        client = self.client()
        response = client.post("/admin/google-week-update", data=self.form(freeze_enabled="1"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.freeze_repo.get_state().sheet_ids, (11, 12))
        self.assertTrue(self.freeze_repo.get_state().enabled)
        response = client.post("/admin/google-week-update", data=self.form(freeze_sheet_ids="[]"))
        self.assertEqual(response.status_code, 200, response.text)
        state = self.freeze_repo.get_state()
        self.assertEqual(state.sheet_ids, ())
        self.assertTrue(state.selection_initialized)
        self.assertFalse(state.enabled)
        self.week._google_service.assert_not_called()

    def test_changing_document_never_reuses_old_selected_ids(self):
        self.configure_selection()
        response = self.client().post(
            "/admin/google-week-update",
            data=self.form(
                spreadsheet_url="https://docs.google.com/spreadsheets/d/new-doc/edit",
                freeze_enabled="1",
            ),
        )
        self.assertEqual(response.status_code, 200, response.text)
        state = self.freeze_repo.get_state()
        self.assertEqual(state.spreadsheet_id, "new-doc")
        self.assertEqual(state.sheet_ids, ())
        self.assertFalse(state.enabled)
        self.assertFalse(state.selection_initialized)

    def test_bad_selection_payload_cannot_save_settings(self):
        self.configure_selection()
        client = self.client()
        for value in ('{"11":true}', "[true]", '["11"]', "[-1]", "broken"):
            with self.subTest(value=value):
                response = client.post("/admin/google-week-update", data=self.form(freeze_sheet_ids=value))
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.freeze_repo.get_state().sheet_ids, (11,))

    def report(self, *, preview):
        return {
            "processed_at": self.now.isoformat(),
            "week": "W39 2026",
            "complete": False,
            "dry_run": preview,
            "total_formulas": 42,
            "written_cells": 0 if preview else 42,
            "sheets": [
                {
                    "sheet_id": 11,
                    "sheet_name": '<img src=x onerror="alert(1)">',
                    "ranges": ["'Сток'!I1:J100"],
                    "formula_count": 42,
                    "written_cells": 0,
                    "issue": "<script>bad</script>",
                }
            ],
        }

    def test_manual_and_preview_actions_use_saved_settings_and_escaped_report(self):
        self.configure_selection()
        client = self.client()
        for suffix, method, preview in (("/preview", "preview_now", True), ("", "run_now", False)):
            with (
                self.subTest(suffix=suffix),
                patch.object(self.freeze, method, return_value=self.report(preview=preview)) as action,
            ):
                response = client.post(
                    "/admin/google-week-update/freeze" + suffix, data={"freeze_sheet_ids": "[999]"}
                )
                self.assertEqual(response.status_code, 200, response.text)
                action.assert_called_once_with()
                markup = response.json()["freeze_html"]
                self.assertIn("&lt;img", markup)
                self.assertIn("&lt;script&gt;", markup)
                self.assertNotIn("<img", markup)
                self.assertIn("42", markup)
                self.assertEqual(response.json()["freeze_result"]["dry_run"], preview)
        self.week._google_service.assert_not_called()

    def test_freeze_and_preview_lock_conflicts_and_validation_errors(self):
        self.configure_selection()
        client = self.client()
        with self.week.locks.hold(self.week.JOB_NAME):
            for suffix in ("", "/preview"):
                self.assertEqual(client.post("/admin/google-week-update/freeze" + suffix).status_code, 409)
        with patch.object(self.freeze, "preview_now", side_effect=ValueError("Проверка не прошла")):
            response = client.post("/admin/google-week-update/freeze/preview")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Проверка не прошла")
        self.assertIn("freeze_html", response.json())
        self.week._google_service.assert_not_called()


if __name__ == "__main__":
    unittest.main()
