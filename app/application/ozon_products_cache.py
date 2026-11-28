import asyncio
import gzip
from collections.abc import Awaitable, Callable, Coroutine
from contextvars import Context
from dataclasses import dataclass
from time import monotonic

from pydantic import TypeAdapter

from app.core.constants import WEB
from app.dto.ozon_economics import OzonProducts, OzonProductsQuery, OzonReportRequest

_products_json = TypeAdapter(OzonProducts)
type ReportLoader = Callable[[OzonProductsQuery], Awaitable[OzonProducts]]


@dataclass(frozen=True, slots=True, kw_only=True)
class CachedOzonProducts:
    query: OzonProductsQuery
    expires_at: float
    body: bytes
    gzip_body: bytes


class OzonProductsCache:
    def __init__(
        self, ttl_seconds: int, spawn_refresh: Callable[[Coroutine[object, object, None]], asyncio.Task[None]]
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._spawn_refresh = spawn_refresh
        self._entry: CachedOzonProducts | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False

    async def get(
        self, query: OzonProductsQuery, load: ReportLoader, *, accepts_gzip: bool
    ) -> CachedOzonProducts:
        if not self.ttl_seconds:
            return await self._build(query, load, compress=accepts_gzip)
        entry = self._entry
        if entry is not None and entry.query == query:
            if monotonic() >= entry.expires_at:
                self._start_refresh(entry, load)
            return entry

        async with self._lock:
            entry = self._entry
            if entry is None or entry.query != query:
                entry = await self._build(query, load)
                self._schedule(entry, load)
            return entry

    async def _build(
        self, query: OzonProductsQuery, load: ReportLoader, *, compress: bool = True
    ) -> CachedOzonProducts:
        body = _products_json.dump_json(await load(query), by_alias=True)
        gzip_body = (
            await asyncio.to_thread(gzip.compress, body, compresslevel=WEB.ozon_products_gzip_level, mtime=0)
            if compress
            else b""
        )
        entry = CachedOzonProducts(
            query=query, expires_at=monotonic() + self.ttl_seconds, body=body, gzip_body=gzip_body
        )
        if self.ttl_seconds:
            self._entry = entry
        return entry

    def close(self) -> None:
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule(self, entry: CachedOzonProducts, load: ReportLoader) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if not self._closed:
            self._timer = asyncio.get_running_loop().call_later(
                self.ttl_seconds, self._start_refresh, entry, load, context=Context()
            )

    def _start_refresh(self, entry: CachedOzonProducts, load: ReportLoader) -> None:
        if self._closed or self._entry is not entry:
            return
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = self._spawn_refresh(self._refresh(entry, load))

    async def _refresh(self, previous: CachedOzonProducts, load: ReportLoader) -> None:
        async with self._lock:
            if self._entry is previous:
                entry = previous
                try:
                    query = OzonReportRequest().query(previous.query.stores)
                    entry = await self._build(query, load)
                finally:
                    self._refresh_task = None
                    self._schedule(entry, load)
