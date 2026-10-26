import asyncio
from time import monotonic

from app.config import settings
from app.core.constants import OZON_API
from app.dto.orders import OzonCredentials
from app.dto.ozon_orders import OzonProductInfoRequest
from app.dto.ozon_related import OzonModelProduct, OzonModelProducts
from app.infrastructure.async_http import AsyncHttp
from app.ozon.api import OzonApiError
from app.ozon.async_api import AsyncSellerApi


class AsyncRelatedReader:
    def __init__(self, http: AsyncHttp):
        self.http = http
        self._products: dict[str, OzonModelProducts] = {}
        self._articles: dict[str, tuple[str, ...]] = {}
        self._expires: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def load(self, credentials: OzonCredentials, articles: tuple[str, ...]) -> OzonModelProducts:
        articles = tuple(sorted(set(articles)))
        if not articles:
            return OzonModelProducts(items=())
        key = credentials.client_id
        async with self._locks.setdefault(key, asyncio.Lock()):
            if self._articles.get(key) == articles and self._expires.get(key, 0) > monotonic():
                return self._products[key]
            api = AsyncSellerApi(self.http, credentials)
            products: dict[str, OzonModelProduct] = {}
            for offset in range(0, len(articles), OZON_API.product_info_batch_size):
                batch = articles[offset : offset + OZON_API.product_info_batch_size]
                response = await api._response(
                    OZON_API.product_info_path, OzonProductInfoRequest(sku=(), offer_id=batch)
                )
                page = OzonModelProducts.model_validate(response.json())
                for item in page.items:
                    if item.offer_id not in batch or item.offer_id in products:
                        raise OzonApiError(None, "Unexpected Ozon model product")
                    products[item.offer_id] = item
            result = OzonModelProducts(items=tuple(products.values()))
            self._products[key] = result
            self._articles[key] = articles
            self._expires[key] = monotonic() + settings.ozon_related_cache_seconds
            return result
