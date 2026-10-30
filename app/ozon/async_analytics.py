import logging
from datetime import date, datetime

import httpx
from pydantic import ValidationError

from app.config import settings
from app.core.constants import (
    OZON_ANALYTICS,
    OZON_API,
    OZON_STOCK_ANALYTICS,
    HTTPMethod,
    HTTPStatus,
    OrderScheme,
    OzonAnalyticsReason,
)
from app.dto.orders import OzonArticleIndex, OzonCredentials
from app.dto.ozon_analytics import (
    OzonAnalyticsApiError,
    OzonAnalyticsApiResponse,
    OzonAnalyticsFunnelPage,
    OzonAnalyticsFunnelRequest,
    OzonAnalyticsStock,
    OzonAnalyticsStocksPage,
    OzonAnalyticsTransit,
    OzonAnalyticsTransitRow,
)
from app.infrastructure.async_http import AsyncHttp, retry_after
from app.ozon.analytics_normalizer import article_index, funnel_page
from app.ozon.api import OzonApiError
from app.ozon.async_api import AsyncSellerApi

logger = logging.getLogger(__name__)


class AsyncOzonAnalyticsClient:
    def __init__(self, http: AsyncHttp, credentials: OzonCredentials):
        if http.limits is None:
            raise ValueError(OzonAnalyticsReason.LIMITER_REQUIRED)
        self.http = http
        self.seller = AsyncSellerApi(http, credentials)
        self.headers = self.seller.headers

    async def _json(self, path: str, payload: object) -> object:
        try:
            response = await self.http.request(
                HTTPMethod.POST,
                OZON_API.base_url + path,
                headers=self.headers,
                json=payload,
                attempts=1,
                backoff=0,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise OzonAnalyticsApiError(OzonAnalyticsReason.TRANSPORT_ERROR) from error
        try:
            if len(response.content) > OZON_ANALYTICS.max_response_bytes:
                raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE)
            if response.is_error:
                body = response.text.casefold()
                reason = OzonAnalyticsReason.API_REJECTED
                if response.status_code == HTTPStatus.UNAUTHORIZED:
                    reason = OzonAnalyticsReason.CREDENTIALS_ERROR
                elif any(marker in body for marker in OZON_ANALYTICS.subscription_markers):
                    reason = OzonAnalyticsReason.SUBSCRIPTION_REQUIRED
                elif any(marker in body for marker in OZON_ANALYTICS.daily_quota_markers):
                    reason = OzonAnalyticsReason.QUOTA_DEFERRED
                elif response.status_code == HTTPStatus.FORBIDDEN:
                    reason = OzonAnalyticsReason.ACCESS_DENIED
                elif response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                    reason = OzonAnalyticsReason.RATE_LIMITED
                elif response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    reason = OzonAnalyticsReason.TRANSPORT_ERROR
                raise OzonAnalyticsApiError(
                    reason,
                    retry_seconds=retry_after(response.headers.get("Retry-After")),
                    status=response.status_code,
                )
            try:
                value: object = response.json()
                return value
            except ValueError as error:
                raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE) from error
        finally:
            await response.aclose()

    async def _transit_json(self, path: str, payload: object) -> object:
        attempts = (
            OZON_STOCK_ANALYTICS.request_attempts
            if path == OZON_STOCK_ANALYTICS.path
            else settings.ozon_request_attempts
        )
        for attempt in range(1, attempts + 1):
            try:
                return await self._json(path, payload)
            except OzonAnalyticsApiError as error:
                if (
                    error.reason
                    not in (OzonAnalyticsReason.TRANSPORT_ERROR, OzonAnalyticsReason.RATE_LIMITED)
                    or attempt == attempts
                ):
                    raise
                pause = max(
                    error.retry_seconds or 0,
                    settings.ozon_retry_backoff_seconds * attempt,
                )
                logger.warning(
                    "ozon_analytics_transit_retry path=%s attempt=%s status=%s delay=%s",
                    path,
                    attempt,
                    error.status,
                    pause,
                )
                await self.http.sleep(pause)
        raise ValueError(OzonAnalyticsReason.TRANSPORT_ERROR)

    async def funnel(
        self, source_day: date, offset: int, catalog: OzonArticleIndex
    ) -> OzonAnalyticsFunnelPage:
        request = OzonAnalyticsFunnelRequest(date_from=source_day, date_to=source_day, offset=offset)
        data = await self._json(OZON_ANALYTICS.funnel_path, request.model_dump(mode="json"))
        try:
            parsed = OzonAnalyticsApiResponse.model_validate(data)
        except ValidationError as error:
            raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE) from error
        if len(parsed.result.data) > OZON_ANALYTICS.page_size:
            raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE)
        try:
            mapping = article_index(catalog)
            missing = tuple(
                dict.fromkeys(
                    row.dimensions[0].id for row in parsed.result.data if row.dimensions[0].id not in mapping
                )
            )
            if missing:
                resolved = article_index(await self.seller.product_articles(missing))
                mapping.update(resolved)
            return funnel_page(parsed.result.data, mapping).model_copy(
                update={"source_timestamp": parsed.timestamp}
            )
        except (OzonAnalyticsApiError, OzonApiError) as error:
            reason = (
                error.reason
                if isinstance(error, OzonAnalyticsApiError)
                else OzonAnalyticsReason.CREDENTIALS_ERROR
                if error.status == HTTPStatus.UNAUTHORIZED
                else OzonAnalyticsReason.MAPPING_CONFLICT
            )
            raise OzonAnalyticsApiError(reason, raw_payload=parsed.model_dump_json()) from error

    async def transit(self, store: str, now: datetime) -> OzonAnalyticsTransit:
        mapping: dict[str, str] = {}
        seen_products: set[int] = set()
        seen_cursors: set[str] = set()
        cursor = ""
        while True:
            data = await self._transit_json(
                OZON_ANALYTICS.product_stocks_path,
                {
                    "cursor": cursor,
                    "limit": OZON_ANALYTICS.page_size,
                    "filter": {"visibility": OZON_ANALYTICS.all_visibility},
                },
            )
            try:
                page = OzonAnalyticsStocksPage.model_validate(data)
            except ValidationError as error:
                raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE) from error
            for product in page.items:
                if product.product_id in seen_products:
                    raise OzonAnalyticsApiError(OzonAnalyticsReason.REPEATED_PAGE)
                seen_products.add(product.product_id)
                for stock in product.stocks:
                    if stock.type.casefold() == OrderScheme.FBO:
                        sku = str(stock.sku)
                        if sku in mapping and mapping[sku] != product.offer_id:
                            raise OzonAnalyticsApiError(OzonAnalyticsReason.MAPPING_CONFLICT)
                        mapping[sku] = product.offer_id
            if len(seen_products) > OZON_ANALYTICS.max_source_rows:
                raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE)
            expected = page.total_items if page.total_items is not None else page.total
            if expected is not None and len(seen_products) >= expected:
                if len(seen_products) != expected:
                    raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE)
                break
            if not page.cursor:
                if expected is not None and len(seen_products) != expected:
                    raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE)
                break
            if page.cursor in seen_cursors or not page.items:
                raise OzonAnalyticsApiError(OzonAnalyticsReason.REPEATED_PAGE)
            seen_cursors.add(page.cursor)
            cursor = page.cursor
        quantities: dict[str, int] = {}
        seen: set[tuple[str, str]] = set()
        skus = tuple(sorted(mapping))
        for offset in range(0, len(skus), OZON_STOCK_ANALYTICS.sku_batch_size):
            requested = skus[offset : offset + OZON_STOCK_ANALYTICS.sku_batch_size]
            data = await self._transit_json(OZON_STOCK_ANALYTICS.path, {"skus": requested})
            if not isinstance(data, dict):
                raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE)
            nested = data.get("result", data)
            if not isinstance(nested, dict) or not isinstance(nested.get("items"), list):
                raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE)
            for value in nested["items"]:
                try:
                    row = OzonAnalyticsTransitRow.model_validate(value)
                except ValidationError as error:
                    raise OzonAnalyticsApiError(OzonAnalyticsReason.MALFORMED_SOURCE) from error
                sku = str(row.sku)
                if sku not in requested or mapping.get(sku) != row.offer_id:
                    raise OzonAnalyticsApiError(OzonAnalyticsReason.MAPPING_CONFLICT)
                if OZON_STOCK_ANALYTICS.fbs_return_tag in row.item_tags:
                    continue
                key = sku, str(row.warehouse_id)
                if key in seen:
                    raise OzonAnalyticsApiError(OzonAnalyticsReason.REPEATED_PAGE)
                seen.add(key)
                quantities[row.offer_id] = (
                    quantities.get(row.offer_id, 0) + row.return_from_customer_stock_count
                )
        return OzonAnalyticsTransit(
            store_slug=store,
            observed_at=now,
            products=tuple(
                OzonAnalyticsStock(article=article, from_customer=value)
                for article, value in quantities.items()
            ),
        )
