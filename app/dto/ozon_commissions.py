from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import AwareDatetime, Field, model_validator

from app.core.constants import OZON_COMMISSIONS, OzonCommissionInput, OzonCommissionState, OzonPriceSource
from app.dto.common import DtoModel

CommissionPercent = Annotated[
    Decimal,
    Field(ge=OZON_COMMISSIONS.min_percent, le=OZON_COMMISSIONS.max_percent, allow_inf_nan=False),
]
CommissionAmount = Annotated[
    Decimal,
    Field(
        ge=0,
        allow_inf_nan=False,
        max_digits=OZON_COMMISSIONS.amount_max_digits,
        decimal_places=OZON_COMMISSIONS.amount_decimal_places,
    ),
]


class FboCommissionValue(DtoModel):
    percent: CommissionPercent | None = None
    received_at: AwareDatetime | None = None
    attempted_at: AwareDatetime | None = None
    source: OzonPriceSource = OzonPriceSource.SELLER
    state: OzonCommissionState = OzonCommissionState.MISSING


class OzonCommissionCalculation(DtoModel):
    price: CommissionAmount | None = None
    percent: CommissionPercent | None = None
    amount: CommissionAmount | None = None


class OzonCommissionRequest(OzonCommissionCalculation):
    source: OzonCommissionInput = OzonCommissionInput.PERCENT

    @model_validator(mode="after")
    def valid_amount(self) -> Self:
        if (
            self.source == OzonCommissionInput.AMOUNT
            and self.price is not None
            and self.amount is not None
            and self.amount > self.price
        ):
            raise ValueError("Commission amount cannot exceed its price basis")
        return self


class OzonCommissionData(FboCommissionValue):
    price: CommissionAmount | None = None
    amount: CommissionAmount | None = None
    stale: bool = False


class FboCommissionObservation(DtoModel):
    product_id: int = Field(gt=0)
    offer_id: str
    value: FboCommissionValue


class StoredFboCommission(FboCommissionObservation):
    store_slug: str


class DailyFboCommission(DtoModel):
    store_slug: str
    product_id: int = Field(gt=0)
    offer_id: str
    day: date
    first: FboCommissionValue
    last: FboCommissionValue
    closed_at: datetime | None = None
