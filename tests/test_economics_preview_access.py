"""Private rollout checks against the real authentication middleware and routes."""

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import dotenv

dotenv.dotenv_values = lambda *_args, **_kwargs: {}
os.environ["CHECKSTOCK_DATABASE_URL"] = ""

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.access import economics_preview as preview  # noqa: E402
from app.dto.identity import User  # noqa: E402
from app.web.middleware import authentication_middleware  # noqa: E402
from app.web.routers import economics_calendar as calendar  # noqa: E402
from app.web.routers import integrations  # noqa: E402
from app.web.routers import unit_economics as economics  # noqa: E402


class PreviewAccessTests(unittest.TestCase):
    def setUp(self):
        self.user = self.account(1)
        self.patches = [
            patch.object(
                preview, "settings", preview.settings.model_copy(update={"economics_preview_user_id": 1})
            ),
            patch.object(
                economics, "render_page", side_effect=lambda _title, _active, content, *_a, **_k: content
            ),
            patch.object(
                calendar, "render_page", side_effect=lambda _title, _active, content, *_a, **_k: content
            ),
            patch.object(economics, "_unit_economics_1c_price_warnings", return_value=[]),
            patch.object(economics.db, "get_ui_preference", return_value=None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        app = FastAPI()
        app.state.container = SimpleNamespace(
            identity=SimpleNamespace(user_for_token=lambda _token: self.user)
        )
        app.middleware("http")(authentication_middleware)
        for router in (economics.router, calendar.router, integrations.router):
            app.include_router(router)
        self.client = TestClient(app, headers={"accept": "application/json"})
        # Use the application's cookie name, which can vary between installations.
        from app.access.auth import SESSION_COOKIE

        self.client.cookies.set(SESSION_COOKIE, "preview-test-session")
        self.addCleanup(self.client.close)

    @staticmethod
    def account(user_id, **changes):
        return User(
            id=user_id,
            login=changes.pop("login", "preview"),
            full_name="Preview user",
            role=changes.pop("role", "superadmin"),
            created_at=datetime.now(UTC),
            **changes,
        )

    def test_id_required_even_for_other_superadmins_or_matching_login(self):
        self.assertTrue(preview.can_preview(self.user))
        self.assertFalse(preview.can_preview(self.account(2, login="UegeneAdmin")))
        self.assertFalse(preview.can_preview(self.account(1, is_active=False)))
        self.assertFalse(preview.can_preview(None))
        with patch.object(
            preview, "settings", preview.settings.model_copy(update={"economics_preview_user_id": 0})
        ):
            self.assertFalse(preview.can_preview(self.user))

    def test_calendar_links_and_pages_are_private(self):
        for user_id in (1, 2):
            self.user = self.account(user_id)
            for suffix in ("", "/yandex-market"):
                base = "/sales/unit-economics-1c" + suffix
                response = self.client.get(base)
                self.assertEqual(response.status_code, 200)
                self.assertEqual('id="ue1c-calendar-link"' in response.text, user_id == 1)
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                calendar_page = self.client.get(base + "?calendar=1")
                self.assertEqual(calendar_page.status_code, 200 if user_id == 1 else 404)

    def test_ozon_page_and_data_are_private(self):
        with patch.object(economics.unit_economics_ozon, "load_products", return_value=[]) as loader:
            for user_id in (2, 1):
                self.user = self.account(user_id)
                response = self.client.get("/sales/unit-economics-1c/ozon")
                self.assertEqual(response.status_code, 200)
                self.assertEqual('"ozonPreview": true' in response.text, user_id == 1)
                self.assertNotIn('id="ue1c-calendar-link"', response.text)
                data = self.client.get("/sales/unit-economics-1c/ozon?data=1")
                self.assertEqual(data.status_code, 200 if user_id == 1 else 404)
                if user_id == 2:
                    loader.assert_not_called()
            loader.assert_called_once()

    def test_calendar_read_and_write_blocked_before_database_access(self):
        self.user = self.account(2)
        payload = dict(
            store="rimili",
            day="2026-09-30",
            article="sku",
            field="purchase_price",
            value=100,
            token="old",
            reason="test",
            preview_token="old",
        )
        with (
            patch.object(calendar, "products") as products,
            patch.object(calendar.repository, "correct") as correct,
        ):
            for suffix in ("", "/yandex-market"):
                base = "/api/unit-economics-1c" + suffix + "/calendar"
                for tail in ("", "/cell"):
                    response = self.client.get(
                        base + tail, params={"store": "rimili", "day": "2026-09-30", "article": "sku"}
                    )
                    self.assertEqual(response.status_code, 404)
                for tail in ("/preview", "/correction"):
                    self.assertEqual(self.client.post(base + tail, json=payload).status_code, 404)
            products.assert_not_called()
            correct.assert_not_called()

    def test_calendar_allowed_account_still_needs_section_rights(self):
        self.user = self.account(
            1,
            role="user",
            can_edit_stock=False,
            can_manage_users=False,
            section_access={"unit_economics_wb": "none"},
        )
        self.assertEqual(self.client.get("/sales/unit-economics-1c?calendar=1").status_code, 403)

    def test_new_job_settings_and_history_are_private(self):
        owner_jobs = {job.name for job in integrations._visible_jobs(self.user)}
        self.assertTrue(preview.PRIVATE_JOB_NAMES <= owner_jobs)
        self.user = self.account(2)
        self.assertFalse(
            preview.PRIVATE_JOB_NAMES & {job.name for job in integrations._visible_jobs(self.user)}
        )
        with (
            patch.object(integrations, "_sync_history") as history,
            patch.object(integrations.sync_settings, "save_setting") as save,
        ):
            for name in preview.PRIVATE_JOB_NAMES:
                base = "/api/admin/integrations/sync-jobs/" + name
                self.assertEqual(self.client.get(base + "/history").status_code, 404)
                self.assertEqual(self.client.post(base + "/run").status_code, 404)
                self.assertEqual(self.client.put(base + "/settings", json={"enabled": True}).status_code, 404)
            history.assert_not_called()
            save.assert_not_called()

    def test_unauthenticated_requests_rejected(self):
        self.user = None
        for path in (
            "/sales/unit-economics-1c/ozon?data=1",
            "/sales/unit-economics-1c?calendar=1",
            "/api/unit-economics-1c/calendar?store=rimili&day=2026-09-30",
        ):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_shared_source_sync_does_not_expose_private_ozon_results(self):
        self.user = self.account(2)
        with (
            patch.object(economics, "run_tracked", side_effect=lambda _name, _trigger, callback: callback()),
            patch.object(
                economics.unit_economics_1c_source,
                "sync_all_marketplaces",
                return_value={"saved": 0, "ok": True},
            ) as sync,
            patch.object(economics.db, "log_action"),
            patch.object(economics, "_cabinet_settings_payload", return_value=[]),
        ):
            response = self.client.post("/api/unit-economics-1c/source-data/sync")
            self.assertEqual(response.status_code, 200)
            sync.assert_called_once_with(include_ozon=False)


if __name__ == "__main__":
    unittest.main()
