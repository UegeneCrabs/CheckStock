import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import BinaryIO

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import (
    LOCKS,
    DatabaseDialect,
)
from app.infrastructure.async_database import AsyncDatabase
from app.jobs.locks import SyncJobBusyError, acquire_file, lock_digest


class AsyncJobLock:
    def __init__(
        self,
        *,
        connection: AsyncConnection | None = None,
        key: int | None = None,
        file: BinaryIO | None = None,
    ) -> None:
        self.connection, self.key, self.file = connection, key, file

    async def close(self) -> None:
        if self.file is not None:
            self.file.close()
        if self.connection is not None:
            try:
                await self.connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
                await self.connection.commit()
            finally:
                await self.connection.close()


async def acquire(name: str, database: AsyncDatabase) -> AsyncJobLock:
    if database.dialect != DatabaseDialect.POSTGRESQL:
        return AsyncJobLock(file=acquire_file(name, database.path))
    connection = await database.lock_engine.connect()
    key = int.from_bytes(
        lock_digest(name)[: LOCKS.postgres_key_bytes], LOCKS.postgres_byte_order, signed=True
    )
    try:
        result = await connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        acquired = result.scalar()
        await connection.commit()
        if not acquired:
            raise SyncJobBusyError()
    except BaseException:
        await connection.close()
        raise
    return AsyncJobLock(connection=connection, key=key)


@asynccontextmanager
async def hold(name: str, database: AsyncDatabase, *, wait: bool = False) -> AsyncIterator[None]:
    while True:
        try:
            handle = await acquire(name, database)
            break
        except SyncJobBusyError:
            if not wait:
                raise
            await asyncio.sleep(LOCKS.retry_seconds)
    try:
        yield
    finally:
        await handle.close()


async def is_running(name: str, database: AsyncDatabase) -> bool:
    try:
        handle = await acquire(name, database)
    except SyncJobBusyError:
        return True
    await handle.close()
    return False
