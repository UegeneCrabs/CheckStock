from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, model_validator

from app.core.constants import (
    OZON_COMMISSIONS,
    OZON_PRICE_CALCULATOR,
    OzonCalculatorCommissionMode,
    OzonPriceCalculatorField,
)
from app.dto.common import DtoModel
from app.dto.ozon_commissions import CommissionAmount, CommissionPercent
from app.dto.ozon_tariffs import TariffId

CalculatorNumber = Annotated[
    Decimal,
    Field(
        allow_inf_nan=False,
        max_digits=OZON_PRICE_CALCULATOR.number_max_digits,
        decimal_places=OZON_PRICE_CALCULATOR.number_decimal_places,
    ),
]
DiscountPercent = Annotated[CalculatorNumber, Field(le=OZON_COMMISSIONS.max_percent)]


class OzonPriceCalculation(DtoModel):
    base_price: CommissionAmount | None = None
    seller_price: CommissionAmount | None = None
    black_price: CommissionAmount | None = None
    green_price: CommissionAmount | None = None
    action_discount_percent: DiscountPercent | None = None
    spp_percent: DiscountPercent | None = None
    wallet_percent: DiscountPercent | None = None
    commission_percent: CommissionPercent | None = None
    commission_amount: CommissionAmount | None = None
    regular_commission_percent: CommissionPercent | None = None
    commission_mode: OzonCalculatorCommissionMode = OzonCalculatorCommissionMode.AUTOMATIC


class OzonPriceEdit(DtoModel):
    field: OzonPriceCalculatorField
    value: CalculatorNumber | None = None

    @model_validator(mode="after")
    def valid_value(self) -> Self:
        if self.value is None:
            return self
        if (
            self.field
            in (
                OzonPriceCalculatorField.SPP,
                OzonPriceCalculatorField.WALLET,
                OzonPriceCalculatorField.COMMISSION_PERCENT,
            )
            and self.value > OZON_COMMISSIONS.max_percent
        ):
            raise ValueError("Percent cannot exceed 100")
        if (
            self.field
            not in (
                OzonPriceCalculatorField.SPP,
                OzonPriceCalculatorField.WALLET,
            )
            and self.value < 0
        ):
            raise ValueError("Price and commission cannot be negative")
        return self


class OzonPriceCalculationRequest(DtoModel):
    values: OzonPriceCalculation
    initialize: bool = False
    tariff_id: TariffId | None = None
    product_tariff_id: TariffId | None = None
    edits: tuple[OzonPriceEdit, ...] = Field(default=(), max_length=OZON_PRICE_CALCULATOR.max_edits)
