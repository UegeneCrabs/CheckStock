from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool

from app.core.constants import OZON_PRICES, OZON_PROMOTIONS, OZON_SCENARIO, OzonEntryIssue, OzonEntryState
from app.dto.common import DtoModel
from app.dto.ozon_calculator import OzonCalculatorValues
from app.dto.ozon_commissions import OzonCommissionData
from app.dto.ozon_orders import OzonPayload
from app.dto.ozon_price_calculator import OzonPriceCalculationRequest
from app.dto.ozon_prices import PriceValues, SellerPriceItem
from app.dto.ozon_promotions import OzonPromotionCalculation, OzonPromotionMoney, OzonPromotions

EntryAmount = Annotated[
    Decimal, Field(gt=0, le=OZON_SCENARIO.max_money, decimal_places=2, allow_inf_nan=False)
]


class OzonEntryBusyError(Exception):
    pass


class OzonEntryRequest(DtoModel):
    store: str = Field(min_length=1)
    article: str = Field(min_length=1)
    action_id: int = Field(gt=0)
    price: EntryAmount
    currency: str = Field(min_length=1)
    stock: int | None = Field(default=None, ge=1)
    values: OzonCalculatorValues
    prices: OzonPriceCalculationRequest


class OzonEntryConfirm(DtoModel):
    confirmed: Literal[True]


class OzonEntryStocks(OzonPayload):
    has_stock: StrictBool | None = None


class OzonEntryIdentity(OzonPayload):
    id: int = Field(gt=0)
    offer_id: str = Field(min_length=1)
    name: str = ""
    stocks: OzonEntryStocks | None = None
    is_archived: StrictBool | None = None
    is_autoarchived: StrictBool | None = None


class OzonEntryIdentities(OzonPayload):
    items: tuple[OzonEntryIdentity, ...]


class OzonEntryPriceFilter(DtoModel):
    offer_id: tuple[str, ...]
    visibility: str = OZON_PRICES.visibility


class OzonEntryPriceRequest(DtoModel):
    filter: OzonEntryPriceFilter
    limit: int = OZON_PROMOTIONS.page_size
    cursor: str = ""


class OzonEntryProductUpdate(DtoModel):
    product_id: int = Field(gt=0)
    action_price: OzonPromotionMoney
    stock: int | None = None


class OzonEntryUpdate(DtoModel):
    action_id: int = Field(gt=0)
    products: tuple[OzonEntryProductUpdate, ...]


class OzonEntryReason(OzonPayload):
    product_id: int = Field(gt=0)
    reason: str = Field(max_length=OZON_PROMOTIONS.entry_reason_max_length)


class OzonEntryReceipt(OzonPayload):
    active_product_ids: tuple[int, ...]
    deactivated_product_ids: tuple[int, ...]
    rejected: tuple[OzonEntryReason, ...]
    warnings: tuple[OzonEntryReason, ...]


class OzonEntryEvidence(DtoModel):
    identity: OzonEntryIdentity
    prices: SellerPriceItem
    promotions: OzonPromotions


class OzonEntryRefreshedPrices(DtoModel):
    prices: PriceValues
    commission: OzonCommissionData


class OzonEntryOperation(DtoModel):
    id: UUID
    user_id: int
    user_name: str
    request: OzonEntryRequest
    state: OzonEntryState
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    sent_at: datetime | None = None
    evidence: OzonEntryEvidence | None = None
    observed: OzonEntryEvidence | None = None
    calculation: OzonPromotionCalculation | None = None
    issue: OzonEntryIssue | None = None
    reason: str | None = None
    receipt: OzonEntryReceipt | None = None
    refreshed: OzonEntryRefreshedPrices | None = None


class OzonEntryView(DtoModel):
    id: UUID
    store: str
    article: str
    action_id: int
    state: OzonEntryState
    price: OzonPromotionMoney
    stock: int | None = None
    current_price: OzonPromotionMoney | None = None
    evidence: OzonEntryEvidence | None = None
    observed: OzonEntryEvidence | None = None
    calculation: OzonPromotionCalculation | None = None
    refreshed: OzonEntryRefreshedPrices | None = None
    issue: OzonEntryIssue | None = None
    reason: str | None = None
    warnings: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class OzonEntryHistory(DtoModel):
    items: tuple[OzonEntryView, ...]


class OzonEntryStart(DtoModel):
    view: OzonEntryView
    start: bool
