import asyncio
from dataclasses import dataclass
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
    OzonProductCategory,
    OzonProductCategorySelection,
)
from app.infrastructure.async_http import AsyncHttp
from app.ozon.api import OzonApiError
from app.ozon.async_api import AsyncSellerApi
from app.ozon.tariff_normalizer import category_identities


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonCategoryCacheEntry:
    product: OzonProductCategorySelection | None
    expires_at: float


class AsyncCategoryReader:
    def __init__(self, http: AsyncHttp) -> None:
        self.http = http
        self._trees: dict[str, tuple[OzonCategoryIdentity, ...]] = {}
        self._expires: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._products: dict[str, dict[str, OzonCategoryCacheEntry]] = {}
        self._product_locks: dict[str, asyncio.Lock] = {}

    async def product(self, credentials: OzonCredentials, article: str) -> OzonCategorySelection:
        products = await self.products(credentials, (article,), refresh=True)
        if len(products) != 1:
            raise OzonApiError(None, "Product category unavailable")
        return OzonCategorySelection.model_validate(products[0].model_dump(exclude={"article"}))

    async def products(
        self, credentials: OzonCredentials, articles: tuple[str, ...], *, refresh: bool = False
    ) -> tuple[OzonProductCategorySelection, ...]:
        articles = tuple(sorted({article for article in articles if article}))
        if not articles:
            return ()
        key = credentials.client_id
        async with self._product_locks.setdefault(key, asyncio.Lock()):
            cached = self._products.setdefault(key, {})
            now = monotonic()
            pending = tuple(
                article
                for article in articles
                if refresh or (entry := cached.get(article)) is None or entry.expires_at <= now
            )
            if pending:
                products = await self._load(credentials, pending)
                by_article = {item.article: item for item in products}
                expires = monotonic() + settings.ozon_category_cache_seconds
                for article in pending:
                    product = by_article.get(article)
                    cached[article] = OzonCategoryCacheEntry(
                        product=product,
                        expires_at=expires if product is not None and product.type_name else 0,
                    )
            return tuple(item for article in articles if (item := cached[article].product) is not None)

    async def _load(
        self, credentials: OzonCredentials, articles: tuple[str, ...]
    ) -> tuple[OzonProductCategorySelection, ...]:
        api = AsyncSellerApi(self.http, credentials)
        products: list[OzonProductCategory] = []
        for offset in range(0, len(articles), OZON_API.product_info_batch_size):
            batch = articles[offset : offset + OZON_API.product_info_batch_size]
            response = await api._response(
                OZON_API.product_info_path, OzonProductInfoRequest(sku=(), offer_id=batch)
            )
            page = OzonProductCategories.model_validate(response.json())
            if any(item.offer_id not in batch for item in page.items) or len(
                {item.offer_id for item in page.items}
            ) != len(page.items):
                raise OzonApiError(None, "Unexpected Ozon category product")
            products.extend(page.items)
        if not products:
            return ()
        key = credentials.client_id
        async with self._locks.setdefault(key, asyncio.Lock()):
            if self._expires.get(key, 0) <= monotonic():
                response = await api._response(
                    OZON_TARIFFS.category_tree_path, OzonCategoryTreeRequest(language=OZON_TARIFFS.language)
                )
                tree = OzonCategoryTree.model_validate(response.json())
                self._trees[key] = category_identities(tree.result)
                self._expires[key] = monotonic() + settings.ozon_category_cache_seconds
            identities = {(item.category_id, item.type_id): item for item in self._trees[key]}
        received_at = datetime.now(UTC)
        result: list[OzonProductCategorySelection] = []
        for product in products:
            identity = identities.get((product.description_category_id, product.type_id))
            result.append(
                OzonProductCategorySelection(
                    article=product.offer_id,
                    category_id=product.description_category_id,
                    type_id=product.type_id,
                    category_name=identity.category_name if identity else "",
                    type_name=identity.type_name if identity else "",
                    received_at=received_at,
                )
            )
        return tuple(result)
