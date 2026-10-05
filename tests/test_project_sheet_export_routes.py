"""Unified Google editor, access control and scheduler entry points without external calls."""

import importlib
import os
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch


class ProjectSheetExportRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.routes = importlib.import_module("app.web.routers.google_export")
            cls.export = importlib.import_module("app.exports.project_sheet")
            cls.jobs = importlib.import_module("app.jobs.catalog")
            cls.background = importlib.import_module("app.jobs.background")
            cls.manual = importlib.import_module("app.jobs.manual")
            cls.User = importlib.import_module("app.dto.identity").User

    def mock(self, obj, name, **kwargs):
        mocker = patch.object(obj, name, **kwargs)
        value = mocker.start()
        self.addCleanup(mocker.stop)
        return value

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        self.user = self.User(
            id=1,
            login="synthetic",
            full_name="Synthetic administrator",
            role="superadmin",
            created_at=datetime.now(UTC),
        )
        app = FastAPI()

        @app.middleware("http")
        async def user_context(request, call_next):
            request.state.user = self.user
            return await call_next(request)

        app.include_router(self.routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.settings = self.export.default_settings(datetime(2026, 10, 5, tzinfo=UTC))
        self.mock(self.export, "get_settings", return_value=self.settings)
        self.saver = self.mock(self.export, "save_settings", side_effect=self.export.validate_settings)
        self.runner = self.mock(self.export, "run_export", return_value={"marketplaces": []})
        self.tracked = self.mock(
            self.routes, "run_tracked", side_effect=lambda name, source, callback: callback()
        )
        self.mock(self.routes.db, "log_action")

    def form(self, **changes):
        return {
            "wb_spreadsheet_url": "https://docs.google.com/spreadsheets/d/wb-test/edit",
            "ozon_spreadsheet_url": "https://docs.google.com/spreadsheets/d/ozon-test/edit",
            "yandex_spreadsheet_url": "https://docs.google.com/spreadsheets/d/yandex-test/edit",
            "enabled": "1",
            "schedule_kind": "daily",
            "weekday": "0",
            "run_time": "09:30",
            "wb_sheet_name": "WB Stocks",
            "wb_fbs_orders_sheet_name": "WB Orders",
            "ozon_sheet_name": "Ozon Stocks",
            "ozon_fbs_orders_sheet_name": "Ozon Orders",
            "yandex_sheet_name": "YM Stocks",
            "yandex_fbs_orders_sheet_name": "YM Orders",
            **changes,
        }

    def test_shared_settings_route_precedes_legacy_dynamic_route(self):
        response = self.client.post("/admin/google-export/settings", data=self.form())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        settings = self.saver.call_args.args[0]
        for marketplace, prefix in self.routes.MARKETPLACE_FORM_PREFIXES.items():
            self.assertEqual(
                settings.target(marketplace).spreadsheet_url, self.form()[f"{prefix}_spreadsheet_url"]
            )
        self.assertEqual(settings.target("WB").stock_sheet_name, "WB Stocks")
        self.assertEqual(settings.target("OZON").orders_sheet_name, "Ozon Orders")
        self.assertEqual(settings.target("YANDEX MARKET").orders_sheet_name, "YM Orders")
        self.runner.assert_not_called()

    def test_shared_form_preserves_execution_status(self):
        existing = replace(
            self.settings,
            last_attempt_at="2026-10-04T01:00:00+03:00",
            last_success_at="2026-10-04T01:01:00+03:00",
            last_error="Historical error",
        )
        parsed = self.routes._project_settings_from_form(self.form(), existing)
        self.assertEqual(
            (parsed.last_attempt_at, parsed.last_success_at, parsed.last_error),
            (existing.last_attempt_at, existing.last_success_at, existing.last_error),
        )

    def test_reused_sheet_returns_validation_error(self):
        response = self.client.post(
            "/admin/google-export/settings", data=self.form(yandex_fbs_orders_sheet_name="YM Stocks")
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])

    def test_same_sheet_names_are_allowed_in_different_platform_files(self):
        response = self.client.post(
            "/admin/google-export/settings",
            data=self.form(
                wb_sheet_name="Stocks",
                ozon_sheet_name="Stocks",
                yandex_sheet_name="Stocks",
                wb_fbs_orders_sheet_name="Orders",
                ozon_fbs_orders_sheet_name="Orders",
                yandex_fbs_orders_sheet_name="Orders",
            ),
        )
        self.assertEqual(response.status_code, 200)

    def test_sheet_without_platform_file_returns_validation_error(self):
        response = self.client.post("/admin/google-export/settings", data=self.form(ozon_spreadsheet_url=""))
        self.assertEqual(response.status_code, 400)

    def test_full_and_scoped_runs_use_the_same_global_job(self):
        for data in ({}, {"marketplace": "OZON", "export_kind": "stocks"}):
            with self.subTest(data=data):
                response = self.client.post("/admin/google-export/run", data=data)
                self.assertEqual(response.status_code, 200)
                self.runner.assert_called_with(
                    marketplace=data.get("marketplace"), export_kind=data.get("export_kind")
                )
                self.assertEqual(self.tracked.call_args.args[:2], ("stock_sheet_export", "manual"))

    def test_invalid_or_half_scopes_do_not_start_a_run(self):
        for data in (
            {"marketplace": "WB"},
            {"export_kind": "stocks"},
            {"marketplace": "UNKNOWN", "export_kind": "stocks"},
            {"marketplace": "WB", "export_kind": "all"},
        ):
            with self.subTest(data=data):
                self.assertEqual(self.client.post("/admin/google-export/run", data=data).status_code, 400)
        self.runner.assert_not_called()

    def test_retired_store_routes_cannot_write_or_export(self):
        old_save = self.mock(self.routes.stock_sheet_export, "save_settings")
        old_run = self.mock(self.routes.stock_sheet_export, "run_store")
        for path in (
            "/admin/google-export/rimili",
            "/admin/google-export/rockkiddo/run",
            "/admin/google-export/toyka/run",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data=self.form()).status_code, 410)
        self.saver.assert_not_called()
        self.runner.assert_not_called()
        old_save.assert_not_called()
        old_run.assert_not_called()

    def test_non_superadmin_cannot_save_or_run_any_export_route(self):
        self.user = self.user.model_copy(update={"role": "admin"})
        for path in (
            "/admin/google-export/settings",
            "/admin/google-export/run",
            "/admin/google-export/rimili/run",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, data=self.form()).status_code, 403)
        self.saver.assert_not_called()
        self.runner.assert_not_called()

    def test_busy_export_reports_conflict_when_saving_or_running(self):
        self.saver.side_effect = self.routes.SyncJobBusyError()
        response = self.client.post("/admin/google-export/settings", data=self.form())
        self.assertEqual(response.status_code, 409)
        self.runner.side_effect = self.routes.SyncJobBusyError()
        self.assertEqual(self.client.post("/admin/google-export/run").status_code, 409)

    def test_single_editor_has_three_platform_urls_and_all_projects(self):
        rendered = self.routes._render_project_export(self.settings)
        self.assertNotIn('name="spreadsheet_url"', rendered)
        self.assertEqual(rendered.count("data-export-form"), 1)
        for field in (
            "wb_spreadsheet_url",
            "ozon_spreadsheet_url",
            "yandex_spreadsheet_url",
            "wb_sheet_name",
            "ozon_sheet_name",
            "yandex_sheet_name",
            "wb_fbs_orders_sheet_name",
            "ozon_fbs_orders_sheet_name",
            "yandex_fbs_orders_sheet_name",
        ):
            self.assertEqual(rendered.count(f'name="{field}"'), 1)
        for store in self.routes.STORES.values():
            self.assertIn(store.name, rendered)
        self.assertNotIn("data-store=", rendered)

    def test_global_job_definition_has_no_legacy_project_target_switches(self):
        job = next(job for job in self.jobs.job_definitions() if job.name == "stock_sheet_export")
        self.assertEqual(job.scope, "global")
        self.assertEqual(job.marketplaces, ())

    def test_background_and_manual_entry_points_delegate_once_without_store_filters(self):
        self.assertIs(self.background.stock_sheet_export, self.export)
        due = self.mock(self.export, "run_due", return_value={"scheduled": True})
        enabled = self.mock(
            self.background.sync_settings,
            "enabled_stores",
            side_effect=AssertionError("Legacy project switches cannot filter export"),
        )
        self.assertEqual(self.background._run_stock_sheet_export_configured(), {"scheduled": True})
        due.assert_called_once_with()
        self.manual._sheets_now()
        self.runner.assert_called_once_with()
        enabled.assert_not_called()


if __name__ == "__main__":
    unittest.main()
