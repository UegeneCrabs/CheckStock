import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Protocol

from app.config import settings
from app.core.constants import OZON_PROMOTIONS, HTTPStatus, OzonPromotionIssue, OzonPromotionStatus
from app.dto.ozon_promotions import (
    OzonAction,
    OzonActionProduct,
    OzonPromotionItem,
    OzonPromotions,
    OzonPromotionsIncompleteError,
)
from app.dto.ozon_related import OzonRelatedProducts
from app.ozon.api import OzonApiError


class PromotionsClient(Protocol):
    async def actions(self) -> tuple[OzonAction, ...]: ...

    async def product_id(self, article: str) -> int: ...

    async def find_product(
        self, action_id: int, product_id: int, *, participating: bool
    ) -> OzonActionProduct | None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class CachedPromotions:
    response: OzonPromotions
    expires: float


class OzonPromotionsCache:
    def __init__(self) -> None:
        self.items: OrderedDict[tuple[str, str], CachedPromotions] = OrderedDict()
        self.locks: dict[str, asyncio.Lock] = {}


class OzonPromotionsService:
    def __init__(
        self,
        client: PromotionsClient,
        catalog_reader: Callable[[], Awaitable[OzonRelatedProducts]],
        cache: OzonPromotionsCache,
        scope: str,
        clock: Callable[[], datetime],
    ):
        self.client = client
        self.catalog_reader = catalog_reader
        self.cache = cache
        self.scope = scope
        self.clock = clock

    def invalidate(self, article: str) -> None:
        self.cache.items.pop((self.scope, article), None)

    async def promotions(self, article: str, *, fresh: bool = False) -> OzonPromotions:
        catalog = await self.catalog_reader()
        if article not in {item.article for item in catalog.items}:
            raise LookupError("Product not found")
        key = (self.scope, article)
        async with self.cache.locks.setdefault(self.scope, asyncio.Lock()):
            cached = self.cache.items.get(key)
            if not fresh and cached is not None and cached.expires > monotonic():
                self.cache.items.move_to_end(key)
                return cached.response
            deadline = asyncio.get_running_loop().time() + settings.ozon_promotions_timeout_seconds
            async with asyncio.timeout_at(deadline):
                product_id = await self.client.product_id(article)
                actions = await self.client.actions()
            items: list[OzonPromotionItem] = []
            completed = 0
            try:
                async with asyncio.timeout_at(deadline):
                    for action in actions:
                        item = await self._action(action, product_id)
                        if item is not None:
                            items.append(item)
                        completed += 1
            except TimeoutError:
                items.extend(
                    OzonPromotionItem(
                        action=action, status=OzonPromotionStatus.UNKNOWN, issue=OzonPromotionIssue.TIMEOUT
                    )
                    for action in actions[completed:]
                )
            result = OzonPromotions(
                product_id=product_id,
                items=tuple(items),
                complete=all(item.issue is None for item in items),
                checked_at=self.clock(),
            )
            if result.complete:
                self.cache.items[key] = CachedPromotions(
                    response=result, expires=monotonic() + settings.ozon_promotions_cache_seconds
                )
                self.cache.items.move_to_end(key)
                while len(self.cache.items) > OZON_PROMOTIONS.cache_size:
                    self.cache.items.popitem(last=False)
            return result

    async def _action(self, action: OzonAction, product_id: int) -> OzonPromotionItem | None:
        try:
            product = await self.client.find_product(action.id, product_id, participating=True)
            if product is not None:
                return OzonPromotionItem(
                    action=action, status=OzonPromotionStatus.PARTICIPATING, product=product
                )
            product = await self.client.find_product(action.id, product_id, participating=False)
            if product is not None:
                return OzonPromotionItem(action=action, status=OzonPromotionStatus.CANDIDATE, product=product)
            return None
        except OzonPromotionsIncompleteError:
            issue = OzonPromotionIssue.INCOMPLETE
        except OzonApiError as error:
            issue = (
                OzonPromotionIssue.ACCESS_DENIED
                if error.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)
                else OzonPromotionIssue.UNAVAILABLE
            )
        except ValueError:
            issue = OzonPromotionIssue.UNAVAILABLE
        return OzonPromotionItem(action=action, status=OzonPromotionStatus.UNKNOWN, issue=issue)
