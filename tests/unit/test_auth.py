from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from app.application.identity import IdentityService
from app.dto.identity import (
    ActivityCommand,
    ActivityLogQuery,
    AuditedCreateUser,
    AuditedUserMutation,
    CreateUserCommand,
    Credentials,
    ExpiredSessionsCommand,
    LoginQuery,
    PermissionChange,
    PermissionName,
    Role,
    RoleCheck,
    SessionData,
    SessionToken,
    User,
    UserActiveChange,
    UserCountQuery,
    UserId,
    UserMutationKind,
    UserPasswordChange,
    UserStoreAccessChange,
)
from app.security import Pbkdf2PasswordService


def user(active: bool = True) -> User:
    return User(
        id=1,
        full_name="Test User",
        google_email="test@example.test",
        login="test",
        password_hash="placeholder",
        role=Role.ADMIN,
        is_active=active,
        can_edit_stock=True,
        can_manage_users=True,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
        store_slugs=("rimili",),
    )


def service(repository: mock.Mock, password_service: mock.Mock | None = None) -> IdentityService:
    unit_of_work = mock.MagicMock()
    unit_of_work.__enter__.return_value = unit_of_work
    unit_of_work.identities = repository
    return IdentityService(
        unit_of_work_factory=lambda: unit_of_work,
        password_service=password_service or mock.Mock(),
        session_ttl=timedelta(days=1),
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        token_factory=lambda: "x" * 43,
    )


@pytest.mark.unit
def test_password_hash_round_trip_and_rejects_malformed_values() -> None:
    passwords = Pbkdf2PasswordService(iterations=100_000)
    from app.dto.identity import PasswordHashRequest, PasswordVerification

    password_hash = passwords.hash(PasswordHashRequest(password="secret-password"))
    assert passwords.verify(
        PasswordVerification(password="secret-password", stored_hash=password_hash.root)
    ).root
    assert not passwords.verify(
        PasswordVerification(password="wrong-password", stored_hash=password_hash.root)
    ).root
    assert not passwords.verify(PasswordVerification(password="secret-password", stored_hash="invalid")).root


@pytest.mark.unit
def test_authentication_uses_repository_and_password_port() -> None:
    repository = mock.Mock()
    candidate = user().model_copy(update={"password_hash": "stored"})
    repository.get_user_by_login.return_value = candidate
    passwords = mock.Mock()
    passwords.verify.return_value.root = True
    identities = service(repository, passwords)

    authenticated = identities.authenticate(Credentials(login="test", password="password"))

    assert authenticated == candidate
    repository.get_user_by_login.assert_called_once()
    passwords.verify.assert_called_once()

    repository.get_user_by_login.return_value = candidate.model_copy(update={"is_active": False})
    assert identities.authenticate(Credentials(login="test", password="password")) is None
    repository.get_user_by_login.return_value = None
    assert identities.authenticate(Credentials(login="test", password="password")) is None
    repository.get_user_by_login.return_value = candidate
    passwords.verify.return_value.root = False
    assert identities.authenticate(Credentials(login="test", password="password")) is None


@pytest.mark.unit
def test_session_lifecycle_is_atomic_and_expires_invalid_session() -> None:
    repository = mock.Mock()
    identities = service(repository)

    token = identities.start_session(UserId(1))

    assert token == SessionToken(value="x" * 43)
    repository.create_session.assert_called_once()
    active_session = SessionData(
        token=token.value,
        user_id=1,
        created_at=datetime(2026, 8, 11, tzinfo=UTC),
        expires_at=datetime(2026, 8, 13, tzinfo=UTC),
    )
    repository.get_session.return_value = active_session
    repository.get_user.return_value = user()
    assert identities.user_for_token(token) == user()

    repository.get_session.return_value = active_session.model_copy(
        update={"expires_at": datetime(2026, 8, 11, tzinfo=UTC)}
    )
    assert identities.user_for_token(token) is None
    repository.delete_session.assert_called_with(token)

    repository.get_session.return_value = None
    assert identities.user_for_token(token) is None
    repository.get_session.return_value = active_session
    repository.get_user.return_value = user(active=False)
    assert identities.user_for_token(token) is None
    identities.end_session(token)
    repository.delete_session.assert_called_with(token)


@pytest.mark.unit
def test_identity_policy_decisions() -> None:
    identities = service(mock.Mock())
    candidate = user()

    assert identities.has_role(RoleCheck(user=candidate, minimum=Role.USER)).root
    assert not identities.has_role(RoleCheck(user=candidate, minimum=Role.SUPERADMIN)).root
    assert not identities.has_role(RoleCheck(user=None, minimum=Role.USER)).root
    assert identities.can_edit_stock(candidate).root
    assert identities.can_manage_users(candidate).root
    assert not identities.can_edit_stock(None).root
    assert not identities.can_manage_users(candidate.model_copy(update={"role": Role.USER})).root


def activity() -> ActivityCommand:
    return ActivityCommand(
        user_id=1,
        user_name="Admin",
        action="Changed",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command", "expected_method"),
    [
        (
            AuditedUserMutation(kind=UserMutationKind.DELETE, user_id=2, activity=activity()),
            "delete_user",
        ),
        (
            AuditedUserMutation(
                kind=UserMutationKind.ACTIVE,
                user_id=2,
                is_active=False,
                activity=activity(),
            ),
            "set_active",
        ),
        (
            AuditedUserMutation(
                kind=UserMutationKind.PERMISSION,
                user_id=2,
                permission=PermissionName.EDIT_STOCK,
                allowed=True,
                activity=activity(),
            ),
            "set_permission",
        ),
        (
            AuditedUserMutation(
                kind=UserMutationKind.STORES,
                user_id=2,
                store_slugs=("rimili",),
                activity=activity(),
            ),
            "set_store_access",
        ),
        (
            AuditedUserMutation(
                kind=UserMutationKind.PASSWORD,
                user_id=2,
                password_hash="hash",
                activity=activity(),
            ),
            "update_password",
        ),
    ],
)
def test_audited_mutations_use_one_unit_of_work(command, expected_method) -> None:
    repository = mock.Mock()
    identities = service(repository)

    identities.mutate_user(command)

    getattr(repository, expected_method).assert_called_once()
    repository.add_activity.assert_called_once_with(command.activity)
    if command.kind is UserMutationKind.ACTIVE:
        repository.delete_sessions_for_user.assert_called_once_with(UserId(command.user_id))


@pytest.mark.unit
@pytest.mark.parametrize(
    "command",
    [
        AuditedUserMutation(kind=UserMutationKind.ACTIVE, user_id=2, activity=activity()),
        AuditedUserMutation(kind=UserMutationKind.PERMISSION, user_id=2, activity=activity()),
        AuditedUserMutation(kind=UserMutationKind.PASSWORD, user_id=2, activity=activity()),
    ],
)
def test_audited_mutations_require_kind_specific_values(command) -> None:
    with pytest.raises(ValueError):
        service(mock.Mock()).mutate_user(command)


@pytest.mark.unit
def test_identity_crud_methods_delegate_to_repository() -> None:
    repository = mock.Mock()
    identities = service(repository)
    created = CreateUserCommand(
        full_name="Created",
        login="created",
        password_hash="hash",
        role=Role.USER,
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    repository.create_user.return_value = UserId(2)

    assert identities.create_user(created) == UserId(2)
    assert identities.create_user_with_activity(
        AuditedCreateUser(user=created, activity=activity())
    ) == UserId(2)
    identities.get_user(UserId(2))
    identities.get_user_by_login(LoginQuery(login="created"))
    identities.list_users()
    identities.count_users()
    identities.count_superadmins(UserCountQuery(exclude_user_id=2))
    identities.set_active(UserActiveChange(user_id=2, is_active=True))
    identities.set_permission(PermissionChange(user_id=2, permission=PermissionName.EDIT_STOCK, allowed=True))
    identities.set_store_access(UserStoreAccessChange(user_id=2, store_slugs=("rimili",)))
    identities.update_password(UserPasswordChange(user_id=2, password_hash="new-hash"))
    identities.delete_user(UserId(2))
    identities.delete_sessions_for_user(UserId(2))
    identities.delete_expired_sessions(ExpiredSessionsCommand(now=datetime(2026, 8, 12, tzinfo=UTC)))
    identities.log_activity(activity())
    identities.get_activity(ActivityLogQuery(limit=10))

    repository.get_user.assert_called_once()
    repository.get_user_by_login.assert_called_once()
    repository.list_users.assert_called_once()
    repository.count_users.assert_called_once()
    repository.count_superadmins.assert_called_once()
    repository.set_active.assert_called_once()
    repository.set_permission.assert_called_once()
    repository.set_store_access.assert_called_once()
    repository.update_password.assert_called_once()
    repository.delete_user.assert_called_once()
    repository.delete_sessions_for_user.assert_called_once()
    repository.delete_expired_sessions.assert_called_once()
    assert repository.add_activity.call_count == 2
    repository.get_activity.assert_called_once()
