from typing import Annotated, Literal

from pydantic import Field

from app.dto.common import DtoModel

Amount = Annotated[float, Field(ge=0, le=1_000_000_000, allow_inf_nan=False)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Scheme = Literal["FBY", "FBS"]


class EconomicsValues(DtoModel):
    seller_price: Amount | None = None
    buyer_price: Amount | None = None
    purchase_price: Amount | None = None
    fulfillment_cost: Amount | None = None
    category_id: int | None = Field(default=None, gt=0)
    category_name: str | None = Field(default=None, max_length=500)
    length: Amount | None = None
    width: Amount | None = None
    height: Amount | None = None
    weight: Amount | None = None
    commission_percent: Percent | None = None
    payment_acceptance: Amount | None = None
    payment_transfer_percent: Percent | None = None
    delivery_cost: Amount | None = None
    return_cost: Amount | None = None
    storage_per_day: Amount | None = None
    storage_days: int | None = Field(default=None, ge=0, le=3650)
    transit_cost: Amount | None = None
    other_percent: Percent | None = None
    other_cost: Amount | None = None
    tax_percent: Percent | None = None
    tax_base: Literal["buyer", "seller"] | None = None
    capital_percent: Percent | None = None
    turnover_days: int | None = Field(default=None, ge=0, le=3650)
    loss_percent: Percent | None = None
    disposal_cost: Amount | None = None
    buyout_percent: Percent | None = None
    plan_drr: Percent | None = None
    advertising_mode: Literal["actual", "plan"] | None = None
    campaign_id: int | None = Field(default=None, gt=0)
    frequency: Literal["DAILY", "WEEKLY", "BIWEEKLY", "MONTHLY"] | None = None
    payment_delay_weeks: Literal[0, 1, 2, 4] | None = None


class SettingsChange(DtoModel):
    scheme: Scheme = "FBY"
    revision: int = Field(ge=0)
    values: EconomicsValues


class CalculationRequest(DtoModel):
    scheme: Scheme = "FBY"
    mode: Literal["current", "calculator"] = "calculator"
    values: EconomicsValues = Field(default_factory=EconomicsValues)
    refresh_tariffs: bool = False


class SyncRequest(DtoModel):
    scheme: Scheme = "FBY"
    article: str | None = Field(default=None, max_length=500)
