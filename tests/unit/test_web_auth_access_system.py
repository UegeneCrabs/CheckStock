import logging
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.dto.identity import Role, SessionToken, User
from app.dto.system import ReadinessStatus
from app.main import create_app
from app.stores import STORES
from app.web import access, templating
from app.web.routers import auth as auth_routes


class WebAuthAccessSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)
        self.client = TestClient(create_app(), raise_server_exceptions=False)
        self.identities = self.client.app.state.container.identity
        self.user = User(
            id=1,
            full_name="User",
            login="user",
            role=Role.SUPERADMIN,
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
            store_slugs=tuple(STORES),
        )

    def tearDown(self) -> None:
        self.client.close()
        logging.disable(logging.NOTSET)

    def test_login_logout_all_paths(self) -> None:
        with mock.patch.object(self.identities, "user_for_token", return_value=None):
            response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(self.identities, "user_for_token", return_value=self.user):
            self.client.cookies.set(auth_routes.auth.SESSION_COOKIE, "token")
            response = self.client.get("/login", follow_redirects=False)
        self.assertEqual(response.status_code, 303)

        with mock.patch.object(self.identities, "authenticate", return_value=None):
            response = self.client.post("/login", data={"login": "bad", "password": "bad"})
        self.assertEqual(response.status_code, 401)
        with (
            mock.patch.object(self.identities, "authenticate", return_value=self.user),
            mock.patch.object(self.identities, "start_session", return_value=SessionToken(value="session")),
            mock.patch.object(self.client.app.state.container.usage, "start_session"),
        ):
            response = self.client.post(
                "/login", data={"login": "good", "password": "password"}, follow_redirects=False
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn(auth_routes.auth.SESSION_COOKIE, response.cookies)
        with (
            mock.patch.object(self.identities, "end_session") as end,
            mock.patch.object(self.client.app.state.container.usage, "end_session"),
        ):
            self.client.cookies.set(auth_routes.auth.SESSION_COOKIE, "session")
            response = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        end.assert_called_once_with(SessionToken(value="session"))
        self.client.cookies.clear()
        with (
            mock.patch.object(self.identities, "end_session") as end,
            mock.patch.object(self.client.app.state.container.usage, "end_session"),
        ):
            self.client.get("/logout", follow_redirects=False)
        end.assert_not_called()

    def test_profile_has_logout_button(self) -> None:
        with mock.patch.object(self.identities, "user_for_token", return_value=self.user):
            self.client.cookies.set(auth_routes.auth.SESSION_COOKIE, "session")
            response = self.client.get("/profile")

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="btn-secondary profile-account-logout" href="/logout"', response.text)
        self.assertIn("Выйти из аккаунта", response.text)

    def test_access_helpers(self) -> None:
        superadmin = self.user
        user = self.user.model_copy(update={"id": 2, "role": Role.USER, "store_slugs": ("rimili", "wrong")})
        self.assertEqual(access.accessible_store_slugs(None), ())
        self.assertEqual(access.accessible_store_slugs(superadmin), tuple(STORES))
        self.assertEqual(access.accessible_store_slugs(user), ("rimili",))
        self.assertEqual(access.accessible_store_items(user).root[0].slug, "rimili")
        self.assertTrue(access.has_store_access(user, "RIMILI"))
        self.assertEqual(access.first_accessible_store(user), "rimili")
        self.assertIsNone(access.first_accessible_store(None))
        request = SimpleNamespace(state=SimpleNamespace(user=user))
        context = access.require_store_access(request, "RIMILI")
        self.assertEqual(context.slug, "rimili")
        self.assertIs(context.store, STORES["rimili"])
        with self.assertRaises(HTTPException) as missing:
            access.require_store_access(request, "wrong")
        self.assertEqual(missing.exception.status_code, 404)
        with self.assertRaises(HTTPException) as denied:
            access.require_store_access(request, "tris")
        self.assertEqual(denied.exception.status_code, 403)

    def test_authentication_middleware_redirect_json_and_store_denial(self) -> None:
        self.client.cookies.set(auth_routes.auth.SESSION_COOKIE, "test-session")
        with mock.patch.object(self.identities, "user_for_token", return_value=None):
            self.assertEqual(self.client.get("/sales", follow_redirects=False).status_code, 303)
            self.assertEqual(
                self.client.get("/sales", headers={"accept": "application/json"}).status_code, 401
            )
            self.assertEqual(self.client.post("/admin/sync-stock").status_code, 401)
        limited = {
            "id": 2,
            "full_name": "Limited",
            "role": "user",
            "is_active": 1,
            "store_slugs": ["rimili"],
        }
        self.client.cookies.set(auth_routes.auth.SESSION_COOKIE, "test-session")
        with mock.patch.object(self.identities, "user_for_token", return_value=limited):
            self.assertEqual(self.client.get("/stock/tris").status_code, 403)
            self.assertEqual(
                self.client.get("/stock/tris", headers={"accept": "application/json"}).status_code,
                403,
            )
            self.assertEqual(self.client.post("/stock/tris/shipment").status_code, 403)
        self.assertEqual(self.client.get("/static/app.css").status_code, 404)

    def test_health_and_readiness(self) -> None:
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/readyz").json(), {"status": "ok", "database": "ok"})
        with mock.patch.object(
            self.client.app.state.container.health,
            "readiness",
            return_value=ReadinessStatus(status="unavailable", database="unavailable"),
        ):
            response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)

    def test_templating_token_banner_and_page(self) -> None:
        user = {"full_name": "A B", "role": "admin", "store_slugs": ["rimili"]}
        with mock.patch.object(templating.token_watch, "get_warnings", return_value=[]):
            self.assertEqual(templating.render_token_banner(user), "")
        warnings = [
            {"store": STORES["rimili"]["name"], "expires_at": "2026-08-01", "expired": True, "days_left": -1},
            {"store": STORES["rimili"]["name"], "expires_at": "2026-08-13", "expired": False, "days_left": 1},
            {"store": "Other", "expires_at": "2026-08-13", "expired": False, "days_left": 0},
        ]
        with mock.patch.object(templating.token_watch, "get_warnings", return_value=warnings):
            banner = templating.render_token_banner(user)
        self.assertIn("token-banner", banner)
        self.assertNotIn("Other", banner)
        with mock.patch.object(templating.token_watch, "get_warnings", return_value=[]):
            page = templating.render_page("CheckStock — Test", "admin", "<main>x</main>", user, "wide")
        self.assertIn("AB", page)
        self.assertIn("wide", page)
        self.assertIn("<main>x</main>", page)


if __name__ == "__main__":
    unittest.main()
