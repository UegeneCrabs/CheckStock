import asyncio
import logging
import random
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.core.constants import (
    HTTP,
    HTTPStatus,
)

logger = logging.getLogger(__name__)
store_context: ContextVar[str] = ContextVar("async_http_store", default="")


def retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0, float(value))
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            return max(0, (moment - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


class AsyncHttp:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

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
        for attempt in range(1, attempts + 1):
            pause = None
            try:
                response = await self.client.request(method, url, headers=headers, params=params, json=json)
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
            await asyncio.sleep(pause)
        raise ValueError("HTTP attempts must be positive")
