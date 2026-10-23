import asyncio
from datetime import UTC, datetime
from time import monotonic

from app.config import settings
from app.core.constants import OZON_API, OZON_TARIFFS
from app.dto.orders import OzonCredentials
from app.dto.ozon_orders import OzonProductInfoRequest
from app.dto.ozon_tariffs import (
    OzonCategoryIdentity,
    OzonCategorySelection,
    OzonCategoryTree,
    OzonCategoryTreeRequest,
    OzonProductCategories,
)
from app.infrastructure.async_http import AsyncHttp
from app.ozon.api import OzonApiError
from app.ozon.async_api import AsyncSellerApi
from app.ozon.tariff_normalizer import category_identities


class AsyncCategoryReader:
    def __init__(self, http: AsyncHttp) -> None:
        self.http = http
        self._trees: dict[str, tuple[OzonCategoryIdentity, ...]] = {}
        self._expires: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def product(self, credentials: OzonCredentials, article: str) -> OzonCategorySelection:
        api = AsyncSellerApi(self.http, credentials)
        response = await api._response(
            OZON_API.product_info_path, OzonProductInfoRequest(sku=(), offer_id=(article,))
        )
        products = OzonProductCategories.model_validate(response.json())
        matching = [item for item in products.items if item.offer_id == article]
        if len(matching) != 1:
            raise OzonApiError(None, "Product category unavailable")
        product = matching[0]
        key = credentials.client_id
        async with self._locks.setdefault(key, asyncio.Lock()):
            if self._expires.get(key, 0) <= monotonic():
                response = await api._response(
                    OZON_TARIFFS.category_tree_path, OzonCategoryTreeRequest(language=OZON_TARIFFS.language)
                )
                tree = OzonCategoryTree.model_validate(response.json())
                self._trees[key] = category_identities(tree.result)
                self._expires[key] = monotonic() + settings.ozon_category_cache_seconds
            identities = self._trees[key]
        identity = next(
            (
                item
                for item in identities
                if item.category_id == product.description_category_id and item.type_id == product.type_id
            ),
            None,
        )
        return OzonCategorySelection(
            category_id=product.description_category_id,
            type_id=product.type_id,
            category_name=identity.category_name if identity else "",
            type_name=identity.type_name if identity else "",
            received_at=datetime.now(UTC),
        )
