from datetime import UTC, datetime
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
from app.infrastructure.orm import OrmBase
from app.main import create_app
from app.section_access import access_level, has_access, landing_path, section_for_path
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
    assert access_level(user, SectionName.SALES) is SectionAccessLevel.NONE
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
    assert landing_path(restricted) == "/stock"
    assert section_for_path("/") is None
    assert section_for_path("/api/rnp/action") is None
    assert section_for_path("/sales/unit-economics-1c") is SectionName.UNIT_ECONOMICS_WB
    assert section_for_path("/stock-2/details/marketplace") is None

    independent_unit_access = user.model_copy(
        update={
            "section_access": {
                SectionName.UNIT_ECONOMICS_1C: SectionAccessLevel.READ,
            }
        }
    )
    assert has_access(independent_unit_access, SectionName.UNIT_ECONOMICS_1C)

    superadmin = restricted.model_copy(update={"role": Role.SUPERADMIN})
    assert access_level(superadmin, SectionName.SALES) is SectionAccessLevel.NONE


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


def test_middleware_enforces_unit_economics_1c_access(database_path) -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    identities = client.app.state.container.identity
    user = _user(
        section_access={
            SectionName.UNIT_ECONOMICS_1C: SectionAccessLevel.NONE,
        }
    )
    client.cookies.set(auth_routes.auth.SESSION_COOKIE, "test-session")
    with mock.patch.object(identities, "user_for_token", return_value=user):
        response = client.get("/sales/unit-economics-1c")
        assert response.status_code == 403
        assert "Юнит-экономика 1С" in response.text
    client.close()


def test_middleware_blocks_cabinet_settings_save_for_read_only_access() -> None:
    client = TestClient(create_app(), raise_server_exceptions=False)
    identities = client.app.state.container.identity
    user = _user(
        section_access={
            SectionName.UNIT_ECONOMICS_1C: SectionAccessLevel.READ,
        }
    )
    payload = {
        "acceptance_coefficient": 0,
        "wb_extra_tariff_percent": 0,
        "acquiring_percent": 3.8,
        "team_commission_percent": 0,
        "vat_percent": 9,
        "usn_percent": 6,
        "osno_percent": 0,
        "tax_system": "usn",
    }
    client.cookies.set(auth_routes.auth.SESSION_COOKIE, "test-session")
    with mock.patch.object(identities, "user_for_token", return_value=user):
        response = client.put(
            "/api/unit-economics-1c/cabinet-settings/rimili",
            json=payload,
            headers={"X-Requested-With": "fetch"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "Раздел доступен только для просмотра"
        source_sync = client.post(
            "/api/unit-economics-1c/source-data/sync",
            headers={"X-Requested-With": "fetch"},
        )
        assert source_sync.status_code == 403
        assert source_sync.json()["error"] == "Раздел доступен только для просмотра"
    client.close()
