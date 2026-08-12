from datetime import UTC, datetime
from unittest import mock

import pytest

from app import auth
from app.application.identity import IdentityService
from app.dto.identity import CountResult, Role, SessionToken, User, UserId


def actor() -> User:
    return User(
        id=1,
        full_name="Admin",
        login="admin",
        role=Role.ADMIN,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


@pytest.mark.unit
def test_compatibility_password_and_service_factory() -> None:
    assert isinstance(auth._now(), datetime)
    assert isinstance(auth._service(), IdentityService)
    password_hash = auth.hash_password("password")
    assert auth.verify_password("password", password_hash)
    assert not auth.verify_password("wrong", password_hash)
    assert not auth.verify_password("password", "")


@pytest.mark.unit
def test_compatibility_identity_wrappers_validate_and_delegate() -> None:
    identities = mock.Mock()
    identities.authenticate.return_value = actor()
    identities.start_session.return_value = SessionToken(value="session")
    identities.user_for_token.return_value = actor()
    with mock.patch.object(auth, "_service", return_value=identities):
        assert auth.authenticate("admin", "password") == actor()
        assert auth.authenticate("", "") is None
        assert auth.start_session(1) == "session"
        auth.end_session("")
        auth.end_session("session")
        assert auth.user_for_token("") is None
        assert auth.user_for_token("session") == actor()

    identities.end_session.assert_called_once_with(SessionToken(value="session"))
    identities.user_for_token.assert_called_once_with(SessionToken(value="session"))
    assert auth.has_role(actor(), "user")
    assert not auth.has_role(actor(), "superadmin")
    assert not auth.has_role(actor(), "unknown")
    assert auth.can_edit_stock(actor())
    assert auth.can_manage_users(actor())


@pytest.mark.unit
def test_superadmin_seed_handles_existing_missing_invalid_and_valid_files(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = mock.Mock()
    identities.count_users.return_value = CountResult(1)
    with mock.patch.object(auth, "_service", return_value=identities):
        auth.seed_superadmin()
    identities.create_user.assert_not_called()

    identities.count_users.return_value = CountResult(0)
    monkeypatch.setattr(auth, "SEED_PATH", tmp_path / "missing.json")
    with mock.patch.object(auth, "_service", return_value=identities):
        auth.seed_superadmin()
    identities.create_user.assert_not_called()

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(auth, "SEED_PATH", invalid)
    with mock.patch.object(auth, "_service", return_value=identities):
        auth.seed_superadmin()
    identities.create_user.assert_not_called()

    valid = tmp_path / "valid.json"
    valid.write_text('{"login":"root","password":"password"}', encoding="utf-8")
    monkeypatch.setattr(auth, "SEED_PATH", valid)
    identities.create_user.return_value = UserId(3)
    with mock.patch.object(auth, "_service", return_value=identities):
        auth.seed_superadmin()
    command = identities.create_user.call_args.args[0]
    assert command.login == "root"
    assert command.role is Role.SUPERADMIN
