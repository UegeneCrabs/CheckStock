"""Authentication and data-preservation regressions for retiring account ACLs."""

import asyncio
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch

import dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

with (
    patch.object(dotenv, "dotenv_values", return_value={}),
    patch.dict(os.environ, {"CHECKSTOCK_DATABASE_URL": ""}),
):
    from app.access import auth, economics_preview
    from app.access.resources import accessible_marketplaces, accessible_stores, has_store_marketplace
    from app.access.security import Pbkdf2PasswordService
    from app.analytics import analyzer
    from app.application.identity import IdentityService
    from app.config import Settings, settings
    from app.core.domain import MARKETPLACES
    from app.core.stores import STORES
    from app.dto.identity import (
        ActivityCommand,
        ActivityLogQuery,
        AuditedUserMutation,
        CreateUserCommand,
        Credentials,
        PasswordHashRequest,
        UserActiveChange,
        UserCountQuery,
        UserId,
        UserMutationKind,
        UserPasswordChange,
    )
    from app.economics.yandex import reports as yandex_reports
    from app.infrastructure.database import Database
    from app.infrastructure.identity_repository import SqlAlchemyIdentityUnitOfWork
    from app.infrastructure.orm import OrmBase
    from app.repositories import core, schema
    from app.repositories.identity_migration import (
        RETIRED_TABLES,
        RETIRED_USER_COLUMNS,
        retire_access_control,
    )
    from app.web.middleware import authentication_middleware
    from app.web.routers import ROUTERS, agent_analytics, agent_management, agent_mcp


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.passwords = Pbkdf2PasswordService(100_000)
        cls.password_hash = cls.passwords.hash(PasswordHashRequest(password="test-password")).root

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="identity-retirement-")
        self.addCleanup(self.directory.cleanup)
        self.database = Database(path=Path(self.directory.name) / "test.sqlite")
        self.addCleanup(self.database.dispose)
        OrmBase.metadata.create_all(self.database.engine)
        self.now = datetime.now(UTC)
        self.service = IdentityService(
            lambda: SqlAlchemyIdentityUnitOfWork(self.database.session_factory),
            self.passwords,
            timedelta(days=2),
            clock=lambda: self.now,
        )
        self.users = [self.create_user("first"), self.create_user("second")]
        self.patch(core, "DB_PATH", self.database.path)
        self.patch(core, "database_for_path", lambda *a, **kw: self.database)
        self.patch(schema, "database_for_path", lambda *a, **kw: self.database)
        self.patch(auth, "database_for_path", lambda *a, **kw: self.database)
        from app.web import templating

        self.patch(templating, "render_system_alerts", lambda *a, **kw: "")
        self.application = FastAPI()
        self.application.state.container = SimpleNamespace(identity=self.service, passwords=self.passwords)
        self.application.middleware("http")(authentication_middleware)
        for router in ROUTERS:
            self.application.include_router(router)
        self.client = TestClient(self.application, follow_redirects=False)
        self.addCleanup(self.client.close)

    def patch(self, target, name, value):
        patcher = patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def create_user(self, login):
        user_id = self.service.create_user(
            CreateUserCommand(
                full_name=login,
                login=login,
                google_email=f"{login}@example.test",
                password_hash=self.password_hash,
                created_at=self.now,
            )
        )
        return self.service.get_user(user_id)

    def login(self, index=0):
        token = self.service.start_session(UserId(self.users[index].id))
        self.client.cookies.set(auth.SESSION_COOKIE, token.value)
        return token

    def mutation(self, kind, user_id, **values):
        return AuditedUserMutation(
            kind=kind,
            user_id=user_id,
            activity=ActivityCommand(
                user_id=self.users[0].id, user_name="first", action="test", created_at=self.now
            ),
            **values,
        )

    def test_fresh_schema_and_repeated_startup_never_recreate_acl(self):
        schema.init_db()
        schema.init_db()
        tables = set(inspect(self.database.engine).get_table_names())
        self.assertFalse(tables.intersection(RETIRED_TABLES))
        self.assertEqual(
            {c["name"] for c in inspect(self.database.engine).get_columns("users")},
            {"id", "full_name", "google_email", "login", "password_hash", "is_active", "created_at"},
        )
        self.assertTrue({"stock_items", "ff_stock", "stock_operations", "sessions", "activity_log"} <= tables)
        self.assertEqual(self.service.count_users().root, 2)

    def test_login_multidevice_logout_expiry_and_blocking(self):
        self.assertIsNone(self.service.authenticate(Credentials(login="first", password="wrong")))
        self.assertEqual(
            self.service.authenticate(Credentials(login="first", password="test-password")).id,
            self.users[0].id,
        )
        first = self.service.start_session(UserId(self.users[0].id))
        second = self.service.start_session(UserId(self.users[0].id))
        self.assertNotEqual(first, second)
        self.service.end_session(first)
        self.assertIsNone(self.service.user_for_token(first))
        self.assertIsNotNone(self.service.user_for_token(second))
        self.now += timedelta(days=3)
        self.assertIsNone(self.service.user_for_token(second))
        third = self.service.start_session(UserId(self.users[0].id))
        self.service.set_active(UserActiveChange(user_id=self.users[0].id, is_active=False))
        self.assertIsNone(self.service.user_for_token(third))
        self.assertIsNone(self.service.authenticate(Credentials(login="first", password="test-password")))

    def test_reset_password_revokes_all_sessions(self):
        tokens = [self.service.start_session(UserId(self.users[1].id)) for _ in range(2)]
        password_hash = self.passwords.hash(PasswordHashRequest(password="new-password")).root
        self.service.update_password(
            UserPasswordChange(user_id=self.users[1].id, password_hash=password_hash)
        )
        self.assertTrue(all(self.service.user_for_token(token) is None for token in tokens))
        self.assertIsNone(self.service.authenticate(Credentials(login="second", password="test-password")))
        self.assertIsNotNone(self.service.authenticate(Credentials(login="second", password="new-password")))

    def test_every_active_user_gets_all_resources_without_manager_filter(self):
        for user in self.users:
            self.assertEqual(accessible_stores(user), tuple(STORES))
            self.assertEqual(accessible_marketplaces(user), tuple(MARKETPLACES))
            self.assertTrue(analyzer.permitted("unrelated employee", user))
            self.assertTrue(yandex_reports.permitted("unrelated employee", user))
            self.assertFalse(has_store_marketplace(user, "unknown-store", "WB"))
        inactive = self.users[0].model_copy(update={"is_active": False})
        self.assertFalse(accessible_stores(inactive))
        self.assertFalse(analyzer.permitted("first", inactive))

    def test_self_and_last_active_protection(self):
        for kind, values in [(UserMutationKind.DELETE, {}), (UserMutationKind.ACTIVE, {"is_active": False})]:
            with self.assertRaises(ValueError):
                self.service.mutate_user(self.mutation(kind, self.users[0].id, **values))
        self.service.delete_user(UserId(self.users[1].id))
        with self.assertRaises(ValueError):
            self.service.set_active(UserActiveChange(user_id=self.users[0].id, is_active=False))
        with self.assertRaises(ValueError):
            self.service.delete_user(UserId(self.users[0].id))

    def test_concurrent_disable_and_delete_cannot_remove_last_active_user(self):
        barrier = Barrier(2)

        def mutate(user):
            barrier.wait()
            try:
                if user.id == self.users[0].id:
                    self.service.delete_user(UserId(user.id))
                else:
                    self.service.set_active(UserActiveChange(user_id=user.id, is_active=False))
                return "ok"
            except ValueError:
                return "protected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(mutate, self.users))
        self.assertCountEqual(result, ["ok", "protected"])
        self.assertEqual(self.service.count_active_users(UserCountQuery()).root, 1)

    def test_browser_auth_and_admin_crud_without_roles(self):
        self.assertEqual(self.client.get("/admin").status_code, 303)
        self.assertEqual(self.client.post("/admin/users").status_code, 401)
        response = self.client.post("/login", data={"login": "first", "password": "test-password"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/stock")
        self.login()
        for path in (
            "/admin",
            "/profile",
            "/stock",
            "/stock/rimili",
            "/stock/total",
            "/stock/supplies",
            "/stock/inbound",
            "/stock/cost-report",
            "/stock/rimili/operations",
            "/analytics/analyzer",
            "/analytics/sales-api",
            "/analytics/ephemerides",
            "/ai-agents/keys",
            "/sales/unit-economics-1c",
            "/sales/unit-economics-1c/yandex-market",
            "/admin/google-export",
            "/admin/integrations",
            "/finance-reports/yandex",
            "/supply-schedule",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                expected = 303 if path == "/admin/google-export" else 200
                self.assertEqual(response.status_code, expected, response.text[:200])
                self.assertNotIn("$user_rows", response.text)
        response = self.client.post(
            "/admin/users",
            data={
                "login": "third",
                "full_name": "Third",
                "google_email": "third@example.test",
                "password": "new-password",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.service.count_users().root, 3)
        self.assertEqual(self.client.post(f"/admin/users/{self.users[0].id}/delete").status_code, 400)
        self.assertEqual(self.client.post(f"/admin/users/{self.users[1].id}/toggle-active").status_code, 200)
        self.assertFalse(self.service.get_user(UserId(self.users[1].id)).is_active)
        self.assertGreaterEqual(len(self.service.get_activity(ActivityLogQuery()).root), 2)
        self.assertEqual(self.client.get("/logout").status_code, 303)
        self.assertEqual(self.client.get("/profile").status_code, 303)

    def test_retired_endpoints_are_not_registered(self):
        self.login()
        for path in ("role", "stores", "sections", "access-policy", "toggle-stock-edit"):
            self.assertEqual(self.client.post(f"/admin/users/1/{path}").status_code, 404)
        for path in ("/admin/access-requests/1/decision", "/admin/access-grants/1/revoke"):
            self.assertEqual(self.client.post(path).status_code, 404)
        self.assertEqual(
            self.client.put("/api/unit-economics-1c/preferences/columns", json={}).status_code, 404
        )

    def test_agent_keys_remain_owned_and_readonly_and_revoke_on_account_block(self):
        key_settings = settings.model_copy(
            update={"agent_tokens_path": Path(self.directory.name) / "keys.json"}
        )
        with (
            patch.object(agent_management, "settings", key_settings),
            patch.object(agent_analytics, "settings", key_settings),
        ):
            self.login()
            response = self.client.post(
                "/api/ai-agents/keys", json={"name": "test"}, headers={"X-Agent-Management": "1"}
            )
            self.assertEqual(response.status_code, 201, response.text)
            key = response.json()
            self.login(1)
            self.assertEqual(self.client.get("/api/ai-agents/keys").json()["keys"], [])
            self.assertEqual(
                self.client.delete(
                    f"/api/ai-agents/keys/{key['id']}", headers={"X-Agent-Management": "1"}
                ).status_code,
                404,
            )
            headers = {"Authorization": f"Bearer {key['token']}"}
            self.assertEqual(self.client.get("/api/agent/v1/stores", headers=headers).status_code, 200)
            self.assertEqual(self.client.post("/api/agent/v1/stores", headers=headers).status_code, 405)
            self.service.set_active(UserActiveChange(user_id=self.users[0].id, is_active=False))
            self.assertEqual(self.client.get("/api/agent/v1/stores", headers=headers).status_code, 401)

    def test_preview_and_mcp_check_active_account(self):
        with patch.object(
            economics_preview, "settings", settings.model_copy(update={"economics_preview_enabled": True})
        ):
            self.assertTrue(all(economics_preview.can_preview(user) for user in self.users))
            self.assertFalse(
                economics_preview.can_preview(self.users[0].model_copy(update={"is_active": False}))
            )
        with patch.object(
            economics_preview, "settings", settings.model_copy(update={"economics_preview_enabled": False})
        ):
            self.assertFalse(economics_preview.can_preview(self.users[0]))
        request = SimpleNamespace(app=self.application)
        self.assertIsNotNone(asyncio.run(agent_mcp.active_user(request, self.users[0].id)))
        self.service.set_active(UserActiveChange(user_id=self.users[0].id, is_active=False))
        self.assertIsNone(asyncio.run(agent_mcp.active_user(request, self.users[0].id)))

    def test_preview_configuration_prefers_global_flag_and_accepts_legacy_enabled(self):
        with patch.dict(os.environ, {"CHECKSTOCK_ECONOMICS_PREVIEW_USER_ID": "7"}, clear=True):
            self.assertTrue(Settings.from_env().economics_preview_enabled)
            with patch.dict(os.environ, {"CHECKSTOCK_ECONOMICS_PREVIEW_ENABLED": "0"}):
                self.assertFalse(Settings.from_env().economics_preview_enabled)
            with patch.dict(
                os.environ,
                {
                    "CHECKSTOCK_ECONOMICS_PREVIEW_ENABLED": "1",
                    "CHECKSTOCK_ECONOMICS_PREVIEW_USER_ID": "unused",
                },
            ):
                self.assertTrue(Settings.from_env().economics_preview_enabled)

    def test_seed_creates_normal_active_account_once(self):
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM users")
        seed = Path(self.directory.name) / "initial-user.json"
        seed.write_text('{"login":"initial","password":"initial-password"}', encoding="utf-8")
        with patch.object(auth, "SEED_PATH", seed):
            auth.seed_initial_user()
            auth.seed_initial_user()
        users = self.service.list_users().root
        self.assertEqual(len(users), 1)
        self.assertTrue(users[0].is_active)
        self.assertNotIn("role", users[0].model_dump())

    def test_explicit_manager_filter_still_filters_reports(self):
        from app import db
        from app.agents import reports

        rows = [{"article": "1", "manager": "Alice"}, {"article": "2", "manager": "Bob"}]
        with patch.object(db, "get_unit_economics_1c_product_reference_rows", return_value=rows):
            self.assertEqual(reports.references(self.users[0], "rimili"), rows)
            self.assertEqual(reports.references(self.users[0], "rimili", "Bob"), rows[1:])

    def test_existing_database_migration_preserves_rows_and_is_repeatable(self):
        token = self.service.start_session(UserId(self.users[0].id))
        self.service.log_activity(
            ActivityCommand(
                user_id=self.users[0].id, user_name="first", action="preserve", created_at=self.now
            )
        )
        with self.database.engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE business_probe (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), value TEXT)"
            )
            conn.exec_driver_sql("INSERT INTO business_probe VALUES (42,1,'preserve')")
            for column in RETIRED_USER_COLUMNS:
                conn.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {column} TEXT DEFAULT 'legacy'")
            for table in reversed(RETIRED_TABLES):
                extra = (
                    ", request_id INTEGER REFERENCES access_requests(id)"
                    if table == "temporary_access_grants"
                    else ""
                )
                conn.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY{extra})")
                conn.exec_driver_sql(f"INSERT INTO {table} (id) VALUES (1)")
            before = {
                table: conn.exec_driver_sql(f'SELECT * FROM "{table}"').all()
                for table in ("sessions", "activity_log", "business_probe")
            }
        retire_access_control(self.database)
        retire_access_control(self.database)
        schema.init_db()
        for table, rows in before.items():
            with self.database.engine.connect() as conn:
                self.assertEqual(conn.exec_driver_sql(f'SELECT * FROM "{table}"').all(), rows)
        self.assertEqual(self.service.user_for_token(token).id, self.users[0].id)
        self.assertEqual(self.service.get_user(UserId(1)).password_hash, self.password_hash)
        self.assertFalse(set(inspect(self.database.engine).get_table_names()).intersection(RETIRED_TABLES))

    def test_failed_migration_rolls_back_all_drops(self):
        with self.database.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE user_section_access (id INTEGER PRIMARY KEY)")
            conn.exec_driver_sql("INSERT INTO user_section_access VALUES (9)")
            conn.exec_driver_sql("ALTER TABLE users ADD COLUMN role TEXT")
            conn.exec_driver_sql("CREATE INDEX unexpected_role_index ON users(role)")
        with self.assertRaises(OperationalError):
            retire_access_control(self.database)
        with self.database.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT id FROM user_section_access").scalar(), 9)
            self.assertIn("role", {column["name"] for column in inspect(conn).get_columns("users")})

    def test_unexpected_inbound_dependency_aborts_before_deletion(self):
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE access_requests (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE unexpected_business (request_id INTEGER REFERENCES access_requests(id))"
            )
        with self.assertRaisesRegex(RuntimeError, "Unexpected dependency"):
            retire_access_control(self.database)
        self.assertIn("access_requests", inspect(self.database.engine).get_table_names())


if __name__ == "__main__":
    unittest.main()
