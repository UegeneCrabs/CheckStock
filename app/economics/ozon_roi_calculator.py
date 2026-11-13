from collections.abc import Callable
from decimal import Decimal

from app.core.constants import (
    OZON_LOGISTICS,
    OZON_SCENARIO,
    OZON_TARIFFS,
    OzonCalculatorTarget,
    OzonPriceCalculatorField,
)
from app.dto.ozon_calculator import (
    OzonCalculatorPriceResult,
    OzonCalculatorRoiRequest,
    OzonCalculatorRoiResult,
    OzonCalculatorTargetRequest,
    OzonCalculatorValues,
)
from app.dto.ozon_price_calculator import OzonPriceCalculation, OzonPriceCalculationRequest, OzonPriceEdit
from app.economics.ozon_calculator import calculate, recalculate, rounded


def calculate_roi(
    request: OzonCalculatorRoiRequest,
    price_calculator: Callable[[OzonPriceCalculationRequest], OzonPriceCalculation],
) -> OzonCalculatorRoiResult:
    result = calculate_target(
        OzonCalculatorTargetRequest(
            values=request.values,
            edits=request.edits,
            prices=request.prices,
            target=OzonCalculatorTarget.ROI,
            value=request.roi,
        ),
        price_calculator,
    )
    return OzonCalculatorRoiResult(values=result.values, prices=result.prices)


def calculate_target(
    request: OzonCalculatorTargetRequest,
    price_calculator: Callable[[OzonPriceCalculationRequest], OzonPriceCalculation],
) -> OzonCalculatorPriceResult:
    values = calculate(request)
    purchase = rounded(values.purchase)
    if request.target == OzonCalculatorTarget.ROI and (purchase is None or purchase <= 0):
        raise ValueError("Positive purchase required")
    prices = price_calculator(request.prices)
    if prices.spp_percent is None or prices.wallet_percent is None:
        raise ValueError("Price discounts required")
    target = request.value

    def metric(result: OzonCalculatorPriceResult) -> Decimal | None:
        if request.target == OzonCalculatorTarget.MARGIN:
            return result.values.result.margin
        base = (
            rounded(result.values.seller_price)
            if request.target == OzonCalculatorTarget.MARGIN_PERCENT
            else purchase
        )
        margin = result.values.result.margin
        return (
            margin / base * OZON_SCENARIO.percent_factor
            if margin is not None and base is not None and base > 0
            else None
        )

    def at(cents: int) -> OzonCalculatorPriceResult:
        updated = price_calculator(
            request.prices.model_copy(
                update={
                    "values": prices,
                    "initialize": False,
                    "edits": (
                        OzonPriceEdit(
                            field=OzonPriceCalculatorField.SELLER,
                            value=Decimal(cents) / OZON_SCENARIO.price_scale,
                        ),
                    ),
                }
            )
        )
        scenario = OzonCalculatorValues.model_validate(
            values.model_dump()
            | {
                "seller_price": updated.seller_price,
                "buyer_price": updated.black_price,
                "wallet_price": updated.green_price,
                "commission_amount": updated.commission_amount,
            }
        )
        return OzonCalculatorPriceResult(values=recalculate(scenario), prices=updated)

    start = 1
    fallback: OzonCalculatorPriceResult | None = None
    limits = {OZON_TARIFFS.first_price_limit, OZON_TARIFFS.second_price_limit, OZON_SCENARIO.target_max_price}
    if values.new_product_costs and values.new_product_costs.logistics:
        limits.add(OZON_LOGISTICS.tariff_price_limit)
    for limit in sorted(limits):
        end = limit * OZON_SCENARIO.price_scale
        try:
            ceiling = at(end)
        except ValueError:
            start = end + 1
            continue
        last_value = metric(ceiling)
        if last_value is None:
            start = end + 1
            continue
        lower, upper = start, end
        while lower < upper:
            middle = (lower + upper) // 2
            try:
                at(middle)
                upper = middle
            except ValueError:
                lower = middle + 1
        floor = lower
        first = at(floor)
        first_value = metric(first)
        for boundary in (first, ceiling):
            boundary_value = metric(boundary)
            fallback_value = metric(fallback) if fallback is not None else None
            if (
                boundary_value is not None
                and boundary_value >= target
                and (fallback_value is None or boundary_value < fallback_value)
            ):
                fallback = boundary
        if first_value is None or not min(first_value, last_value) <= target <= max(first_value, last_value):
            start = end + 1
            continue
        increasing = last_value >= first_value
        lower, upper = floor, end
        while lower < upper:
            middle = (lower + upper) // 2
            actual = metric(at(middle))
            if actual is not None and (actual >= target if increasing else actual <= target):
                upper = middle
            else:
                lower = middle + 1
        result = at(lower)
        actual = metric(result)
        if not increasing and actual is not None and actual < target:
            result = at(max(floor, lower - 1))
        actual = metric(result)
        if actual is not None and actual >= target:
            return result
    if fallback is not None:
        return fallback
    raise ValueError("Target unavailable with current inputs")
