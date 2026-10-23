from decimal import ROUND_HALF_UP, Decimal

from app.core.constants import (
    OZON_COMMISSIONS,
    OZON_PRICE_CALCULATOR,
    OzonCommissionInput,
    OzonPriceCalculatorField,
)
from app.dto.ozon_commissions import OzonCommissionRequest
from app.dto.ozon_price_calculator import OzonPriceCalculation, OzonPriceCalculationRequest, OzonPriceEdit
from app.economics import ozon_commissions


def _money(value: Decimal | None) -> Decimal | None:
    return (
        value.quantize(Decimal(OZON_COMMISSIONS.money_quantum), ROUND_HALF_UP) if value is not None else None
    )


def _discount(price: Decimal | None, discounted: Decimal | None) -> Decimal | None:
    if price is None or discounted is None or price <= 0:
        return None
    return ((1 - discounted / price) * OZON_COMMISSIONS.max_percent).quantize(
        Decimal(OZON_COMMISSIONS.percent_quantum), ROUND_HALF_UP
    )


def _commission(values: OzonPriceCalculation, source: OzonCommissionInput) -> OzonPriceCalculation:
    result = ozon_commissions.calculate(
        OzonCommissionRequest(
            price=values.seller_price,
            percent=values.commission_percent,
            amount=values.commission_amount,
            source=source,
        )
    )
    return values.model_copy(
        update={"commission_percent": result.percent, "commission_amount": result.amount}
    )


def _edit(values: OzonPriceCalculation, edit: OzonPriceEdit) -> OzonPriceCalculation:
    field = edit.field
    if field in (
        OzonPriceCalculatorField.COMMISSION_PERCENT,
        OzonPriceCalculatorField.COMMISSION_AMOUNT,
    ):
        return _commission(
            OzonPriceCalculation.model_validate(values.model_dump() | {field.value: edit.value}),
            OzonCommissionInput.AMOUNT
            if field == OzonPriceCalculatorField.COMMISSION_AMOUNT
            else OzonCommissionInput.PERCENT,
        )
    prices: list[Decimal | None] = [
        getattr(values, item.value) for item in OZON_PRICE_CALCULATOR.price_fields
    ]
    discounts = [values.action_discount_percent, values.spp_percent, values.wallet_percent]
    if field in OZON_PRICE_CALCULATOR.price_fields:
        anchor = OZON_PRICE_CALCULATOR.price_fields.index(field)
        prices[anchor] = _money(edit.value)
    elif field == OzonPriceCalculatorField.SPP:
        discounts[1] = edit.value
        anchor = 1
    else:
        discounts[2] = edit.value
        anchor = 2
    factors = [
        1 - discount / OZON_COMMISSIONS.max_percent if discount is not None else None
        for discount in discounts
    ]
    if field in OZON_PRICE_CALCULATOR.price_fields:
        for index in range(anchor - 1, -1, -1):
            factor, child = factors[index], prices[index + 1]
            if factor == 0 and child is not None:
                if child > 0:
                    raise ValueError("A positive price is incompatible with a 100 percent discount")
            else:
                prices[index] = (
                    _money(child / factor)
                    if child is not None and factor is not None and factor > 0
                    else None
                )
    for index in range(anchor, len(factors)):
        factor, parent = factors[index], prices[index]
        prices[index + 1] = _money(parent * factor) if parent is not None and factor is not None else None
    updated = OzonPriceCalculation(
        base_price=prices[0],
        seller_price=prices[1],
        black_price=prices[2],
        green_price=prices[3],
        action_discount_percent=discounts[0],
        spp_percent=discounts[1],
        wallet_percent=discounts[2],
        commission_percent=values.commission_percent,
        regular_commission_percent=values.regular_commission_percent,
        commission_mode=values.commission_mode,
    )
    return _commission(updated, OzonCommissionInput.PERCENT)


def calculate(request: OzonPriceCalculationRequest) -> OzonPriceCalculation:
    values = request.values
    if request.initialize:
        values = values.model_copy(
            update={
                "action_discount_percent": _discount(values.base_price, values.seller_price),
                "spp_percent": _discount(values.seller_price, values.black_price),
                "wallet_percent": _discount(values.black_price, values.green_price),
            }
        )
        values = _commission(values, OzonCommissionInput.PERCENT)
    for edit in request.edits:
        values = _edit(values, edit)
    return OzonPriceCalculation.model_validate(values.model_dump())
