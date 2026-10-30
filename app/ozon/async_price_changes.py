from app.config import settings
from app.core.constants import OZON_API, OZON_PRICE_CHANGES, HTTPMethod
from app.dto.orders import OzonCredentials
from app.dto.ozon_price_changes import OzonPriceChangePayload, OzonPriceChangeReceipt
from app.dto.ozon_prices import SellerPriceItem
from app.dto.ozon_promotion_entry import OzonEntryIdentity
from app.infrastructure.async_http import AsyncHttp
from app.ozon.api import OzonApiError
from app.ozon.async_promotions import AsyncPromotionsClient


class AsyncPriceChangeClient:
    def __init__(self, http: AsyncHttp, credentials: OzonCredentials):
        self.http = http
        self.reader = AsyncPromotionsClient(http, credentials)

    async def identity(self, article: str) -> OzonEntryIdentity:
        return await self.reader.identity(article)

    async def price(self, article: str, product_id: int) -> SellerPriceItem:
        return await self.reader.price(article, product_id)

    async def update(self, payload: OzonPriceChangePayload) -> OzonPriceChangeReceipt:
        response = await self.http.request(
            HTTPMethod.POST,
            OZON_API.base_url + OZON_PRICE_CHANGES.update_path,
            headers=self.reader.api.headers,
            json=payload.model_dump(mode="json"),
            attempts=OZON_PRICE_CHANGES.write_attempts,
            backoff=settings.ozon_retry_backoff_seconds,
        )
        if response.is_error:
            raise OzonApiError(response.status_code, f"Ozon price change HTTP {response.status_code}")
        receipt = OzonPriceChangeReceipt.model_validate(response.json())
        if len(receipt.result) != 1 or receipt.result[0].product_id != payload.prices[0].product_id:
            raise ValueError("Unexpected price change receipt")
        return receipt
