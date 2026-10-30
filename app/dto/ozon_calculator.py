from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, model_validator

from app.core.constants import OZON_ADVERTISING, OZON_SCENARIO, OzonScenarioBasis, OzonScenarioField
from app.dto.common import DtoModel
from app.dto.ozon_advertising import AdvertisingDailyExpense, AdvertisingPeriod
from app.dto.ozon_margin import OzonMarginResult

ScenarioSignedNumber = Annotated[
    Decimal,
    Field(
        ge=-OZON_SCENARIO.max_money,
        le=OZON_SCENARIO.max_money,
        allow_inf_nan=False,
        max_digits=OZON_SCENARIO.number_max_digits,
        decimal_places=OZON_SCENARIO.number_decimal_places,
    ),
]
ScenarioNumber = Annotated[ScenarioSignedNumber, Field(ge=0)]
ScenarioPercent = Annotated[ScenarioNumber, Field(le=OZON_SCENARIO.percent_factor)]


class OzonCalculatorOtherExpense(AdvertisingDailyExpense):
    drr: ScenarioSignedNumber | None = None


class OzonCalculatorAdvertisingWeek(DtoModel):
    period: AdvertisingPeriod
    spend: ScenarioSignedNumber | None = None
    orders_count: int | None = Field(default=None, ge=0)
    complete: bool = False
    missing_dates: tuple[date, ...] = ()
    partial_dates: tuple[date, ...] = ()
    other_incomplete: bool = False
    advertising_updated_at: datetime | None = None
    orders_updated_at: datetime | None = None


class OzonCalculatorValues(DtoModel):
    advertising_week: OzonCalculatorAdvertisingWeek | None = None
    other_spend: ScenarioSignedNumber | None = None
    other_drr: ScenarioSignedNumber | None = None
    other_breakdown: tuple[OzonCalculatorOtherExpense, ...] | None = None
    orders_count: ScenarioNumber | None = None
    commission_amount: ScenarioNumber | None = None
    logistics_amount: ScenarioSignedNumber | None = None
    advertising_amount: ScenarioSignedNumber | None = None
    advertising_manual: bool = False
    result: OzonMarginResult = OzonMarginResult()
    seller_price: ScenarioNumber | None = None
    buyer_price: ScenarioNumber | None = None
    wallet_price: ScenarioNumber | None = None
    turnover: ScenarioNumber | None = None
    buyout_percent: ScenarioPercent | None = None
    click_spend: ScenarioSignedNumber | None = None
    click_drr: ScenarioSignedNumber | None = None
    order_spend: ScenarioSignedNumber | None = None
    order_drr: ScenarioSignedNumber | None = None
    total_drr: ScenarioSignedNumber | None = None
    purchase: ScenarioNumber | None = None
    fulfillment: ScenarioNumber | None = None
    storage_amount: ScenarioSignedNumber | None = None
    acquiring_percent: ScenarioPercent | None = None
    acquiring_amount: ScenarioNumber | None = None
    team_percent: ScenarioPercent | None = None
    team_amount: ScenarioNumber | None = None
    unallocated_percent: ScenarioPercent | None = Decimal(0)
    unallocated_amount: ScenarioNumber | None = None
    vat_percent: ScenarioPercent | None = None
    vat_amount: ScenarioNumber | None = None
    usn_percent: ScenarioPercent | None = None
    usn_amount: ScenarioNumber | None = None
    click_basis: OzonScenarioBasis = OzonScenarioBasis.AMOUNT
    order_basis: OzonScenarioBasis = OzonScenarioBasis.AMOUNT
    acquiring_basis: OzonScenarioBasis = OzonScenarioBasis.PERCENT
    team_basis: OzonScenarioBasis = OzonScenarioBasis.PERCENT
    unallocated_basis: OzonScenarioBasis = OzonScenarioBasis.PERCENT
    vat_basis: OzonScenarioBasis = OzonScenarioBasis.PERCENT
    usn_basis: OzonScenarioBasis = OzonScenarioBasis.PERCENT

    @model_validator(mode="after")
    def distinct_other_kinds(self) -> Self:
        if self.other_breakdown is not None:
            kinds = tuple(item.kind for item in self.other_breakdown)
            if len(set(kinds)) != len(kinds) or any(
                kind in OZON_ADVERTISING.required_spend_kinds for kind in kinds
            ):
                raise ValueError("Other advertising must contain distinct non-CPC/CPO kinds")
        return self


class OzonCalculatorEdit(DtoModel):
    field: OzonScenarioField
    value: ScenarioSignedNumber | None = None

    @model_validator(mode="after")
    def bounded_percent(self) -> Self:
        if self.value is not None and self.value < 0 and self.field not in OZON_SCENARIO.signed_fields:
            raise ValueError("Cost and rate cannot be negative")
        if (
            self.field in OZON_SCENARIO.bounded_percent_fields
            and self.value is not None
            and self.value > OZON_SCENARIO.percent_factor
        ):
            raise ValueError("Percent cannot exceed 100")
        return self


class OzonCalculatorRequest(DtoModel):
    values: OzonCalculatorValues
    edits: tuple[OzonCalculatorEdit, ...] = Field(default=(), max_length=OZON_SCENARIO.max_edits)
