"""Bounded report cache; committed database changes invalidate every stored result."""

import logging
import pickle
from collections import OrderedDict
from concurrent.futures import Future
from threading import Lock
from time import monotonic

from app.dto.identity import coerce_user
from app.infrastructure.database import database_for_path
from app.repositories import core

logger = logging.getLogger(__name__)


def user_key(user):
    normalized = coerce_user(user)
    return normalized.model_dump_json(exclude={"password_hash"}) if normalized is not None else None


class DatabaseRevision:
    """SQLite data_version must be compared on the same dedicated connection.

    PostgreSQL's snapshot changes when writing transactions start or finish;
    reads here do not assign transaction IDs. No schema changes are required.
    """

    def __init__(self):
        self._lock = Lock()
        self._connection = None
        self._identity = None
        self._generation = 0

    def __call__(self):
        with self._lock:
            try:
                database = database_for_path(core.DB_PATH)
                if database.dialect_name == "postgresql":
                    with database.connect() as conn:
                        row = conn.execute(
                            "SELECT pg_current_snapshot()::text, pg_postmaster_start_time()::text"
                        ).fetchone()
                    return (id(database), str(row[0]), str(row[1]))
                stat = database.path.stat()
                identity = (id(database), stat.st_dev, stat.st_ino)
                if self._identity != identity or self._connection is None:
                    self._close()
                    self._connection = database.connect()
                    self._identity = identity
                    self._generation += 1
                version = self._connection.execute("PRAGMA data_version").fetchone()[0]
                self._connection.rollback()
                return (identity, self._generation, int(version))
            except Exception:
                self._close()
                logger.warning("report_cache_revision_unavailable", exc_info=True)
                return None

    def _close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def close(self):
        with self._lock:
            self._close()


class ReportCache:
    def __init__(self, revision=None, *, ttl=30, max_entries=32, max_bytes=32 * 1024 * 1024):
        self.revision = revision or DatabaseRevision()
        self.ttl = ttl
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lock = Lock()
        self._entries = OrderedDict()
        self._pending = {}
        self._version = None
        self._bytes = 0

    def get(self, key, loader):
        version = self.revision()
        if version is None:
            return loader()
        pending_key = (version, key)
        with self._lock:
            if self._version != version:
                self._entries.clear()
                self._bytes = 0
                self._version = version
            cached = self._entries.get(key)
            if cached is not None and monotonic() < cached[0]:
                self._entries.move_to_end(key)
                return pickle.loads(cached[1])
            future = self._pending.get(pending_key)
            owner = future is None
            if owner:
                future = self._pending[pending_key] = Future()
        if not owner:
            future.result()
            return self.get(key, loader)
        try:
            result = loader()
            # Only internally generated values are serialized; no external pickle input.
            data = pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL)
            unchanged = self.revision() == version
            with self._lock:
                if unchanged and self._version == version and len(data) <= self.max_bytes:
                    previous = self._entries.pop(key, None)
                    if previous is not None:
                        self._bytes -= len(previous[1])
                    self._entries[key] = (monotonic() + self.ttl, data)
                    self._bytes += len(data)
                    while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                        _, (_, removed) = self._entries.popitem(last=False)
                        self._bytes -= len(removed)
                self._pending.pop(pending_key, None)
            future.set_result(None)
            return result
        except BaseException as error:
            with self._lock:
                self._pending.pop(pending_key, None)
            future.set_exception(error)
            raise


reports_cache = ReportCache()
