from datetime import datetime

from pydantic import Field, PositiveInt, RootModel, field_validator

from app.dto.common import DtoModel
from app.dto.marketplace import Marketplace


class PositiveStockEntry(DtoModel):
    code: str = Field(min_length=1, max_length=200)
    quantity: PositiveInt


class PositiveStockEntries(RootModel[tuple[PositiveStockEntry, ...]]):
    root: tuple[PositiveStockEntry, ...] = Field(min_length=1)


class SignedStockEntry(DtoModel):
    code: str = Field(min_length=1, max_length=200)
    quantity: int

    @field_validator("quantity")
    @classmethod
    def quantity_must_not_be_zero(cls, value: int) -> int:
        if value == 0:
            raise ValueError("quantity must not be zero")
        return value


class SignedStockEntries(RootModel[tuple[SignedStockEntry, ...]]):
    root: tuple[SignedStockEntry, ...] = Field(min_length=1)


class TransferStockCommand(DtoModel):
    store_slug: str
    entries: SignedStockEntries
    from_fulfillment: str = Field(min_length=1, max_length=200)
    from_marketplace: Marketplace
    to_fulfillment: str = Field(min_length=1, max_length=200)
    to_marketplace: Marketplace
    user_id: PositiveInt | None = None
    user_name: str


class ShipmentCommand(DtoModel):
    store_slug: str
    entries: SignedStockEntries
    fulfillment: str = Field(min_length=1, max_length=200)
    marketplace: Marketplace
    to_trash: bool = False


class StockMovementItem(DtoModel):
    article: str
    name: str
    barcode: str
    quantity: int
    reason: str | None = None


class StockMovementItems(RootModel[tuple[StockMovementItem, ...]]):
    root: tuple[StockMovementItem, ...] = ()


class TransferResult(DtoModel):
    moved: StockMovementItems
    skipped: StockMovementItems


class ResolvedStockEntry(DtoModel):
    article: str
    quantity: int
    name: str
    barcode: str


class ResolvedStockEntries(RootModel[tuple[ResolvedStockEntry, ...]]):
    root: tuple[ResolvedStockEntry, ...] = ()


class TargetStockEntry(DtoModel):
    from_article: str
    to_article: str
    quantity: PositiveInt
    name: str
    barcode: str


class TargetStockEntries(RootModel[tuple[TargetStockEntry, ...]]):
    root: tuple[TargetStockEntry, ...] = ()


class TargetResolution(DtoModel):
    movable: TargetStockEntries
    skipped: StockMovementItems


class ResolveStockEntriesCommand(DtoModel):
    store_slug: str
    entries: SignedStockEntries
    marketplace: Marketplace
    allow_negative: bool = False


class StockAvailabilityQuery(DtoModel):
    store_slug: str
    entries: ResolvedStockEntries
    fulfillment: str
    marketplace: Marketplace


class TargetResolutionQuery(DtoModel):
    store_slug: str
    entries: ResolvedStockEntries
    marketplace: Marketplace


class StockEntrySplit(DtoModel):
    write_off: ResolvedStockEntries
    surplus: ResolvedStockEntries


class AddFulfillmentItemsRequest(DtoModel):
    fulfillment: str = Field(min_length=1, max_length=200)
    marketplace: Marketplace = Marketplace.WB
    items: tuple[PositiveStockEntry, ...] = Field(min_length=1)


class AddFulfillmentItemsCommand(DtoModel):
    store_slug: str = Field(min_length=1, max_length=100)
    request: AddFulfillmentItemsRequest


class AddedFulfillmentItem(DtoModel):
    article: str
    barcode: str
    name: str
    added: PositiveInt


class AddedFulfillmentItems(RootModel[tuple[AddedFulfillmentItem, ...]]):
    root: tuple[AddedFulfillmentItem, ...] = ()


class CatalogSearchQuery(DtoModel):
    q: str = Field(default="", max_length=200)
    fulfillment: str = Field(default="", max_length=200)
    marketplace: Marketplace | None = None


class FulfillmentCellQuery(DtoModel):
    fulfillment: str = Field(min_length=1, max_length=200)
    marketplace: Marketplace


class TrashCheckRequest(DtoModel):
    marketplace: Marketplace
    article: str = Field(min_length=1, max_length=200)
    fulfillment: str = Field(min_length=1, max_length=200)
    checked: bool = False


class CatalogItem(DtoModel):
    article: str
    barcode: str
    name: str
    marketplace: Marketplace
    mp_sku: str | None = None
    mp_product_id: str | None = None
    image_url: str | None = None


class CatalogItems(RootModel[tuple[CatalogItem, ...]]):
    root: tuple[CatalogItem, ...] = ()


class CatalogQuery(DtoModel):
    store_slug: str
    marketplace: Marketplace


class StockQuantityQuery(DtoModel):
    store_slug: str
    article: str
    fulfillment: str
    marketplace: Marketplace


class StockQuantity(RootModel[int]):
    root: int


class StockIncrement(DtoModel):
    store_slug: str
    article: str
    fulfillment: str
    marketplace: Marketplace
    quantity: int
    updated_at: datetime


class ApplyTransferCommand(DtoModel):
    transfer: TransferStockCommand
    items: TargetStockEntries
    created_at: datetime


class ApplyShipmentCommand(DtoModel):
    shipment: ShipmentCommand
    write_off: ResolvedStockEntries
    surplus: ResolvedStockEntries
    created_at: datetime
