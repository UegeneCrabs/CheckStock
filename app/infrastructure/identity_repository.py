from __future__ import annotations

from datetime import datetime
from types import TracebackType

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.ports import IdentityRepository
from app.dto.identity import (
    ActivityCommand,
    ActivityEntry,
    ActivityLog,
    ActivityLogQuery,
    CountResult,
    CreateSessionCommand,
    CreateUserCommand,
    ExpiredSessionsCommand,
    LoginQuery,
    SessionData,
    SessionToken,
    User,
    UserActiveChange,
    UserCollection,
    UserCountQuery,
    UserId,
    UserPasswordChange,
    WbTokenInfo,
    WbTokenInfoCollection,
    WbTokenInfoCommand,
)
from app.infrastructure.orm import ActivityLogRecord, SessionRecord, UserRecord, WbTokenInfoRecord


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _user(record: UserRecord) -> User:
    return User(
        id=record.id,
        full_name=record.full_name,
        google_email=record.google_email,
        login=record.login,
        password_hash=record.password_hash,
        is_active=bool(record.is_active),
        created_at=_as_datetime(record.created_at),
    )


class SqlAlchemyIdentityRepository(IdentityRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_user(self, command: CreateUserCommand) -> UserId:
        record = UserRecord(
            full_name=command.full_name,
            google_email=command.google_email,
            login=command.login,
            password_hash=command.password_hash,
            created_at=command.created_at.isoformat(),
        )
        self._session.add(record)
        self._session.flush()
        return UserId(record.id)

    def get_user(self, query: UserId) -> User | None:
        record = self._session.get(UserRecord, query.root)
        return _user(record) if record is not None else None

    def get_user_by_login(self, query: LoginQuery) -> User | None:
        record = self._session.scalar(select(UserRecord).where(UserRecord.login == query.login))
        return _user(record) if record is not None else None

    def list_users(self) -> UserCollection:
        records = self._session.scalars(select(UserRecord).order_by(UserRecord.id))
        return UserCollection(tuple(_user(record) for record in records))

    def count_users(self) -> CountResult:
        return CountResult(self._session.scalar(select(func.count(UserRecord.id))) or 0)

    def count_active_users(self, query: UserCountQuery) -> CountResult:
        statement = select(func.count(UserRecord.id)).where(UserRecord.is_active == 1)
        if query.exclude_user_id is not None:
            statement = statement.where(UserRecord.id != query.exclude_user_id)
        return CountResult(self._session.scalar(statement) or 0)

    def _protect_last_active_user(self, user_id: int) -> None:
        # Serialize deactivation/deletion so concurrent requests cannot remove
        # the last two active accounts in separate transactions.
        connection = self._session.connection()
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            self._session.execute(select(UserRecord.id).order_by(UserRecord.id).with_for_update()).all()
        record = self._session.get(UserRecord, user_id)
        if (
            record is not None
            and record.is_active
            and not self.count_active_users(UserCountQuery(exclude_user_id=user_id)).root
        ):
            raise ValueError("Нельзя удалить или заблокировать последнего активного пользователя")

    def set_active(self, command: UserActiveChange) -> None:
        if not command.is_active:
            self._protect_last_active_user(command.user_id)
        record = self._session.get(UserRecord, command.user_id)
        if record is not None:
            record.is_active = int(command.is_active)
        if not command.is_active:
            self.delete_sessions_for_user(UserId(command.user_id))

    def update_password(self, command: UserPasswordChange) -> None:
        record = self._session.get(UserRecord, command.user_id)
        if record is not None:
            record.password_hash = command.password_hash
        self.delete_sessions_for_user(UserId(command.user_id))

    def delete_user(self, command: UserId) -> None:
        self._protect_last_active_user(command.root)
        self.delete_sessions_for_user(command)
        record = self._session.get(UserRecord, command.root)
        if record is not None:
            self._session.delete(record)

    def create_session(self, command: CreateSessionCommand) -> None:
        self._session.add(
            SessionRecord(
                token=command.token,
                user_id=command.user_id,
                created_at=command.created_at.isoformat(),
                expires_at=command.expires_at.isoformat(),
            )
        )

    def get_session(self, query: SessionToken) -> SessionData | None:
        record = self._session.get(SessionRecord, query.value)
        if record is None:
            return None
        return SessionData(
            token=record.token,
            user_id=record.user_id,
            created_at=_as_datetime(record.created_at),
            expires_at=_as_datetime(record.expires_at),
        )

    def delete_session(self, command: SessionToken) -> None:
        record = self._session.get(SessionRecord, command.value)
        if record is not None:
            self._session.delete(record)

    def delete_sessions_for_user(self, command: UserId) -> None:
        self._session.execute(delete(SessionRecord).where(SessionRecord.user_id == command.root))

    def delete_expired_sessions(self, command: ExpiredSessionsCommand) -> None:
        self._session.execute(delete(SessionRecord).where(SessionRecord.expires_at < command.now.isoformat()))

    def add_activity(self, command: ActivityCommand) -> None:
        self._session.add(
            ActivityLogRecord(
                user_id=command.user_id,
                user_name=command.user_name,
                action=command.action,
                details=command.details,
                created_at=command.created_at.isoformat(),
            )
        )

    def get_activity(self, query: ActivityLogQuery) -> ActivityLog:
        records = self._session.scalars(
            select(ActivityLogRecord).order_by(ActivityLogRecord.id.desc()).limit(query.limit)
        )
        return ActivityLog(
            tuple(
                ActivityEntry(
                    id=record.id,
                    user_id=record.user_id,
                    user_name=record.user_name,
                    action=record.action,
                    details=record.details,
                    operation_id=record.operation_id,
                    created_at=_as_datetime(record.created_at),
                )
                for record in records
            )
        )

    def upsert_wb_token_info(self, command: WbTokenInfoCommand) -> None:
        record = self._session.get(WbTokenInfoRecord, command.store_slug)
        if record is None:
            record = WbTokenInfoRecord(
                store_slug=command.store_slug, checked_at=command.checked_at.isoformat()
            )
            self._session.add(record)
        record.expires_at = command.expires_at.isoformat() if command.expires_at else None
        record.checked_at = command.checked_at.isoformat()

    def get_wb_token_infos(self) -> WbTokenInfoCollection:
        records = self._session.scalars(select(WbTokenInfoRecord).order_by(WbTokenInfoRecord.store_slug))
        return WbTokenInfoCollection(
            tuple(
                WbTokenInfo(
                    store_slug=record.store_slug,
                    expires_at=_as_datetime(record.expires_at) if record.expires_at else None,
                    checked_at=_as_datetime(record.checked_at),
                )
                for record in records
            )
        )


class SqlAlchemyIdentityUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.identities: SqlAlchemyIdentityRepository

    def __enter__(self) -> SqlAlchemyIdentityUnitOfWork:
        self._session = self._session_factory()
        self.identities = SqlAlchemyIdentityRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._session is None:
            return None
        try:
            if exc_type is not None:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
        return None
