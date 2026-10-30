import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.application.ports import OzonLimitStore
from app.config import settings
from app.core.constants import (
    OZON_ADVERTISING,
    OZON_ANALYTICS,
    OZON_API,
    OZON_API_LIMITS,
    OZON_LOGISTICS,
    OZON_STOCK_ANALYTICS,
    AdvertisingError,
)
from app.dto.ozon_limits import (
    OzonRateFeedback,
    OzonRateScope,
    PerformanceCompletion,
    PerformanceIdentity,
    PerformanceLease,
    PerformanceQuotaRequest,
    PerformanceReservation,
)


class OzonQuotaExceeded(Exception):
    def __init__(self, code: AdvertisingError):
        self.code = code
        super().__init__(code)


def account_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class OzonLimitsService:
    def __init__(
        self,
        repository: OzonLimitStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.repository = repository
        self.clock = clock
        self.sleep = sleep

    @staticmethod
    def seller_scopes(url: str, headers: Mapping[str, str] | None) -> tuple[OzonRateScope, ...]:
        target = urlsplit(url)
        if target.hostname != urlsplit(OZON_API.base_url).hostname:
            return ()
        client = next((v for k, v in (headers or {}).items() if k.casefold() == "client-id"), "")
        if not client:
            return ()
        account = account_digest(client)
        spacing = 1 / settings.ozon_api_limits.seller_requests_per_second
        return (
            OzonRateScope(key=OZON_API_LIMITS.seller_scope_prefix + account, minimum_spacing=spacing),
            OzonRateScope(
                key=OZON_API_LIMITS.method_scope_prefix + account_digest(account + target.path),
                minimum_spacing=OZON_ANALYTICS.method_minimum_spacing_seconds
                if target.path == OZON_ANALYTICS.funnel_path
                else max(spacing, OZON_STOCK_ANALYTICS.minimum_interval_seconds)
                if target.path == OZON_STOCK_ANALYTICS.path
                else settings.ozon_logistics_request_interval_seconds
                if target.path == OZON_LOGISTICS.accrual_path
                else spacing,
            ),
        )

    async def wait(self, scopes: tuple[OzonRateScope, ...]) -> None:
        if not scopes:
            return
        while (delay := await self.repository.reserve_rate(scopes, self.clock())) > 0:
            await self.sleep(delay)

    async def observe(self, scope: OzonRateScope, feedback: OzonRateFeedback) -> None:
        await self.repository.observe_rate(scope, feedback, self.clock())

    async def wait_performance(self, identity: PerformanceIdentity) -> None:
        await self.wait(
            (
                OzonRateScope(
                    key=OZON_API_LIMITS.performance_scope_prefix + identity.account_scope,
                    minimum_spacing=OZON_ADVERTISING.request_spacing_seconds,
                ),
            )
        )

    async def observe_performance(self, identity: PerformanceIdentity, feedback: OzonRateFeedback) -> None:
        await self.observe(
            OzonRateScope(
                key=OZON_API_LIMITS.performance_scope_prefix + identity.account_scope,
                minimum_spacing=OZON_ADVERTISING.request_spacing_seconds,
            ),
            feedback,
        )

    async def reserve_performance(self, request: PerformanceQuotaRequest) -> PerformanceReservation:
        decision = await self.repository.reserve_performance(request, self.clock())
        if decision.error is not None:
            raise OzonQuotaExceeded(decision.error)
        if decision.reservation is None:
            raise RuntimeError("Performance reservation is missing")
        return decision.reservation

    async def finish_performance(self, completion: PerformanceCompletion) -> None:
        await self.repository.finish_performance(completion)

    @asynccontextmanager
    async def report(self, identity: PerformanceIdentity) -> AsyncIterator[PerformanceLease]:
        while (lease := await self.repository.acquire_report(identity, self.clock())) is None:
            await self.sleep(OZON_API_LIMITS.queue_poll_seconds)

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(OZON_API_LIMITS.lease_seconds / 3)
                await self.repository.renew_report(lease, self.clock())

        renewal = asyncio.create_task(heartbeat())
        try:
            yield lease
        finally:

            async def finish() -> None:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
                await self.repository.release_report(lease)

            cleanup = asyncio.create_task(finish())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError

    async def renew(self, lease: PerformanceLease) -> None:
        await self.repository.renew_report(lease, self.clock())

    async def resolve_report(self, identity: PerformanceIdentity, request_key: str) -> None:
        await self.repository.resolve_report(identity, request_key)
