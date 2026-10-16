import hashlib
import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from app.core.constants import (
    LOCKS,
    DatabaseDialect,
)
from app.infrastructure.database import database_for_path
from app.repositories import core


class SyncJobBusyError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Эта выгрузка уже выполняется")


class _PostgresLock:
    def __init__(self, connection, key: int):
        self.connection, self.key = connection, key

    def close(self):
        try:
            self.connection.execute("SELECT pg_advisory_unlock(?)", (self.key,))
            self.connection.commit()
        finally:
            self.connection.close()


def lock_digest(name: str) -> bytes:
    return hashlib.sha256((LOCKS.namespace + name).encode()).digest()


def acquire(name: str, *, detached: bool = True):
    digest = lock_digest(name)
    database = database_for_path(core.DB_PATH)
    if database.dialect_name == DatabaseDialect.POSTGRESQL:
        connection = database.connect()
        key = int.from_bytes(digest[: LOCKS.postgres_key_bytes], LOCKS.postgres_byte_order, signed=True)
        try:
            if detached:
                connection.detach()
            acquired = connection.execute("SELECT pg_try_advisory_lock(?) AS acquired", (key,)).fetchone()[
                "acquired"
            ]
            connection.commit()
            if not acquired:
                raise SyncJobBusyError()
        except Exception:
            connection.close()
            raise
        return _PostgresLock(connection, key)
    return acquire_file(name, core.DB_PATH)


def acquire_file(name: str, database_path: Path) -> BinaryIO:
    directory = database_path.parent / LOCKS.directory
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (lock_digest(name).hex() + LOCKS.file_suffix)
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, LOCKS.file_length_bytes)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SyncJobBusyError() from None
    return handle


@contextmanager
def hold(name: str):
    handle = acquire(name)
    try:
        yield
    finally:
        handle.close()


def is_running(name: str) -> bool:
    try:
        handle = acquire(name, detached=False)
    except SyncJobBusyError:
        return True
    handle.close()
    return False
