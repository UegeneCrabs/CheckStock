import json
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    field_validator,
    model_validator,
)

from app.core.constants import (
    ORDER_NORMALIZATION,
    TIME,
    OrderScheme,
    SortDirection,
)
from app.dto.common import DtoModel
from app.dto.orders import OzonArticleIndex, OzonOrderPosting, OzonOrderProduct


class OzonPayloadError(ValueError):
    def __init__(self, path: str, reason: str):
        super().__init__(f"{path}: {reason}")


def identifier(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("expected a string or integer identifier")
    return str(value) if value != 0 else ""


def optional_date(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=TIME.moscow_timezone)
    if not isinstance(value, str):
        raise ValueError("expected a date string")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ORDER_NORMALIZATION.date_formats:
            try:
                parsed = datetime.strptime(raw, pattern)
                break
            except ValueError:
                continue
        else:
            raise ValueError("invalid order date") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=TIME.moscow_timezone)


def quantity(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("expected a positive integer quantity")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid quantity") from None
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ValueError("expected a positive integer quantity")
    return int(number)


Identifier = Annotated[str, BeforeValidator(identifier)]
OptionalDate = Annotated[datetime | None, BeforeValidator(optional_date)]
Quantity = Annotated[int, BeforeValidator(quantity)]


class OzonPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class OzonAmount(OzonPayload):
    value: str | StrictFloat | None = None
    amount: str | StrictFloat | None = None


def price_input(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("expected a numeric price, not a boolean")
    return value


Price = Annotated[str | float | OzonAmount | None, BeforeValidator(price_input)]


def price_value(value: Price) -> float | None:
    if isinstance(value, OzonAmount):
        value = value.value if value.value is not None else value.amount
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


class OzonProduct(OzonPayload):
    sku: Identifier = ""
    product_id: Identifier = ""
    offer_id: Identifier = ""
    quantity: Quantity
    price: Price = None
    name: str | None = None
    barcode: str | None = None
    currency_code: str | None = None


class OzonFinancialProduct(OzonPayload):
    product_id: Identifier = ""
    sku: Identifier = ""
    offer_id: Identifier = ""
    price: Price = None
    old_price: Price = None


class OzonFinancialData(OzonPayload):
    products: tuple[OzonFinancialProduct, ...] | None = None


class OzonAnalytics(OzonPayload):
    delivery_date_end: OptionalDate = None


class OzonCancellation(OzonPayload):
    cancel_reason_id: str | int | None = None


class OzonPostingDates(OzonPayload):
    created_at: OptionalDate = None
    in_process_at: OptionalDate = None
    shipment_date: OptionalDate = None

    def filter_day(self, scheme: OrderScheme, fallback: date) -> date:
        value = self.in_process_at if scheme == OrderScheme.FBO else self.created_at
        value = value or self.created_at or self.in_process_at or self.shipment_date
        return value.astimezone(TIME.moscow_timezone).date() if value else fallback


class OzonPosting(OzonPostingDates):
    posting_number: Identifier = ""
    order_number: Identifier = ""
    order_id: Identifier = ""
    status: str
    substatus: str | int | None = None
    updated_at: OptionalDate = None
    status_updated_at: OptionalDate = None
    delivering_date: OptionalDate = None
    cancellation: OzonCancellation | None = None
    analytics_data: OzonAnalytics | None = None
    financial_data: OzonFinancialData | None = None
    financial_products: tuple[OzonFinancialProduct, ...] | None = None
    products: tuple[OzonProduct, ...] | None = None
    items: tuple[OzonProduct, ...] | None = None
    raw_json: str = Field(exclude=True)

    @field_validator("status")
    @classmethod
    def nonempty_status(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("missing order status")
        return value.strip().lower()

    @model_validator(mode="before")
    @classmethod
    def keep_original(cls, value: object) -> object:
        if isinstance(value, dict):
            return {**value, "raw_json": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}
        return value

    def prepare(
        self, scheme: OrderScheme, *, path: str, catalog: OzonArticleIndex | None = None
    ) -> OzonOrderPosting:
        key = self.posting_number or self.order_number or self.order_id
        if not key.strip() or (scheme == OrderScheme.FBS and not self.posting_number.strip()):
            raise OzonPayloadError(path + ".posting_number", "missing posting identifier")
        ordered = self.created_at or self.in_process_at or self.shipment_date
        if ordered is None:
            raise OzonPayloadError(path + ".created_at", "missing order date")
        updated = self.updated_at or self.status_updated_at or ordered
        sold = (
            self.delivering_date
            or (self.analytics_data.delivery_date_end if self.analytics_data else None)
            or self.shipment_date
            or updated
        )
        financial_rows = (
            (self.financial_data.products if self.financial_data else ()) or self.financial_products or ()
        )
        finances = {row.product_id or row.sku or row.offer_id: row for row in financial_rows}
        products = self.products or self.items or ()
        if not products:
            raise OzonPayloadError(path + ".products", "missing products")
        prepared: list[OzonOrderProduct] = []
        for index, product in enumerate(products):
            product_path = f"{path}.products.{index}"
            product_key = product.sku or product.product_id or product.offer_id
            if not product_key.strip():
                raise OzonPayloadError(product_path + ".sku", "missing product identifier")
            financial = finances.get(product_key)
            price = price_value(product.price)
            if price is None and financial:
                price = price_value(financial.price)
                if price is None:
                    price = price_value(financial.old_price)
            if price is None:
                raise OzonPayloadError(product_path + ".price", "no valid price in any source")
            article = product.offer_id
            if not article.strip():
                candidates = catalog.by_sku.get(product.sku, frozenset()) if catalog else frozenset()
                if len(candidates) != 1:
                    raise OzonPayloadError(
                        product_path + ".offer_id", "missing offer_id and no unique catalog article for SKU"
                    )
                article = next(iter(candidates))
            prepared.append(
                OzonOrderProduct(
                    key=product_key,
                    article=article,
                    barcode=product.barcode or "",
                    name=product.name or "",
                    quantity=product.quantity,
                    unit_price=price,
                    currency=product.currency_code or ORDER_NORMALIZATION.default_currency,
                )
            )
        status = self.status
        cancelled = ORDER_NORMALIZATION.cancellation_status_marker in status or bool(
            self.cancellation and self.cancellation.cancel_reason_id
        )
        return OzonOrderPosting(
            key=key,
            external_order_id=self.order_number or self.order_id or key,
            status=status,
            substatus=str(self.substatus or ""),
            cancelled=cancelled,
            delivered=status in ORDER_NORMALIZATION.delivered_statuses,
            ordered_at=ordered.astimezone(TIME.moscow_timezone).isoformat(timespec="seconds"),
            updated_at=updated.astimezone(TIME.moscow_timezone).isoformat(timespec="seconds"),
            sold_at=sold.astimezone(TIME.moscow_timezone).isoformat(timespec="seconds"),
            products=tuple(prepared),
            raw_json=self.raw_json,
        )


class OzonPostingsPage(OzonPayload):
    postings: tuple[OzonPosting, ...]
    has_next: StrictBool | None = None
    cursor: str | None = None


class OzonProductSource(OzonPayload):
    sku: Identifier = ""


class OzonProductInfo(OzonPayload):
    offer_id: Identifier = Field(min_length=1)
    sku: Identifier = ""
    sources: tuple[OzonProductSource, ...] = ()


class OzonProductInfoPage(OzonPayload):
    items: tuple[OzonProductInfo, ...]


class OzonProductInfoRequest(DtoModel):
    sku: tuple[str, ...]
    offer_id: tuple[str, ...] = ()
    product_id: tuple[int, ...] = ()


class PostingFilter(DtoModel):
    since: str
    to: str
    posting_numbers: tuple[str, ...] | None = None
    order_numbers: tuple[str, ...] | None = None


class PostingOptions(DtoModel):
    analytics_data: bool
    barcodes: bool
    financial_data: bool
    translit: bool = False


class PostingsRequest(DtoModel):
    dir: Literal[SortDirection.ASC] = SortDirection.ASC
    filter: PostingFilter
    limit: int = Field(gt=0)
    options: PostingOptions = Field(serialization_alias="with")


class FboPostingsRequest(PostingsRequest):
    cursor: str | None = None


class FbsPostingsRequest(PostingsRequest):
    cursor: str | None = None
