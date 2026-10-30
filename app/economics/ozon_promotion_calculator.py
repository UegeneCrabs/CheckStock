from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal

from app.core.constants import OZON_PROMOTIONS, OZON_SCENARIO, OzonPriceCalculatorField
from app.dto.ozon_price_calculator import OzonPriceCalculation, OzonPriceCalculationRequest, OzonPriceEdit
from app.dto.ozon_promotions import (
    OzonBoostSettings,
    OzonPromotionCalculation,
    OzonPromotionCalculationRequest,
)
from app.economics.ozon_calculator import recalculate


def boost_percent(request: OzonPromotionCalculationRequest, settings: OzonBoostSettings) -> Decimal | None:
    price = request.price
    minimum = request.product.price_min_elastic
    maximum = request.product.price_max_elastic
    limit = minimum or request.product.max_action_price
    if price is None or limit is None or limit.currency != request.currency:
        return None
    if (
        minimum is not None
        and maximum is not None
        and (minimum.currency != maximum.currency or minimum.amount < maximum.amount)
    ):
        return None
    if price <= 0 or price > limit.amount:
        return Decimal(0)
    if minimum is None and maximum is None:
        return settings.static_percent
    if minimum is None or maximum is None or minimum.currency != maximum.currency:
        return None
    if minimum.amount == maximum.amount:
        return settings.static_percent
    if price == minimum.amount:
        return settings.minimum_percent
    if price <= maximum.amount:
        return settings.maximum_percent
    if settings.minimum_percent is None or settings.maximum_percent is None:
        return None
    return (
        settings.minimum_percent
        + (minimum.amount - price)
        / (minimum.amount - maximum.amount)
        * (settings.maximum_percent - settings.minimum_percent)
    ).quantize(Decimal(OZON_SCENARIO.percent_quantum), ROUND_HALF_UP)


def calculate(
    request: OzonPromotionCalculationRequest,
    price_calculator: Callable[[OzonPriceCalculationRequest], OzonPriceCalculation],
    boost_settings: OzonBoostSettings,
) -> OzonPromotionCalculation:
    if request.currency != OZON_PROMOTIONS.currency:
        raise ValueError("Unsupported promotion currency")
    prices = price_calculator(
        request.prices.model_copy(
            update={
                "edits": (OzonPriceEdit(field=OzonPriceCalculatorField.SELLER, value=request.price),),
            }
        )
    )
    values = request.values.model_copy(
        update={
            "seller_price": prices.seller_price,
            "buyer_price": prices.black_price,
            "wallet_price": prices.green_price,
            "commission_amount": prices.commission_amount,
        }
    )
    limit = request.product.price_min_elastic or request.product.max_action_price
    eligible = (
        request.price > 0 and request.price <= limit.amount
        if request.price is not None and limit is not None and limit.currency == request.currency
        else None
    )
    return OzonPromotionCalculation(
        result=recalculate(values).result,
        eligible=eligible,
        boost_percent=boost_percent(request, boost_settings),
    )
