from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Lock
from types import TracebackType

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Connection, CursorResult
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings

DatabaseScalar = str | int | float | bytes | None
SqlParameters = Sequence[DatabaseScalar] | Mapping[str, DatabaseScalar]


class DatabaseRow(Mapping[str, DatabaseScalar]):
    def __init__(self, keys: Sequence[str], values: Sequence[DatabaseScalar]) -> None:
        self._keys = tuple(keys)
        self._values = tuple(values)
        self._by_name = dict(zip(self._keys, self._values, strict=True))

    def __getitem__(self, key: str | int) -> DatabaseScalar:
        if isinstance(key, int):
            return self._values[key]
        return self._by_name[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


class DatabaseResult:
    def __init__(self, result: CursorResult[tuple[DatabaseScalar, ...]]) -> None:
        self._result = result

    @property
    def lastrowid(self) -> int:
        return int(self._result.lastrowid or 0)

    @property
    def rowcount(self) -> int:
        return int(self._result.rowcount)

    def _row(self, row: object | None) -> DatabaseRow | None:
        if row is None:
            return None
        mapping = row._mapping
        return DatabaseRow(tuple(mapping.keys()), tuple(mapping.values()))

    def fetchone(self) -> DatabaseRow | None:
        return self._row(self._result.fetchone())

    def fetchall(self) -> list[DatabaseRow]:
        return [self._row(row) for row in self._result.fetchall()]

    def __iter__(self) -> Iterator[DatabaseRow]:
        for row in self._result:
            converted = self._row(row)
            if converted is not None:
                yield converted


class DatabaseConnection(AbstractContextManager["DatabaseConnection"]):
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def execute(self, statement: str, parameters: SqlParameters = ()) -> DatabaseResult:
        normalized = parameters if isinstance(parameters, Mapping) else tuple(parameters)
        return DatabaseResult(self._connection.exec_driver_sql(statement, normalized))

    def executemany(self, statement: str, parameters: Iterable[Sequence[DatabaseScalar]]) -> DatabaseResult:
        values = [tuple(row) for row in parameters]
        return DatabaseResult(self._connection.exec_driver_sql(statement, values))

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self._connection.exec_driver_sql(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if exc_type is not None:
            self.rollback()
        self.close()
        return None


class UnitOfWork(AbstractContextManager["UnitOfWork"]):
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self.session: Session | None = None

    def __enter__(self) -> UnitOfWork:
        self.session = self._session_factory()
        return self

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork is not active")
        self.session.commit()

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self.session is None:
            return None
        try:
            if exc_type is not None:
                self.session.rollback()
        finally:
            self.session.close()
            self.session = None
        return None


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self.engine = self._create_engine()
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    def _create_engine(self) -> Engine:
        engine = create_engine(
            f"sqlite+pysqlite:///{self.path.as_posix()}",
            connect_args={
                "check_same_thread": False,
                "timeout": settings.database_timeout_seconds,
            },
            poolclass=NullPool,
        )

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection: object, connection_record: object) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(f"PRAGMA busy_timeout = {settings.database_busy_timeout_ms}")
                cursor.execute("PRAGMA foreign_keys = ON")
            finally:
                cursor.close()

        return engine

    def connect(self) -> DatabaseConnection:
        return DatabaseConnection(self.engine.connect())

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.session_factory)

    def dispose(self) -> None:
        self.engine.dispose()


_DATABASES: dict[Path, Database] = {}
_DATABASES_LOCK = Lock()


def database_for_path(path: Path) -> Database:
    resolved = path.resolve()
    with _DATABASES_LOCK:
        database = _DATABASES.get(resolved)
        if database is None:
            database = Database(resolved)
            _DATABASES[resolved] = database
        return database


def dispose_databases() -> None:
    with _DATABASES_LOCK:
        databases = tuple(_DATABASES.values())
        _DATABASES.clear()
    for database in databases:
        database.dispose()
