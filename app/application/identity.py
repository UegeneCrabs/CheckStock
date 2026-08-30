import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.application.ports import IdentityUnitOfWorkFactory, PasswordService
from app.dto.identity import (
    AccessDecision,
    ActivityCommand,
    ActivityLog,
    ActivityLogQuery,
    AuditedCreateUser,
    AuditedUserMutation,
    CountResult,
    CreateSessionCommand,
    CreateUserCommand,
    Credentials,
    ExpiredSessionsCommand,
    LoginQuery,
    PasswordVerification,
    PermissionChange,
    Role,
    RoleCheck,
    SessionToken,
    User,
    UserAccessPolicyChange,
    UserActiveChange,
    UserCollection,
    UserCountQuery,
    UserId,
    UserMutationKind,
    UserPasswordChange,
    UserRoleChange,
    UserSectionAccessChange,
    UserStoreAccessChange,
)

ROLE_LEVEL = {Role.USER: 1, Role.ADMIN: 2, Role.SUPERADMIN: 3}


class IdentityService:
    def __init__(
        self,
        unit_of_work_factory: IdentityUnitOfWorkFactory,
        password_service: PasswordService,
        session_ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._password_service = password_service
        self._session_ttl = session_ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))

    def authenticate(self, credentials: Credentials) -> User | None:
        with self._unit_of_work_factory() as unit_of_work:
            user = unit_of_work.identities.get_user_by_login(LoginQuery(login=credentials.login))
        if user is None or not user.is_active:
            return None
        valid = self._password_service.verify(
            PasswordVerification(
                password=credentials.password,
                stored_hash=user.password_hash,
            )
        )
        return user if valid.root else None

    def start_session(self, user_id: UserId) -> SessionToken:
        now = self._clock()
        token = SessionToken(value=self._token_factory())
        command = CreateSessionCommand(
            token=token.value,
            user_id=user_id.root,
            created_at=now,
            expires_at=now + self._session_ttl,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.create_session(command)
            unit_of_work.commit()
        return token

    def end_session(self, token: SessionToken) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.delete_session(token)
            unit_of_work.commit()

    def user_for_token(self, token: SessionToken) -> User | None:
        with self._unit_of_work_factory() as unit_of_work:
            session = unit_of_work.identities.get_session(token)
            if session is None:
                return None
            if session.expires_at < self._clock():
                unit_of_work.identities.delete_session(token)
                unit_of_work.commit()
                return None
            user = unit_of_work.identities.get_user(UserId(session.user_id))
            if user is None or not user.is_active:
                unit_of_work.identities.delete_session(token)
                unit_of_work.commit()
                return None
            return user

    def has_role(self, check: RoleCheck) -> AccessDecision:
        if check.user is None:
            return AccessDecision(False)
        return AccessDecision(ROLE_LEVEL[check.user.role] >= ROLE_LEVEL[check.minimum])

    def can_edit_stock(self, user: User | None) -> AccessDecision:
        return AccessDecision(bool(user and user.can_edit_stock))

    def can_manage_users(self, user: User | None) -> AccessDecision:
        allowed = bool(user and ROLE_LEVEL[user.role] >= ROLE_LEVEL[Role.ADMIN] and user.can_manage_users)
        return AccessDecision(allowed)

    def create_user(self, command: CreateUserCommand) -> UserId:
        with self._unit_of_work_factory() as unit_of_work:
            user_id = unit_of_work.identities.create_user(command)
            unit_of_work.commit()
            return user_id

    def create_user_with_activity(self, command: AuditedCreateUser) -> UserId:
        with self._unit_of_work_factory() as unit_of_work:
            user_id = unit_of_work.identities.create_user(command.user)
            unit_of_work.identities.add_activity(command.activity)
            unit_of_work.commit()
            return user_id

    def mutate_user(self, command: AuditedUserMutation) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.identities
            if command.kind is UserMutationKind.DELETE:
                repository.delete_user(UserId(command.user_id))
            elif command.kind is UserMutationKind.ACTIVE:
                if command.is_active is None:
                    raise ValueError("is_active is required")
                repository.set_active(UserActiveChange(user_id=command.user_id, is_active=command.is_active))
                if not command.is_active:
                    repository.delete_sessions_for_user(UserId(command.user_id))
            elif command.kind is UserMutationKind.PERMISSION:
                if command.permission is None or command.allowed is None:
                    raise ValueError("permission and allowed are required")
                repository.set_permission(
                    PermissionChange(
                        user_id=command.user_id,
                        permission=command.permission,
                        allowed=command.allowed,
                    )
                )
            elif command.kind is UserMutationKind.STORES:
                repository.set_store_access(
                    UserStoreAccessChange(
                        user_id=command.user_id,
                        store_slugs=command.store_slugs,
                    )
                )
            elif command.kind is UserMutationKind.PASSWORD:
                if not command.password_hash:
                    raise ValueError("password_hash is required")
                repository.update_password(
                    UserPasswordChange(
                        user_id=command.user_id,
                        password_hash=command.password_hash,
                    )
                )
            elif command.kind is UserMutationKind.ROLE:
                if command.role is None:
                    raise ValueError("role is required")
                repository.set_role(UserRoleChange(user_id=command.user_id, role=command.role))
            elif command.kind is UserMutationKind.SECTIONS:
                repository.set_section_access(
                    UserSectionAccessChange(
                        user_id=command.user_id,
                        section_access=command.section_access,
                    )
                )
            elif command.kind is UserMutationKind.ACCESS_POLICY:
                repository.set_access_policy(
                    UserAccessPolicyChange(
                        user_id=command.user_id,
                        access_profile=command.access_profile,
                        access_scopes=command.access_scopes,
                    )
                )
            repository.add_activity(command.activity)
            unit_of_work.commit()

    def get_user(self, query: UserId) -> User | None:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.identities.get_user(query)

    def get_user_by_login(self, query: LoginQuery) -> User | None:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.identities.get_user_by_login(query)

    def list_users(self) -> UserCollection:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.identities.list_users()

    def count_users(self) -> CountResult:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.identities.count_users()

    def count_superadmins(self, query: UserCountQuery) -> CountResult:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.identities.count_superadmins(query)

    def set_active(self, command: UserActiveChange) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.set_active(command)
            unit_of_work.commit()

    def set_permission(self, command: PermissionChange) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.set_permission(command)
            unit_of_work.commit()

    def set_store_access(self, command: UserStoreAccessChange) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.set_store_access(command)
            unit_of_work.commit()

    def update_password(self, command: UserPasswordChange) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.update_password(command)
            unit_of_work.commit()

    def delete_user(self, command: UserId) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.delete_user(command)
            unit_of_work.commit()

    def delete_sessions_for_user(self, command: UserId) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.delete_sessions_for_user(command)
            unit_of_work.commit()

    def delete_expired_sessions(self, command: ExpiredSessionsCommand) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.delete_expired_sessions(command)
            unit_of_work.commit()

    def log_activity(self, command: ActivityCommand) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.add_activity(command)
            unit_of_work.commit()

    def get_activity(self, query: ActivityLogQuery) -> ActivityLog:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.identities.get_activity(query)
