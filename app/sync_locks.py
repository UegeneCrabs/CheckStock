"""Non-blocking job locks shared by web workers and local scheduled collectors."""

import hashlib
import os
from contextlib import contextmanager

from app.infrastructure.database import database_for_path
from app.repositories import core


class SyncJobBusyError(RuntimeError):
    def __init__(self):
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


def acquire(name: str):
    digest = hashlib.sha256(("checkstock-sync:" + name).encode()).digest()
    database = database_for_path(core.DB_PATH)
    if database.dialect_name == "postgresql":
        connection = database.connect()
        key = int.from_bytes(digest[:8], "big", signed=True)
        try:
            acquired = connection.execute("SELECT pg_try_advisory_lock(?) AS acquired", (key,)).fetchone()["acquired"]
            connection.commit()
            if not acquired:
                raise SyncJobBusyError()
        except Exception:
            connection.close()
            raise
        return _PostgresLock(connection, key)
    directory = core.DB_PATH.parent / ".sync-locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest.hex() + ".lock")
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
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
        handle = acquire(name)
    except SyncJobBusyError:
        return True
    handle.close()
    return False
