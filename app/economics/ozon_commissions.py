from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.core.constants import OZON_COMMISSIONS, TIME, OzonCommissionInput, OzonCommissionState
from app.dto.ozon_commissions import (
    FboCommissionValue,
    OzonCommissionCalculation,
    OzonCommissionData,
    OzonCommissionRequest,
)
from app.dto.ozon_prices import PriceValue


def calculate(request: OzonCommissionRequest) -> OzonCommissionCalculation:
    price, percent, amount = request.price, request.percent, request.amount
    divisor = Decimal(OZON_COMMISSIONS.max_percent)
    if request.source == OzonCommissionInput.AMOUNT:
        percent = (
            (amount / price * divisor).quantize(Decimal(OZON_COMMISSIONS.percent_quantum), ROUND_HALF_UP)
            if amount is not None and price is not None and price > 0
            else None
        )
    else:
        amount = price * percent / divisor if price is not None and percent is not None else None
    return OzonCommissionCalculation(
        price=price,
        percent=percent,
        amount=amount.quantize(Decimal(OZON_COMMISSIONS.money_quantum), ROUND_HALF_UP)
        if amount is not None
        else None,
    )


def snapshot(value: FboCommissionValue | None, price: PriceValue, now: datetime) -> OzonCommissionData:
    value = value or FboCommissionValue()
    result = calculate(OzonCommissionRequest(price=price.amount, percent=value.percent))
    return OzonCommissionData(
        **value.model_dump(),
        price=result.price,
        amount=result.amount,
        stale=value.state == OzonCommissionState.ERROR
        or (
            value.received_at is not None
            and value.received_at.astimezone(TIME.moscow_timezone).date()
            < now.astimezone(TIME.moscow_timezone).date()
        ),
    )
