from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from threading import Lock
from types import TracebackType

from sqlalchemy import Engine, create_engine, event, inspect
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
    def __init__(self, result: CursorResult[tuple[DatabaseScalar, ...]] | None) -> None:
        self._result = result

    @property
    def lastrowid(self) -> int:
        if self._result is None:
            return 0
        if self._result.returns_rows:
            row = self._result.fetchone()
            return int(row[0]) if row is not None else 0
        return int(self._result.lastrowid or 0)

    @property
    def rowcount(self) -> int:
        if self._result is None:
            return 0
        return int(self._result.rowcount)

    def _row(self, row: object | None) -> DatabaseRow | None:
        if row is None:
            return None
        mapping = row._mapping
        return DatabaseRow(tuple(mapping.keys()), tuple(mapping.values()))

    def fetchone(self) -> DatabaseRow | None:
        if self._result is None:
            return None
        return self._row(self._result.fetchone())

    def fetchall(self) -> list[DatabaseRow]:
        if self._result is None:
            return []
        return [self._row(row) for row in self._result.fetchall()]

    def __iter__(self) -> Iterator[DatabaseRow]:
        if self._result is None:
            return
        for row in self._result:
            converted = self._row(row)
            if converted is not None:
                yield converted


def _postgresql_parameters(statement: str) -> str:
    """Convert SQLite-style qmark parameters without touching quoted literals."""
    converted: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(statement):
        char = statement[index]
        if quote is not None:
            converted.append(char)
            if char == quote:
                if index + 1 < len(statement) and statement[index + 1] == quote:
                    converted.append(statement[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            converted.append(char)
        elif char == "?":
            converted.append("%s")
        else:
            converted.append(char)
        index += 1
    return "".join(converted)


class DatabaseConnection(AbstractContextManager["DatabaseConnection"]):
    def __init__(self, connection: Connection, dialect_name: str) -> None:
        self._connection = connection
        self.dialect_name = dialect_name

    def _statement(self, statement: str) -> str:
        if self.dialect_name == "postgresql":
            return _postgresql_parameters(statement)
        return statement

    def execute(self, statement: str, parameters: SqlParameters = ()) -> DatabaseResult:
        normalized = parameters if isinstance(parameters, Mapping) else tuple(parameters)
        return DatabaseResult(self._connection.exec_driver_sql(self._statement(statement), normalized))

    def executemany(self, statement: str, parameters: Iterable[Sequence[DatabaseScalar]]) -> DatabaseResult:
        values = [tuple(row) for row in parameters]
        if not values:
            return DatabaseResult(None)
        return DatabaseResult(self._connection.exec_driver_sql(self._statement(statement), values))

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self._connection.exec_driver_sql(self._statement(statement))

    def column_names(self, table: str) -> set[str]:
        return {str(column["name"]) for column in inspect(self._connection).get_columns(table)}

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
    def __init__(self, path: Path | None = None, *, url: str | None = None) -> None:
        if (path is None) == (url is None):
            raise ValueError("exactly one of path or url must be provided")
        self.path = path.resolve() if path is not None else None
        self.url = url
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = self._create_engine()
        self.dialect_name = self.engine.dialect.name
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    def _create_engine(self) -> Engine:
        if self.url is not None:
            return create_engine(self.url, pool_pre_ping=True)

        if self.path is None:
            raise RuntimeError("SQLite path is missing")
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
        return DatabaseConnection(self.engine.connect(), self.dialect_name)

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.session_factory)

    def dispose(self) -> None:
        self.engine.dispose()


_DATABASES: dict[str, Database] = {}
_DATABASES_LOCK = Lock()


def database_for_path(path: Path) -> Database:
    resolved = path.resolve()
    if settings.database_url and resolved == settings.database_path.resolve():
        return database_for_url(settings.database_url)
    key = f"sqlite:{resolved}"
    with _DATABASES_LOCK:
        database = _DATABASES.get(key)
        if database is None:
            database = Database(resolved)
            _DATABASES[key] = database
        return database


def database_for_url(url: str) -> Database:
    key = f"url:{url}"
    with _DATABASES_LOCK:
        database = _DATABASES.get(key)
        if database is None:
            database = Database(url=url)
            _DATABASES[key] = database
        return database


def dispose_databases() -> None:
    with _DATABASES_LOCK:
        databases = tuple(_DATABASES.values())
        _DATABASES.clear()
    for database in databases:
        database.dispose()
