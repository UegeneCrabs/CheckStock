from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BeforeValidator, Field, StrictBool, model_validator

from app.core.constants import OZON_PROMOTIONS, OzonPromotionIssue, OzonPromotionStatus
from app.dto.common import DtoModel
from app.dto.ozon_calculator import OzonCalculatorValues, ScenarioNumber, ScenarioPercent
from app.dto.ozon_margin import OzonMarginResult
from app.dto.ozon_orders import OzonPayload, optional_date
from app.dto.ozon_price_calculator import OzonPriceCalculationRequest
from app.dto.ozon_prices import WebsitePrices

PromotionDate = Annotated[datetime | None, BeforeValidator(optional_date)]


class OzonPromotionsIncompleteError(ValueError):
    pass


class OzonPromotionsQuery(DtoModel):
    store: str = Field(min_length=1)
    article: str = Field(min_length=1)


class OzonPromotionMoney(OzonPayload):
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(min_length=1)


class OzonAction(OzonPayload):
    id: int = Field(gt=0)
    title: str = Field(min_length=1)
    action_type: str = ""
    date_start: PromotionDate = None
    date_end: PromotionDate = None
    freeze_date: PromotionDate = None
    is_voucher_action: StrictBool | None = None


class OzonActionsResponse(OzonPayload):
    result: tuple[OzonAction, ...]


class OzonActionProduct(OzonPayload):
    id: int = Field(gt=0)
    price: OzonPromotionMoney | None = None
    action_price: OzonPromotionMoney | None = None
    max_action_price: OzonPromotionMoney | None = None
    min_seller_price: OzonPromotionMoney | None = None
    price_min_elastic: OzonPromotionMoney | None = None
    price_max_elastic: OzonPromotionMoney | None = None
    add_mode: str | None = None
    stock: int | None = Field(default=None, ge=0)
    min_stock: int | None = Field(default=None, ge=0)
    is_quarantined: StrictBool | None = None
    alert_max_action_price_failed: StrictBool | None = None
    website_prices: WebsitePrices | None = None


class OzonActionProductsRequest(DtoModel):
    action_id: int = Field(gt=0)
    last_id: str = ""
    limit: int = Field(default=OZON_PROMOTIONS.page_size, ge=1, le=OZON_PROMOTIONS.page_size)


class OzonActionProductsPage(OzonPayload):
    products: tuple[OzonActionProduct, ...]
    total: int = Field(ge=0)
    last_id: str = ""


class OzonPromotionProductIdentity(OzonPayload):
    id: int = Field(gt=0)
    offer_id: str = Field(min_length=1)


class OzonPromotionProductIdentities(OzonPayload):
    items: tuple[OzonPromotionProductIdentity, ...]


class OzonPromotionItem(DtoModel):
    action: OzonAction
    status: OzonPromotionStatus
    product: OzonActionProduct | None = None
    issue: OzonPromotionIssue | None = None


class OzonPromotions(DtoModel):
    product_id: int = Field(gt=0)
    items: tuple[OzonPromotionItem, ...]
    complete: bool
    checked_at: datetime


class OzonPromotionCalculationRequest(DtoModel):
    product: OzonActionProduct
    price: ScenarioNumber | None = None
    currency: str = Field(min_length=1)
    values: OzonCalculatorValues
    prices: OzonPriceCalculationRequest


class OzonPromotionCalculation(DtoModel):
    result: OzonMarginResult
    eligible: bool | None = None
    boost_percent: ScenarioPercent | None = None


class OzonBoostSettings(DtoModel):
    minimum_percent: ScenarioPercent | None = None
    maximum_percent: ScenarioPercent | None = None
    static_percent: ScenarioPercent | None = None

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if (
            self.minimum_percent is not None
            and self.maximum_percent is not None
            and self.minimum_percent > self.maximum_percent
        ):
            raise ValueError("Minimum boost cannot exceed maximum boost")
        return self


class OzonBoostRecord(DtoModel):
    values: OzonBoostSettings = OzonBoostSettings()
    revision: int = Field(default=0, ge=0)
    updated_at: datetime | None = None
    updated_by: str | None = None


class OzonBoostUpdate(DtoModel):
    values: OzonBoostSettings
    revision: int = Field(ge=0)


class OzonBoostSave(DtoModel):
    change: OzonBoostUpdate
    user_name: str
    saved_at: datetime
