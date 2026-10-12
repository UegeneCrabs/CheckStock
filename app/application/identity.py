import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.application.ports import IdentityUnitOfWorkFactory, PasswordService
from app.dto.identity import (
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
    SessionToken,
    User,
    UserActiveChange,
    UserCollection,
    UserCountQuery,
    UserId,
    UserMutationKind,
    UserPasswordChange,
)


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
        disabling = command.kind is UserMutationKind.DELETE or (
            command.kind is UserMutationKind.ACTIVE and command.is_active is False
        )
        if disabling and command.activity.user_id == command.user_id:
            raise ValueError("Нельзя удалить или заблокировать самого себя")
        with self._unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.identities
            if command.kind is UserMutationKind.DELETE:
                repository.delete_user(UserId(command.user_id))
            elif command.kind is UserMutationKind.ACTIVE:
                if command.is_active is None:
                    raise ValueError("is_active is required")
                repository.set_active(UserActiveChange(user_id=command.user_id, is_active=command.is_active))
            elif command.kind is UserMutationKind.PASSWORD:
                if not command.password_hash:
                    raise ValueError("password_hash is required")
                repository.update_password(
                    UserPasswordChange(
                        user_id=command.user_id,
                        password_hash=command.password_hash,
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

    def count_active_users(self, query: UserCountQuery) -> CountResult:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.identities.count_active_users(query)

    def set_active(self, command: UserActiveChange) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.identities.set_active(command)
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
