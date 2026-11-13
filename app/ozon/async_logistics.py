import asyncio
from datetime import datetime, time, timedelta

from app.config import settings
from app.core.constants import OZON_LOGISTICS, TIME, OrderScheme
from app.core.logging_config import tagged_logger
from app.dto.common import DtoModel
from app.dto.ozon_logistics import (
    AccrualsPage,
    AccrualsRequest,
    AccrualTypesPage,
    AttributesFilter,
    AttributesPage,
    AttributesRequest,
    LogisticsDimensions,
    LogisticsEvidence,
    LogisticsPeriod,
    LogisticsReturn,
    PostingAccruals,
    ReturnsFilter,
    ReturnsPage,
    ReturnsRequest,
)
from app.ozon.async_api import AsyncSellerApi

logger = tagged_logger(__name__, "ozon", "async_logistics")


class AsyncLogisticsClient(AsyncSellerApi):
    async def dimensions(self, articles: tuple[str, ...]) -> tuple[LogisticsDimensions, ...]:
        page = AttributesPage.model_validate(
            (
                await self._response(
                    OZON_LOGISTICS.attributes_path,
                    AttributesRequest(filter=AttributesFilter(offer_id=articles)),
                )
            ).json()
        )
        return page.result

    async def returns(self, numbers: tuple[str, ...], filters: ReturnsFilter) -> tuple[LogisticsReturn, ...]:
        returns: list[LogisticsReturn] = []
        seen: set[int] = set()
        last_id = 0
        selected = frozenset(numbers)
        if numbers:
            while True:
                page_returns = ReturnsPage.model_validate(
                    (
                        await self._response(
                            OZON_LOGISTICS.returns_path, ReturnsRequest(filter=filters, last_id=last_id)
                        )
                    ).json()
                )
                for item in page_returns.returns:
                    if item.id in seen:
                        raise ValueError("Repeated Ozon return")
                    seen.add(item.id)
                    if item.posting_number in selected:
                        returns.append(item)
                if len(seen) > OZON_LOGISTICS.max_returns:
                    raise ValueError("Ozon returns traversal limit exceeded")
                if not page_returns.has_next:
                    break
                if not page_returns.returns or page_returns.returns[-1].id <= last_id:
                    raise ValueError("Incomplete Ozon returns pagination")
                last_id = page_returns.returns[-1].id
                await asyncio.sleep(settings.ozon_logistics_request_interval_seconds)
        return tuple(returns)

    async def evidence(self, period: LogisticsPeriod, articles: tuple[str, ...]) -> LogisticsEvidence:
        since = datetime.combine(period.start, time.min, TIME.moscow_timezone)
        until = datetime.combine(period.day, time.min, TIME.moscow_timezone) - timedelta(microseconds=1)
        postings = await self.postings(OrderScheme.FBO, since.isoformat(), until.isoformat())
        types = AccrualTypesPage.model_validate(
            (await self._response(OZON_LOGISTICS.types_path, DtoModel())).json()
        )
        numbers = tuple(item.posting_number for item in postings)
        accruals: list[PostingAccruals] = []
        for offset in range(0, len(numbers), OZON_LOGISTICS.accrual_batch_size):
            batch = numbers[offset : offset + OZON_LOGISTICS.accrual_batch_size]
            page = AccrualsPage.model_validate(
                (
                    await self._response(OZON_LOGISTICS.accrual_path, AccrualsRequest(posting_numbers=batch))
                ).json()
            )
            returned = tuple(item.posting_number for item in page.posting_accruals)
            if len(set(returned)) != len(returned) or set(returned) != set(batch):
                raise ValueError("Incomplete or repeated posting accruals")
            accruals.extend(page.posting_accruals)
        returns = await self.returns(numbers, ReturnsFilter())
        dimensions: list[LogisticsDimensions] = []
        dimensions_available = True
        requested_articles = tuple(
            sorted(
                set(articles)
                | {
                    product.offer_id
                    for posting in postings
                    for product in (posting.products or ())
                    if product.offer_id
                }
            )
        )
        try:
            for offset in range(0, len(requested_articles), OZON_LOGISTICS.attributes_batch_size):
                batch = requested_articles[offset : offset + OZON_LOGISTICS.attributes_batch_size]
                dimensions.extend(await self.dimensions(batch))
        except Exception as error:
            logger.warning("ozon_logistics_dimensions_failed error=%s", type(error).__name__)
            dimensions_available = False
        return LogisticsEvidence(
            period=period,
            postings=postings,
            accruals=tuple(accruals),
            returns=tuple(returns),
            types=types.accrual_types,
            dimensions=tuple(dimensions),
            dimensions_available=dimensions_available,
        )
