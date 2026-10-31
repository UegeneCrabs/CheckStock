import asyncio
import math
import random
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.application.ozon_limits import OzonLimitsService
from app.core.constants import (
    HTTP,
    OZON_API_LIMITS,
    HTTPStatus,
)
from app.core.logging_config import tagged_logger
from app.dto.ozon_limits import OzonRateFeedback

logger = tagged_logger(__name__, "infrastructure", "async_http")
store_context: ContextVar[str] = ContextVar("async_http_store", default="")


def retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = float(value)
        return max(0, parsed) if math.isfinite(parsed) else None
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            return max(0, (moment - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


class AsyncHttp:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        limits: OzonLimitsService | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.client = client
        self.limits = limits
        self.sleep = sleep

    async def request(
        self,
        method: str,
        url: str,
        *,
        attempts: int,
        backoff: float,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str | int] | None = None,
        json: object = None,
    ) -> httpx.Response:
        scopes = self.limits.seller_scopes(url, headers) if self.limits is not None else ()
        for attempt in range(1, attempts + 1):
            pause = None
            if self.limits is not None:
                await self.limits.wait(scopes)
            try:
                response = await self.client.request(method, url, headers=headers, params=params, json=json)
                if self.limits is not None and scopes:
                    try:
                        remaining = int(response.headers[OZON_API_LIMITS.remaining_header])
                    except (KeyError, ValueError):
                        remaining = None
                    await self.limits.observe(
                        scopes[-1],
                        OzonRateFeedback(
                            status=response.status_code,
                            retry_seconds=retry_after(response.headers.get(OZON_API_LIMITS.retry_header)),
                            remaining=remaining,
                        ),
                    )
                if (
                    response.status_code != HTTPStatus.TOO_MANY_REQUESTS
                    and response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
                ):
                    return response
                if attempt == attempts:
                    return response
                pause = retry_after(response.headers.get("Retry-After"))
                await response.aclose()
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                if attempt == attempts:
                    raise
            if pause is None:
                pause = backoff * HTTP.retry_backoff_multiplier ** (attempt - 1) + random.uniform(
                    0, HTTP.retry_jitter_max_seconds
                )
            logger.warning(
                "async_http_retry store=%s attempt=%s delay=%.1f", store_context.get(), attempt, pause
            )
            await self.sleep(pause)
        raise ValueError("HTTP attempts must be positive")
