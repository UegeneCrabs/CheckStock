from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db
from app.container import ApplicationContainer
from app.dto.identity import Role, User
from app.infrastructure.database import dispose_databases
from app.main import create_app
from app.repositories import core
from app.stores import STORES


@pytest.fixture
def database_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "checkstock-test.sqlite3"
    dispose_databases()
    monkeypatch.setattr(core, "DB_PATH", path)
    db.init_db()
    db.seed_defaults()
    yield path
    dispose_databases()


@pytest.fixture
def container(database_path: Path) -> ApplicationContainer:
    return ApplicationContainer(database_path=lambda: database_path)


@pytest.fixture
def application(container: ApplicationContainer) -> FastAPI:
    return create_app(container)


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def user_factory() -> Callable[..., User]:
    def factory(
        *,
        user_id: int = 1,
        role: Role = Role.SUPERADMIN,
        stores: tuple[str, ...] = tuple(STORES),
        can_edit_stock: bool = True,
        can_manage_users: bool = True,
        active: bool = True,
    ) -> User:
        return User(
            id=user_id,
            full_name=f"User {user_id}",
            google_email=f"user{user_id}@example.test",
            login=f"user{user_id}",
            password_hash="test-password-hash",
            role=role,
            is_active=active,
            can_edit_stock=can_edit_stock,
            can_manage_users=can_manage_users,
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
            store_slugs=stores,
        )

    return factory
