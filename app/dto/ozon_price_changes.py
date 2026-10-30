from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool

from app.core.constants import (
    OZON_PRICE_CHANGES,
    OZON_SCENARIO,
    OzonPriceChangeIssue,
    OzonPriceChangeState,
    OzonPriceField,
    OzonPromotionStatus,
)
from app.dto.common import DtoModel
from app.dto.ozon_orders import OzonPayload
from app.dto.ozon_price_calculator import OzonPriceCalculation
from app.dto.ozon_prices import PriceAmount, PriceValues, SellerPriceItem
from app.dto.ozon_promotion_entry import OzonEntryIdentity
from app.dto.ozon_promotions import OzonPromotions

PriceChangeAmount = Annotated[
    Decimal, Field(gt=0, le=OZON_SCENARIO.max_money, decimal_places=2, allow_inf_nan=False)
]


class OzonPriceChangeBusyError(Exception):
    pass


class OzonPriceChangeRequest(DtoModel):
    store: str = Field(min_length=1)
    article: str = Field(min_length=1)
    target_field: OzonPriceField
    target_price: PriceChangeAmount


class OzonPriceChangeConfirm(DtoModel):
    confirmed: Literal[True]


class OzonPriceChangeEvidence(DtoModel):
    identity: OzonEntryIdentity
    prices: SellerPriceItem
    promotions: OzonPromotions
    received_at: AwareDatetime


class OzonPriceChangeImpact(DtoModel):
    action_id: int
    title: str
    current_status: OzonPromotionStatus
    expected_participation: bool | None


class OzonPriceChangePlan(DtoModel):
    price: PriceChangeAmount
    previous_price: PriceAmount
    old_price: PriceAmount
    min_price: PriceAmount
    currency: str
    prediction: OzonPriceCalculation
    impacts: tuple[OzonPriceChangeImpact, ...]
    previous_prices: PriceValues | None = None


class OzonPriceChangeItem(DtoModel):
    product_id: int = Field(gt=0)
    price: PriceChangeAmount
    old_price: PriceAmount
    min_price: PriceAmount
    currency_code: str


class OzonPriceChangePayload(DtoModel):
    prices: tuple[OzonPriceChangeItem]


class OzonPriceChangeError(OzonPayload):
    code: str
    message: str = Field(default="", max_length=OZON_PRICE_CHANGES.reason_max_length)


class OzonPriceChangeResult(OzonPayload):
    product_id: int = Field(gt=0)
    offer_id: str
    updated: StrictBool
    errors: tuple[OzonPriceChangeError, ...]


class OzonPriceChangeReceipt(OzonPayload):
    result: tuple[OzonPriceChangeResult, ...]


class OzonPriceChangeOperation(DtoModel):
    id: UUID
    user_id: int
    user_name: str
    request: OzonPriceChangeRequest
    state: OzonPriceChangeState
    created_at: AwareDatetime
    updated_at: AwareDatetime
    expires_at: AwareDatetime
    sent_at: AwareDatetime | None = None
    evidence: OzonPriceChangeEvidence | None = None
    observed: OzonPriceChangeEvidence | None = None
    plan: OzonPriceChangePlan | None = None
    issue: OzonPriceChangeIssue | None = None
    receipt: OzonPriceChangeReceipt | None = None
    refreshed: PriceValues | None = None


class OzonPriceChangeView(DtoModel):
    id: UUID
    store: str
    article: str
    state: OzonPriceChangeState
    target_field: OzonPriceField
    target_price: PriceChangeAmount
    user_name: str | None = None
    plan: OzonPriceChangePlan | None = None
    observed: OzonPriceChangeEvidence | None = None
    issue: OzonPriceChangeIssue | None = None
    errors: tuple[OzonPriceChangeError, ...] = ()
    refreshed: PriceValues | None = None
    sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime


class OzonPriceChangeHistory(DtoModel):
    items: tuple[OzonPriceChangeView, ...]


class OzonPriceChangeStart(DtoModel):
    view: OzonPriceChangeView
    start: bool
