from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from app.config import settings
from app.core.constants import OZON_API, OZON_PRICES, HTTPMethod, OzonPriceSource
from app.dto.orders import OzonCredentials
from app.dto.ozon_prices import (
    ActionPricesReceived,
    ActionProductPage,
    ActionsResponse,
    PriceApiModel,
    SellerPricePage,
    SellerPricesReceived,
)
from app.infrastructure.async_http import AsyncHttp
from app.ozon.api import OzonApiError


class AsyncPricesClient:
    def __init__(self, http: AsyncHttp, credentials: OzonCredentials):
        self.http = http
        self.headers = {"Client-Id": credentials.client_id, "Api-Key": credentials.api_key}

    async def request[T: PriceApiModel](
        self, path: str, response_type: type[T], payload: dict[str, object] | None = None
    ) -> T:
        try:
            response = await self.http.request(
                HTTPMethod.GET if payload is None else HTTPMethod.POST,
                OZON_API.base_url + path,
                headers=self.headers,
                json=payload,
                attempts=settings.ozon_request_attempts,
                backoff=settings.ozon_retry_backoff_seconds,
            )
        except httpx.TransportError as error:
            raise OzonApiError(None, f"Ozon prices: {type(error).__name__}") from None
        if response.is_error:
            raise OzonApiError(response.status_code, f"Ozon prices HTTP {response.status_code}")
        try:
            return response_type.model_validate(response.json())
        except (ValidationError, ValueError):
            raise OzonApiError(None, f"Invalid Ozon prices response: {path}") from None

    async def seller_pages(self) -> AsyncIterator[SellerPricesReceived]:
        cursor = ""
        seen: set[str] = set()
        for _ in range(OZON_PRICES.max_pages):
            page = await self.request(
                OzonPriceSource.SELLER,
                SellerPricePage,
                {
                    "cursor": cursor,
                    "filter": {"visibility": OZON_PRICES.visibility},
                    "limit": OZON_PRICES.page_size,
                },
            )
            if not page.items:
                return
            yield SellerPricesReceived(items=page.items, received_at=datetime.now(UTC))
            if not page.cursor:
                if len(page.items) >= OZON_PRICES.page_size:
                    raise OzonApiError(None, "Ozon seller prices: missing cursor")
                return
            if page.cursor in seen:
                raise OzonApiError(None, "Ozon seller prices: repeated cursor")
            seen.add(page.cursor)
            cursor = page.cursor
        raise OzonApiError(None, "Ozon seller prices: page limit exceeded")

    async def action_ids(self) -> tuple[int, ...]:
        response = await self.request(OZON_PRICES.actions_path, ActionsResponse)
        return tuple(sorted({item.id for item in response.result}))

    async def action_pages(
        self, action_id: int, source: OzonPriceSource
    ) -> AsyncIterator[ActionPricesReceived]:
        if source not in (OzonPriceSource.PRODUCTS, OzonPriceSource.CANDIDATES):
            raise ValueError("An action products source is required")
        last_id = ""
        seen: set[str] = set()
        for _ in range(OZON_PRICES.max_pages):
            page = await self.request(
                source,
                ActionProductPage,
                {
                    "action_id": action_id,
                    "last_id": last_id,
                    "limit": OZON_PRICES.action_page_size,
                },
            )
            if not page.products:
                return
            yield ActionPricesReceived(products=page.products, received_at=datetime.now(UTC))
            if not page.last_id:
                if len(page.products) >= OZON_PRICES.action_page_size:
                    raise OzonApiError(None, "Ozon action prices: missing last_id")
                return
            if page.last_id in seen:
                raise OzonApiError(None, "Ozon action prices: repeated last_id")
            seen.add(page.last_id)
            last_id = page.last_id
        raise OzonApiError(None, "Ozon action prices: page limit exceeded")
