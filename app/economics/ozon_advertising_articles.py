import logging

from app.dto.orders import OzonArticleIndex
from app.dto.ozon_advertising import AdvertisingSnapshot
from app.ozon.advertising_normalizer import attach_articles
from app.ozon.api import OzonApiError
from app.ozon.async_api import AsyncSellerApi

logger = logging.getLogger(__name__)


class AdvertisingArticleResolver:
    def __init__(self, store: str, index: OzonArticleIndex, seller: AsyncSellerApi | None):
        self.store = store
        self.seller = seller
        self.articles = {sku: next(iter(values)) for sku, values in index.by_sku.items() if len(values) == 1}
        self.looked_up: set[str] = set()

    async def attach(self, snapshot: AdvertisingSnapshot) -> AdvertisingSnapshot:
        if snapshot.store_slug != self.store:
            raise ValueError("Advertising snapshot belongs to another store")
        missing = tuple(
            sorted(
                {
                    fact.advertised_sku
                    for fact in snapshot.facts
                    if fact.advertised_sku
                    and fact.advertised_sku not in self.articles
                    and fact.advertised_sku not in self.looked_up
                }
            )
        )
        if missing and self.seller is not None:
            self.looked_up.update(missing)
            try:
                index = await self.seller.product_articles(missing)
            except OzonApiError:
                logger.warning(
                    "ozon_advertising_article_lookup_failed store=%s skus=%s", self.store, len(missing)
                )
            else:
                self.articles.update(
                    (sku, next(iter(values))) for sku, values in index.by_sku.items() if len(values) == 1
                )
        return attach_articles(snapshot, self.articles)
