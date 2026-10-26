from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.constants import DatabaseDialect
from app.dto.common import DtoModel
from app.infrastructure.async_database import AsyncDatabase
from app.infrastructure.database import DatabaseConnection, DatabaseResult, DatabaseScalar, SqlParameters
from app.infrastructure.read_context import repository_read_scope


def bind_parameters(
    statement: str, values: Sequence[DatabaseScalar]
) -> tuple[str, dict[str, DatabaseScalar]]:
    parts: list[str] = []
    parameters: dict[str, DatabaseScalar] = {}
    quote: str | None = None
    index = 0
    while index < len(statement):
        char = statement[index]
        if quote:
            parts.append(char)
            if char == quote:
                if index + 1 < len(statement) and statement[index + 1] == quote:
                    parts.append(char)
                    index += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            parts.append(char)
        elif char == "?":
            key = f"p{len(parameters)}"
            parameters[key] = values[len(parameters)]
            parts.append(f":{key}")
        else:
            parts.append(char)
        index += 1
    if len(parameters) != len(values):
        raise ValueError("SQL parameter count mismatch")
    return "".join(parts), parameters


class AsyncReadConnection(DatabaseConnection):
    def execute(self, statement: str, parameters: SqlParameters = ()) -> DatabaseResult:
        if statement.lstrip().split(None, 1)[0].upper() not in ("SELECT", "WITH", "EXPLAIN"):
            raise RuntimeError("A read model cannot mutate the database")
        if isinstance(parameters, Mapping):
            query, bindings = statement, dict(parameters)
        else:
            query, bindings = bind_parameters(statement, parameters)
        return DatabaseResult(self._connection.execute(text(query), bindings))

    @contextmanager
    def stream(
        self, statement: str, parameters: SqlParameters = (), *, batch_size: int = 128
    ) -> Iterator[DatabaseResult]:
        yield self.execute(statement, parameters)

    def executemany(self, statement: str, parameters: Iterable[Sequence[DatabaseScalar]]) -> DatabaseResult:
        raise RuntimeError("A read model cannot mutate the database")

    def executescript(self, script: str) -> None:
        raise RuntimeError("A read model cannot mutate the database")

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class AsyncReadModels:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def read[T: DtoModel](self, loader: Callable[[], T]) -> T:
        def load(connection: Connection) -> T:
            with repository_read_scope(lambda: AsyncReadConnection(connection, connection.dialect.name)):
                return loader()

        async with self.database.transaction() as connection:
            if connection.dialect.name == DatabaseDialect.POSTGRESQL:
                await connection.execute(text("SET TRANSACTION READ ONLY"))
            else:
                await connection.execute(text("PRAGMA query_only = ON"))
            try:
                return await connection.run_sync(load)
            finally:
                if connection.dialect.name == DatabaseDialect.SQLITE:
                    await connection.execute(text("PRAGMA query_only = OFF"))
