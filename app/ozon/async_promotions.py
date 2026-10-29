import httpx

from app.config import settings
from app.core.constants import OZON_API, OZON_PROMOTIONS, HTTPMethod, OzonPriceSource
from app.dto.orders import OzonCredentials
from app.dto.ozon_orders import OzonProductInfoRequest
from app.dto.ozon_prices import SellerPriceItem, SellerPricePage
from app.dto.ozon_promotion_entry import (
    OzonEntryIdentities,
    OzonEntryIdentity,
    OzonEntryPriceFilter,
    OzonEntryPriceRequest,
    OzonEntryReceipt,
    OzonEntryUpdate,
)
from app.dto.ozon_promotions import (
    OzonAction,
    OzonActionProduct,
    OzonActionProductsPage,
    OzonActionProductsRequest,
    OzonActionsResponse,
    OzonPromotionProductIdentities,
    OzonPromotionsIncompleteError,
)
from app.infrastructure.async_http import AsyncHttp
from app.ozon.api import OzonApiError, _parse_error_body
from app.ozon.async_api import AsyncSellerApi


class AsyncPromotionsClient:
    def __init__(self, http: AsyncHttp, credentials: OzonCredentials):
        self.http = http
        self.api = AsyncSellerApi(http, credentials)

    async def identity(self, article: str) -> OzonEntryIdentity:
        response = await self.api._response(
            OZON_API.product_info_path, OzonProductInfoRequest(sku=(), offer_id=(article,))
        )
        items = OzonEntryIdentities.model_validate(response.json()).items
        if len(items) != 1 or items[0].offer_id != article:
            raise ValueError("Unexpected product identity")
        return items[0]

    async def price(self, article: str, product_id: int) -> SellerPriceItem:
        response = await self.api._response(
            OzonPriceSource.SELLER,
            OzonEntryPriceRequest(filter=OzonEntryPriceFilter(offer_id=(article,))),
        )
        page = SellerPricePage.model_validate(response.json())
        if (
            len(page.items) != 1
            or page.items[0].offer_id != article
            or page.items[0].product_id != product_id
        ):
            raise ValueError("Unexpected price identity")
        return page.items[0]

    async def update(self, payload: OzonEntryUpdate) -> OzonEntryReceipt:
        response = await self.http.request(
            HTTPMethod.POST,
            OZON_API.base_url + OZON_PROMOTIONS.update_path,
            headers=self.api.headers,
            json=payload.model_dump(mode="json", exclude_none=True),
            attempts=OZON_PROMOTIONS.entry_write_attempts,
            backoff=settings.ozon_retry_backoff_seconds,
        )
        if response.is_error:
            raise OzonApiError(response.status_code, _parse_error_body(response.text))
        receipt = OzonEntryReceipt.model_validate(response.json())
        active, deactivated = set(receipt.active_product_ids), set(receipt.deactivated_product_ids)
        rejected = {item.product_id for item in receipt.rejected}
        expected = {item.product_id for item in payload.products}
        if (
            active & deactivated
            or active & rejected
            or deactivated & rejected
            or len(active) != len(receipt.active_product_ids)
            or len(deactivated) != len(receipt.deactivated_product_ids)
            or len(rejected) != len(receipt.rejected)
            or active | deactivated | rejected != expected
            or any(item.product_id not in expected for item in receipt.warnings)
        ):
            raise ValueError("Inconsistent promotion receipt")
        return receipt

    async def actions(self) -> tuple[OzonAction, ...]:
        try:
            response = await self.http.request(
                HTTPMethod.GET,
                OZON_API.base_url + OZON_PROMOTIONS.actions_path,
                headers=self.api.headers,
                attempts=settings.ozon_request_attempts,
                backoff=settings.ozon_retry_backoff_seconds,
            )
        except httpx.TransportError as error:
            raise OzonApiError(None, type(error).__name__) from None
        if response.is_error:
            raise OzonApiError(response.status_code, _parse_error_body(response.text))
        result = OzonActionsResponse.model_validate(response.json())
        if len({action.id for action in result.result}) != len(result.result):
            raise OzonPromotionsIncompleteError("Duplicate action identifiers")
        return result.result

    async def product_id(self, article: str) -> int:
        response = await self.api._response(
            OZON_API.product_info_path, OzonProductInfoRequest(sku=(), offer_id=(article,))
        )
        products = OzonPromotionProductIdentities.model_validate(response.json()).items
        if not products:
            raise LookupError("Product not found")
        if len(products) != 1 or products[0].offer_id != article:
            raise ValueError("Unexpected promotion product identity")
        return products[0].id

    async def find_product(
        self, action_id: int, product_id: int, *, participating: bool
    ) -> OzonActionProduct | None:
        path = OZON_PROMOTIONS.products_path if participating else OZON_PROMOTIONS.candidates_path
        cursor = ""
        seen_cursors: set[str] = set()
        seen_products: set[int] = set()
        expected_total: int | None = None
        for _ in range(OZON_PROMOTIONS.max_pages):
            response = await self.api._response(
                path, OzonActionProductsRequest(action_id=action_id, last_id=cursor)
            )
            page = OzonActionProductsPage.model_validate(response.json())
            if expected_total is not None and page.total != expected_total:
                raise OzonPromotionsIncompleteError("Promotion products changed during pagination")
            expected_total = page.total
            for product in page.products:
                if product.id in seen_products:
                    raise OzonPromotionsIncompleteError("Duplicate promotion product")
                seen_products.add(product.id)
                if product.id == product_id:
                    return product
            if len(seen_products) == page.total:
                return None
            if len(seen_products) > page.total or not page.products:
                raise OzonPromotionsIncompleteError("Inconsistent promotion total")
            if not page.last_id or page.last_id in seen_cursors:
                raise OzonPromotionsIncompleteError("Missing or repeated promotion cursor")
            seen_cursors.add(page.last_id)
            cursor = page.last_id
        raise OzonPromotionsIncompleteError("Promotion pagination limit reached")
