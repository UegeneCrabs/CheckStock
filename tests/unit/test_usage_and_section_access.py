from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.dto.identity import (
    CreateUserCommand,
    Role,
    SectionAccessLevel,
    SectionName,
    User,
    UserId,
    UserRoleChange,
    UserSectionAccessChange,
)
from app.infrastructure.database import database_for_path, dispose_databases
from app.infrastructure.identity_repository import SqlAlchemyIdentityUnitOfWork
from app.infrastructure.orm import OrmBase, UserRecord
from app.main import create_app
from app.section_access import access_level, has_access, landing_path, section_for_path
from app.usage_analytics import UsageAnalyticsService
from app.web.routers import auth as auth_routes


def _user(**updates) -> User:
    values = {
        "id": 1,
        "full_name": "Менеджер",
        "login": "manager",
        "role": Role.USER,
        "created_at": datetime(2026, 8, 13, tzinfo=UTC),
    }
    values.update(updates)
    return User(**values)


def test_section_access_defaults_and_explicit_rules() -> None:
    user = _user(can_edit_stock=False)
    assert access_level(user, SectionName.SALES) is SectionAccessLevel.WRITE
    assert access_level(user, SectionName.STOCK) is SectionAccessLevel.READ
    assert has_access(user, SectionName.STOCK, SectionAccessLevel.READ)
    assert not has_access(user, SectionName.STOCK, SectionAccessLevel.WRITE)

    restricted = user.model_copy(
        update={
            "section_access": {
                SectionName.SALES: SectionAccessLevel.NONE,
                SectionName.SUPPLY: SectionAccessLevel.READ,
            }
        }
    )
    assert not has_access(restricted, SectionName.SALES)
    assert landing_path(restricted) == "/sales/decision-center"
    assert section_for_path("/") is None
    assert section_for_path("/api/rnp/action") is SectionName.RNP
    assert section_for_path("/stock-2/details/marketplace") is SectionName.STOCK_OVERVIEW

    superadmin = restricted.model_copy(update={"role": Role.SUPERADMIN})
    assert access_level(superadmin, SectionName.SALES) is SectionAccessLevel.WRITE


def test_identity_repository_persists_role_and_section_access(tmp_path: Path) -> None:
    dispose_databases()
    database = database_for_path(tmp_path / "identity.sqlite3")
    OrmBase.metadata.create_all(database.engine)

    def factory() -> SqlAlchemyIdentityUnitOfWork:
        return SqlAlchemyIdentityUnitOfWork(database.session_factory)

    with factory() as unit_of_work:
        user_id = unit_of_work.identities.create_user(
            CreateUserCommand(
                full_name="Менеджер",
                login="manager",
                password_hash="hash",
                role=Role.USER,
                created_at=datetime(2026, 8, 13, tzinfo=UTC),
            )
        )
        unit_of_work.commit()
    with factory() as unit_of_work:
        unit_of_work.identities.set_role(UserRoleChange(user_id=user_id.root, role=Role.ADMIN))
        unit_of_work.identities.set_section_access(
            UserSectionAccessChange(
                user_id=user_id.root,
                section_access={
                    SectionName.SALES: SectionAccessLevel.READ,
                    SectionName.STOCK: SectionAccessLevel.NONE,
                },
            )
        )
        unit_of_work.commit()
    with factory() as unit_of_work:
        saved = unit_of_work.identities.get_user(UserId(user_id.root))
    assert saved is not None
    assert saved.role is Role.ADMIN
    assert saved.section_access[SectionName.SALES] is SectionAccessLevel.READ
    assert saved.section_access[SectionName.STOCK] is SectionAccessLevel.NONE
    assert not saved.can_edit_stock
    dispose_databases()


def test_usage_sessions_online_idle_resume_and_logout(tmp_path: Path) -> None:
    dispose_databases()
    database = database_for_path(tmp_path / "usage.sqlite3")
    OrmBase.metadata.create_all(database.engine)
    with database.session_factory() as session:
        session.add(
            UserRecord(
                id=1,
                full_name="Менеджер",
                google_email="manager@example.test",
                login="manager",
                password_hash="hash",
                role=Role.USER.value,
                created_at="2026-08-13T10:00:00+00:00",
            )
        )
        session.commit()

    current = [datetime(2026, 8, 13, 10, 0, tzinfo=UTC)]
    usage = UsageAnalyticsService(database.session_factory, clock=lambda: current[0])
    usage.start_session("token", 1)
    usage.heartbeat("token", 1, SectionName.SALES, "/sales", active=True, page_view=True)
    current[0] += timedelta(seconds=60)
    usage.heartbeat("token", 1, SectionName.SALES, "/sales", active=True, page_view=False)

    dashboard = usage.dashboard(30)
    assert dashboard["online_count"] == 1
    assert dashboard["period_page_views"] == 1
    assert dashboard["people"][0]["active_period"] == 60

    current[0] += timedelta(minutes=10)
    usage.heartbeat("token", 1, SectionName.SALES, "/sales", active=False, page_view=False)
    assert usage.dashboard(30)["online_count"] == 0

    current[0] += timedelta(minutes=1)
    usage.heartbeat("token", 1, SectionName.RNP, "/sales/rnp", active=True, page_view=True)
    assert usage.dashboard(30)["online_count"] == 1
    usage.end_session("token")
    assert usage.dashboard(30)["online_count"] == 0
    dispose_databases()


def test_middleware_enforces_hidden_read_and_write_access() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    identities = client.app.state.container.identity
    user = _user(
        section_access={
            SectionName.SALES: SectionAccessLevel.NONE,
            SectionName.DECISION_CENTER: SectionAccessLevel.READ,
            SectionName.SUPPLY: SectionAccessLevel.NONE,
        }
    )
    client.cookies.set(auth_routes.auth.SESSION_COOKIE, "test-session")
    with mock.patch.object(identities, "user_for_token", return_value=user):
        sales_response = client.get("/sales")
        assert sales_response.status_code == 403
        assert sales_response.text.startswith("<!DOCTYPE html>")
        assert 'class="app-shell"' in sales_response.text
        assert 'class="empty-workspace access-denied-workspace"' in sales_response.text
        assert 'href="/sales" hidden' in sales_response.text
        supply_response = client.get("/supply")
        assert supply_response.status_code == 403
        assert 'href="/supply" hidden' in supply_response.text
        assert client.get("/sales/decision-center").status_code == 200
        response = client.post(
            "/api/decision-center/status",
            json={"fingerprint": "rimili:test", "status": "completed"},
            headers={"X-Requested-With": "fetch"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "Раздел доступен только для просмотра"
    client.close()
