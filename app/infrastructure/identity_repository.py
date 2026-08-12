from __future__ import annotations

from datetime import datetime
from types import TracebackType

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

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
    PermissionChange,
    Role,
    SessionData,
    SessionToken,
    User,
    UserActiveChange,
    UserCollection,
    UserCountQuery,
    UserId,
    UserPasswordChange,
    UserStoreAccessChange,
    WbTokenInfo,
    WbTokenInfoCollection,
    WbTokenInfoCommand,
)
from app.infrastructure.orm import (
    ActivityLogRecord,
    SessionRecord,
    UserRecord,
    UserStoreAccessRecord,
    WbTokenInfoRecord,
)
from app.stores import STORES


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _normalize_store_slugs(values: tuple[str, ...]) -> tuple[str, ...]:
    requested = {value.strip().lower() for value in values}
    return tuple(slug for slug in STORES if slug in requested) or tuple(STORES)


def _user(record: UserRecord) -> User:
    return User(
        id=record.id,
        full_name=record.full_name,
        google_email=record.google_email,
        login=record.login,
        password_hash=record.password_hash,
        role=Role(record.role),
        is_active=bool(record.is_active),
        can_edit_stock=bool(record.can_edit_stock),
        can_manage_users=bool(record.can_manage_users),
        created_at=_as_datetime(record.created_at),
        store_slugs=tuple(access.store_slug for access in record.store_access) or tuple(STORES),
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
            role=command.role.value,
            created_at=command.created_at.isoformat(),
        )
        record.store_access = [
            UserStoreAccessRecord(store_slug=slug) for slug in _normalize_store_slugs(command.store_slugs)
        ]
        self._session.add(record)
        self._session.flush()
        return UserId(record.id)

    def get_user(self, query: UserId) -> User | None:
        record = self._session.scalar(
            select(UserRecord)
            .options(selectinload(UserRecord.store_access))
            .where(UserRecord.id == query.root)
        )
        return _user(record) if record is not None else None

    def get_user_by_login(self, query: LoginQuery) -> User | None:
        record = self._session.scalar(
            select(UserRecord)
            .options(selectinload(UserRecord.store_access))
            .where(UserRecord.login == query.login)
        )
        return _user(record) if record is not None else None

    def list_users(self) -> UserCollection:
        records = self._session.scalars(
            select(UserRecord).options(selectinload(UserRecord.store_access)).order_by(UserRecord.id)
        )
        return UserCollection(tuple(_user(record) for record in records))

    def count_users(self) -> CountResult:
        return CountResult(self._session.scalar(select(func.count(UserRecord.id))) or 0)

    def count_superadmins(self, query: UserCountQuery) -> CountResult:
        statement = select(func.count(UserRecord.id)).where(
            UserRecord.role == Role.SUPERADMIN.value,
            UserRecord.is_active == 1,
        )
        if query.exclude_user_id is not None:
            statement = statement.where(UserRecord.id != query.exclude_user_id)
        return CountResult(self._session.scalar(statement) or 0)

    def set_active(self, command: UserActiveChange) -> None:
        record = self._session.get(UserRecord, command.user_id)
        if record is not None:
            record.is_active = int(command.is_active)

    def set_permission(self, command: PermissionChange) -> None:
        record = self._session.get(UserRecord, command.user_id)
        if record is not None:
            setattr(record, command.permission.value, int(command.allowed))

    def set_store_access(self, command: UserStoreAccessChange) -> None:
        record = self._session.scalar(
            select(UserRecord)
            .options(selectinload(UserRecord.store_access))
            .where(UserRecord.id == command.user_id)
        )
        if record is not None:
            record.store_access = [
                UserStoreAccessRecord(store_slug=slug) for slug in _normalize_store_slugs(command.store_slugs)
            ]

    def update_password(self, command: UserPasswordChange) -> None:
        record = self._session.get(UserRecord, command.user_id)
        if record is not None:
            record.password_hash = command.password_hash
        self.delete_sessions_for_user(UserId(command.user_id))

    def delete_user(self, command: UserId) -> None:
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
