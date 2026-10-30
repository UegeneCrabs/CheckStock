import asyncio
import logging
from collections.abc import Callable, Coroutine
from types import TracebackType
from typing import Self

import httpx

from app.application.ozon_promotions import OzonPromotionsCache
from app.config import settings
from app.core.constants import (
    HTTP,
)
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.async_catalog_repository import AsyncCatalogRepository
from app.infrastructure.async_commissions_repository import AsyncCommissionsRepository
from app.infrastructure.async_database import AsyncDatabase
from app.infrastructure.async_http import AsyncHttp
from app.infrastructure.async_job_repository import AsyncJobRepository
from app.infrastructure.async_orders_repository import AsyncOrdersRepository
from app.infrastructure.async_prices_repository import AsyncPricesRepository
from app.ozon.async_categories import AsyncCategoryReader
from app.ozon.async_related import AsyncRelatedReader

logger = logging.getLogger(__name__)
Finalizer = Callable[[asyncio.Task[None]], Coroutine[object, object, None]]


class AsyncResources:
    def __init__(
        self, *, database: AsyncDatabase | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.database = database or AsyncDatabase()
        self.client = httpx.AsyncClient(
            timeout=settings.ozon_request_timeout_seconds,
            limits=httpx.Limits(
                max_connections=None, max_keepalive_connections=HTTP.max_keepalive_connections
            ),
            transport=transport,
        )
        from app.container import ozon_limits_service

        self.ozon_limits = ozon_limits_service(self.database)
        self.http = AsyncHttp(self.client, limits=self.ozon_limits)
        self.ozon_categories = AsyncCategoryReader(self.http)
        self.ozon_related = AsyncRelatedReader(self.http)
        self.ozon_promotions_cache = OzonPromotionsCache()
        self.jobs = AsyncJobRepository(self.database)
        self.catalog = AsyncCatalogRepository(self.database)
        self.advertising = AsyncAdvertisingRepository(self.database)
        self.orders = AsyncOrdersRepository(self.database)
        self.prices = AsyncPricesRepository(self.database)
        self.commissions = AsyncCommissionsRepository(self.database)
        self.tasks: set[asyncio.Task[None]] = set()
        self._finalizers: set[asyncio.Task[None]] = set()
        self.closing = False

    def spawn(
        self,
        coroutine: Coroutine[object, object, None],
        *,
        name: str,
        finalize: Finalizer | None = None,
    ) -> asyncio.Task[None]:
        if self.closing:
            coroutine.close()
            raise RuntimeError("Async job runtime is stopping")
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.add(task)
        task.add_done_callback(lambda done: self._completed(done, finalize))
        return task

    def _completed(self, task: asyncio.Task[None], finalize: Finalizer | None = None) -> None:
        self.tasks.discard(task)
        if finalize is not None:
            cleanup = asyncio.create_task(finalize(task), name=f"finish:{task.get_name()}")
            self._finalizers.add(cleanup)
            cleanup.add_done_callback(self._finalized)
        self._log_error(task)

    def _finalized(self, task: asyncio.Task[None]) -> None:
        self._finalizers.discard(task)
        self._log_error(task)

    @staticmethod
    def _log_error(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(
                "async_job_task_failed task=%s",
                task.get_name(),
                exc_info=(type(error), error, error.__traceback__),
            )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.closing = True
        shutdown = asyncio.create_task(self._shutdown(cancel=exc_type is not None))
        try:
            await asyncio.shield(shutdown)
        except asyncio.CancelledError:
            for task in tuple(self.tasks):
                task.cancel()
            await shutdown
            raise

    async def _shutdown(self, *, cancel: bool) -> None:
        tasks = tuple(self.tasks)
        if cancel:
            for task in tasks:
                task.cancel()
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self._finalizers:
                await asyncio.gather(*tuple(self._finalizers), return_exceptions=True)
        finally:
            try:
                await self.client.aclose()
            finally:
                await self.database.close()
