import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import Table, event
from sqlalchemy.dialects.postgresql import Insert as PostgresInsert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import Insert as SqliteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import ConnectionPoolEntry, NullPool

from app.config import settings
from app.core.constants import (
    DATABASE,
    DatabaseDialect,
)
from app.dto.database import PostgresConnectionOptions
from app.infrastructure.orm import OrmBase


def postgres_connection_options(url: str) -> PostgresConnectionOptions:
    address = make_url(url).set(drivername=DATABASE.postgres_driver)
    query = {
        name: values[0] if len(values) == 1 else tuple(values)
        for name, values in parse_qs(urlsplit(url).query, keep_blank_values=True).items()
    }
    raw_timeout = query.pop("connect_timeout", None)
    application_name = query.pop("application_name", None)
    if raw_timeout is not None and not isinstance(raw_timeout, str):
        raise ValueError("PostgreSQL connect_timeout must be a single integer")
    try:
        timeout = int(raw_timeout) if raw_timeout is not None else None
        if timeout is not None and timeout > 0:
            float(timeout)
    except (ValueError, OverflowError):
        raise ValueError("PostgreSQL connect_timeout must be a finite integer") from None
    if application_name is not None and not isinstance(application_name, str):
        raise ValueError("PostgreSQL application_name must be a single string")
    return PostgresConnectionOptions(
        dsn=address.set(query=query).render_as_string(hide_password=False),
        connect_timeout=timeout,
        application_name=application_name,
    )


class AsyncDatabase:
    def __init__(self, *, url: str | None = None, path: Path | None = None):
        target = settings.database_url if url is None else url
        self.path = path or settings.database_path
        if target:
            options = postgres_connection_options(target)
            address = URL.create(DATABASE.postgres_async_driver)
            self.engine = create_async_engine(
                address, pool_pre_ping=True, connect_args=options.connect_arguments()
            )
            self.lock_engine = create_async_engine(
                address, poolclass=NullPool, connect_args=options.connect_arguments()
            )
        else:
            address = URL.create(DATABASE.sqlite_async_driver, database=str(self.path.resolve()))
            self.engine = create_async_engine(
                address,
                poolclass=NullPool,
                connect_args={"timeout": settings.database_timeout_seconds},
            )
            self.lock_engine = self.engine

            @event.listens_for(self.engine.sync_engine, "connect")
            def configure_sqlite(connection: DBAPIConnection, record: ConnectionPoolEntry) -> None:
                cursor = connection.cursor()
                cursor.execute(f"PRAGMA busy_timeout = {settings.database_busy_timeout_ms}")
                cursor.execute("PRAGMA foreign_keys = ON")
                cursor.close()

        self.dialect = DatabaseDialect(self.engine.dialect.name)
        self._writer = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self, *, write: bool = False) -> AsyncIterator[AsyncConnection]:
        serialize = write and self.dialect == DatabaseDialect.SQLITE
        if serialize:
            await self._writer.acquire()
        try:
            async with self.engine.connect() as connection, connection.begin():
                if serialize:
                    await connection.exec_driver_sql("BEGIN IMMEDIATE")
                yield connection
        finally:
            if serialize:
                self._writer.release()

    async def close(self) -> None:
        await self.engine.dispose()
        if self.lock_engine is not self.engine:
            await self.lock_engine.dispose()


def table_for(model: type[OrmBase]) -> Table:
    table = model.__table__
    if not isinstance(table, Table):
        raise TypeError("A mapped database table is required")
    return table


def upsert(
    connection: AsyncConnection,
    table: Table,
    values: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    updates: Mapping[str, object] | None = None,
) -> PostgresInsert | SqliteInsert:
    statement = (
        postgres_insert(table)
        if connection.dialect.name == DatabaseDialect.POSTGRESQL
        else sqlite_insert(table)
    ).values(**values)
    assignments = (
        {name: getattr(statement.excluded, name) for name in values if name not in keys}
        if updates is None
        else updates
    )
    return statement.on_conflict_do_update(index_elements=list(keys), set_=assignments)
