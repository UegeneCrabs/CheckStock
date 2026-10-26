from collections.abc import Awaitable, Callable

from app.dto.ozon_related import OzonModelProducts, OzonRelatedProducts


class OzonRelatedService:
    def __init__(
        self,
        catalog_reader: Callable[[], Awaitable[OzonRelatedProducts]],
        model_reader: Callable[[tuple[str, ...]], Awaitable[OzonModelProducts]],
    ):
        self.catalog_reader = catalog_reader
        self.model_reader = model_reader

    async def related(self, article: str) -> OzonRelatedProducts:
        catalog = await self.catalog_reader()
        if article not in {item.article for item in catalog.items}:
            raise LookupError("Product not found")
        models = await self.model_reader(tuple(item.article for item in catalog.items))
        by_article = {item.offer_id: item.model_info for item in models.items}
        model = by_article.get(article)
        if model is None:
            raise ValueError("Product model unavailable")
        return OzonRelatedProducts(
            complete=model.model_id == 0
            or all(by_article.get(item.article) is not None for item in catalog.items),
            items=tuple(
                item
                for item in catalog.items
                if item.article != article
                and model.model_id > 0
                and (other := by_article.get(item.article)) is not None
                and other.model_id == model.model_id
            ),
        )
