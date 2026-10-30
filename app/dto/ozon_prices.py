from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool

from app.core.constants import OrderScheme, OzonPriceField, OzonPriceSource, OzonPriceState
from app.dto.common import DtoModel
from app.dto.ozon_commissions import CommissionPercent

PriceAmount = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class PriceValue(DtoModel):
    amount: PriceAmount | None = None
    currency: str | None = None
    received_at: AwareDatetime | None = None
    attempted_at: AwareDatetime | None = None
    source: OzonPriceSource | None = None
    scheme: OrderScheme | None = None
    action_ids: tuple[int, ...] = ()
    state: OzonPriceState = OzonPriceState.MISSING
    stale: bool = False


class PriceValues(DtoModel):
    black_price: PriceValue = PriceValue()
    green_price: PriceValue = PriceValue()
    base_price: PriceValue = PriceValue()
    seller_price: PriceValue = PriceValue()

    @property
    def complete(self) -> bool:
        return all(
            self.value(field).state == OzonPriceState.AVAILABLE and self.value(field).amount is not None
            for field in OzonPriceField
        )

    def value(self, field: OzonPriceField) -> PriceValue:
        value: PriceValue = getattr(self, field.value)
        return value


class PriceIdentity(DtoModel):
    product_id: int = Field(gt=0)
    offer_id: str


class PriceChange(DtoModel):
    field: OzonPriceField
    value: PriceValue


class PriceObservation(PriceIdentity):
    changes: tuple[PriceChange, ...]


class StoredPrices(PriceIdentity):
    store_slug: str
    prices: PriceValues = PriceValues()


class DailyPrices(StoredPrices):
    day: date
    first: PriceValues
    closed_at: datetime | None = None


class PriceSyncResult(DtoModel):
    stores: int = 0
    products: int = 0
    closed_days: int = 0
    fbo_commissions: int = 0
    closed_commission_days: int = 0
    error: str | None = None


class PriceApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class SellerPriceAmounts(PriceApiModel):
    price: PriceAmount | None = None
    marketing_seller_price: PriceAmount | None = None
    currency_code: str | None = None
    old_price: PriceAmount | None = None
    min_price: PriceAmount | None = None
    auto_action_enabled: StrictBool | None = None
    auto_add_to_ozon_actions_list_enabled: StrictBool | None = None


class SellerCommissions(PriceApiModel):
    sales_percent_fbo: CommissionPercent | None = None


class SellerPriceItem(PriceApiModel):
    product_id: int = Field(gt=0)
    offer_id: str
    price: SellerPriceAmounts
    commissions: SellerCommissions = SellerCommissions()


class SellerPricePage(PriceApiModel):
    items: tuple[SellerPriceItem, ...]
    cursor: str = ""


class ActionItem(PriceApiModel):
    id: int = Field(gt=0)


class ActionsResponse(PriceApiModel):
    result: tuple[ActionItem, ...]


class WebsiteAmount(PriceApiModel):
    amount: PriceAmount | None = None
    currency: str | None = None


class WebsiteSchemePrices(PriceApiModel):
    black_price: WebsiteAmount | None = None
    green_price: WebsiteAmount | None = None


class WebsitePrices(PriceApiModel):
    prices_by_schema: dict[str, WebsiteSchemePrices] = Field(default_factory=dict)


class ActionProduct(PriceApiModel):
    id: int = Field(gt=0)
    website_prices: WebsitePrices | None = None


class ActionProductPage(PriceApiModel):
    products: tuple[ActionProduct, ...]
    last_id: str = ""


class SellerPricesReceived(DtoModel):
    items: tuple[SellerPriceItem, ...]
    received_at: AwareDatetime


class ActionPricesReceived(DtoModel):
    products: tuple[ActionProduct, ...]
    received_at: AwareDatetime


class WebsiteEvidence(DtoModel):
    product_id: int
    action_id: int
    source: OzonPriceSource
    received_at: AwareDatetime
    prices: WebsiteSchemePrices
    scheme: OrderScheme = OrderScheme.FBO
