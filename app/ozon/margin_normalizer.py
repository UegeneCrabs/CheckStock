from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.core.constants import OZON_MARGIN, OZON_SCENARIO
from app.dto.ozon_margin import OzonMarginDay, OzonMarginInputs, OzonMarginResult, OzonMarginSnapshot


def money(value: Decimal | None) -> Decimal | None:
    return value.quantize(Decimal(OZON_SCENARIO.money_quantum), ROUND_HALF_UP) if value is not None else None


def unit_margin(inputs: OzonMarginInputs) -> OzonMarginResult:
    missing = tuple(key for key in type(inputs).model_fields if getattr(inputs, key) is None)
    if missing:
        return OzonMarginResult(missing=missing)
    price = money(inputs.seller_price)
    costs = sum((money(getattr(inputs, key)) or Decimal(0) for key in OZON_MARGIN.expense_fields), Decimal(0))
    margin = price - costs if price is not None else None
    purchase = money(inputs.purchase)
    return OzonMarginResult(
        margin=money(margin),
        roi=money(margin / purchase * OZON_SCENARIO.percent_factor)
        if margin is not None and purchase is not None and purchase > 0
        else None,
    )


def advertising_per_unit(
    spend: Decimal | None, orders: Decimal | None, buyout: Decimal | None
) -> Decimal | None:
    if spend == 0:
        return Decimal(0)
    expected = (
        orders * buyout / OZON_SCENARIO.percent_factor if orders is not None and buyout is not None else None
    )
    return money(spend / expected) if spend is not None and expected is not None and expected > 0 else None


def day_margin(
    day: date,
    snapshot: OzonMarginSnapshot | None,
    orders: int | None,
    spend: Decimal | None,
    *,
    issues: tuple[str, ...] = (),
) -> OzonMarginDay:
    inputs = OzonMarginInputs.model_validate(
        {
            key: money(getattr(snapshot.inputs, key)) if snapshot else None
            for key in OzonMarginInputs.model_fields
        }
    )
    buyout = snapshot.buyout_percent if snapshot else None
    expected = (
        Decimal(0)
        if orders == 0
        else Decimal(orders) * buyout / OZON_SCENARIO.percent_factor
        if orders is not None and buyout is not None
        else None
    )
    inputs = inputs.model_copy(
        update={
            "advertising_amount": advertising_per_unit(
                spend, Decimal(orders) if orders is not None else None, buyout
            )
        }
    )
    result = unit_margin(inputs)
    baseline = unit_margin(inputs.model_copy(update={"advertising_amount": Decimal(0)}))
    missing = list(result.missing)
    if snapshot is None:
        missing.append(OZON_MARGIN.snapshot_missing)
    if orders is None:
        missing.append(OZON_MARGIN.orders_missing)
    if buyout is None and orders != 0:
        missing.append(OZON_MARGIN.buyout_missing)
    if spend is None:
        missing.append(OZON_MARGIN.advertising_missing)
    profit = None
    if expected == 0:
        profit = money(-spend) if spend is not None else None
        missing = [OZON_MARGIN.advertising_missing] if spend is None else []
    elif expected is not None and baseline.margin is not None and spend is not None:
        profit = money(baseline.margin * expected - spend)
    return OzonMarginDay(
        day=day,
        inputs=inputs,
        orders=orders,
        buyout_percent=buyout,
        advertising_spend=spend,
        expected_buyouts=expected,
        profit=profit,
        margin=result.margin if expected != 0 else None,
        roi=result.roi if expected != 0 else None,
        purchase_value=money(inputs.purchase * expected)
        if inputs.purchase is not None and expected is not None
        else Decimal(0)
        if expected == 0
        else None,
        missing=tuple(dict.fromkeys(missing)),
        issues=tuple(dict.fromkeys((snapshot.issues if snapshot else ()) + issues)),
    )
