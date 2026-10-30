from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.core.constants import OZON_SCENARIO, OzonScenarioBasis, OzonScenarioField
from app.dto.ozon_calculator import OzonCalculatorRequest, OzonCalculatorValues
from app.dto.ozon_margin import OzonMarginInputs
from app.ozon.drr import advertising_drr, expected_turnover
from app.ozon.margin_normalizer import advertising_per_unit, unit_margin


@dataclass(frozen=True, slots=True, kw_only=True)
class CalculationPair:
    percent: Decimal | None
    amount: Decimal | None


def rounded(value: Decimal | None, *, percent: bool = False) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(
        Decimal(OZON_SCENARIO.percent_quantum if percent else OZON_SCENARIO.money_quantum), ROUND_HALF_UP
    )


def pair(
    base: Decimal | None, percent: Decimal | None, amount: Decimal | None, basis: OzonScenarioBasis
) -> CalculationPair:
    if basis == OzonScenarioBasis.PERCENT:
        return CalculationPair(
            percent=percent,
            amount=rounded(base * percent / OZON_SCENARIO.percent_factor)
            if base is not None and percent is not None
            else None,
        )
    return CalculationPair(
        percent=rounded(amount / base * OZON_SCENARIO.percent_factor, percent=True)
        if amount is not None and base is not None and base > 0
        else None,
        amount=amount,
    )


def advertising(
    turnover: Decimal | None,
    buyout: Decimal | None,
    drr: Decimal | None,
    spend: Decimal | None,
    basis: OzonScenarioBasis,
) -> CalculationPair:
    if basis == OzonScenarioBasis.AMOUNT:
        return CalculationPair(
            amount=spend,
            percent=rounded(advertising_drr(spend, turnover, buyout), percent=True),
        )
    if buyout == 0 and drr is not None and drr != 0:
        raise ValueError("Nonzero DRR is incompatible with zero buyout")
    expected = expected_turnover(turnover, buyout)
    return CalculationPair(
        percent=drr,
        amount=rounded(drr * expected / OZON_SCENARIO.percent_factor)
        if drr is not None and expected is not None
        else None,
    )


def recalculate(values: OzonCalculatorValues) -> OzonCalculatorValues:
    other_breakdown = (
        tuple(
            item.model_copy(
                update={
                    "drr": advertising(
                        values.turnover, values.buyout_percent, None, item.amount, OzonScenarioBasis.AMOUNT
                    ).percent
                }
            )
            for item in values.other_breakdown
        )
        if values.other_breakdown is not None
        else None
    )
    other_spend = (
        sum((item.amount for item in other_breakdown if item.amount is not None), Decimal(0))
        if other_breakdown is not None
        else values.other_spend
    )
    other_drr = advertising(
        values.turnover, values.buyout_percent, None, other_spend, OzonScenarioBasis.AMOUNT
    ).percent
    acquiring = pair(
        values.wallet_price, values.acquiring_percent, values.acquiring_amount, values.acquiring_basis
    )
    team = pair(values.seller_price, values.team_percent, values.team_amount, values.team_basis)
    unallocated = pair(
        values.seller_price, values.unallocated_percent, values.unallocated_amount, values.unallocated_basis
    )
    clicks = advertising(
        values.turnover, values.buyout_percent, values.click_drr, values.click_spend, values.click_basis
    )
    orders = advertising(
        values.turnover, values.buyout_percent, values.order_drr, values.order_spend, values.order_basis
    )
    if values.vat_basis == OzonScenarioBasis.PERCENT:
        vat_amount = (
            Decimal(0)
            if values.vat_percent == 0
            else values.buyer_price * values.vat_percent / (OZON_SCENARIO.percent_factor + values.vat_percent)
            if values.buyer_price is not None and values.vat_percent is not None
            else None
        )
        vat = CalculationPair(percent=values.vat_percent, amount=rounded(vat_amount))
    else:
        if (
            values.vat_amount is not None
            and values.buyer_price is not None
            and values.vat_amount > 0
            and values.vat_amount >= values.buyer_price
        ):
            raise ValueError("VAT must be lower than buyer price")
        vat_base = (
            values.buyer_price - values.vat_amount
            if values.buyer_price is not None and values.vat_amount is not None
            else None
        )
        vat = pair(vat_base, values.vat_percent, values.vat_amount, values.vat_basis)
        vat_amount = vat.amount
    usn_base = (
        values.buyer_price - vat_amount if values.buyer_price is not None and vat_amount is not None else None
    )
    usn = (
        CalculationPair(percent=values.usn_percent, amount=Decimal(0))
        if values.usn_basis == OzonScenarioBasis.PERCENT and (values.usn_percent == 0 or usn_base == 0)
        else pair(usn_base, values.usn_percent, values.usn_amount, values.usn_basis)
    )
    calculated = OzonCalculatorValues.model_validate(
        values.model_dump()
        | {
            "acquiring_percent": acquiring.percent,
            "acquiring_amount": acquiring.amount,
            "team_percent": team.percent,
            "team_amount": team.amount,
            "unallocated_percent": unallocated.percent,
            "unallocated_amount": unallocated.amount,
            "vat_percent": vat.percent,
            "vat_amount": vat.amount,
            "usn_percent": usn.percent,
            "usn_amount": usn.amount,
            "click_drr": clicks.percent,
            "click_spend": clicks.amount,
            "order_drr": orders.percent,
            "order_spend": orders.amount,
            "other_spend": other_spend,
            "other_drr": other_drr,
            "other_breakdown": other_breakdown,
        }
    )
    spend = (
        clicks.amount + orders.amount + other_spend
        if clicks.amount is not None and orders.amount is not None and other_spend is not None
        else None
    )
    total_drr = advertising(
        values.turnover, values.buyout_percent, None, spend, OzonScenarioBasis.AMOUNT
    ).percent
    advertising_amount = (
        values.advertising_amount
        if values.advertising_manual
        else advertising_per_unit(
            values.advertising_week.spend,
            Decimal(values.advertising_week.orders_count)
            if values.advertising_week.orders_count is not None
            else None,
            values.buyout_percent,
        )
        if values.advertising_week is not None and values.advertising_week.complete
        else None
    )
    inputs = OzonMarginInputs.model_validate(
        {
            key: advertising_amount if key == "advertising_amount" else getattr(calculated, key)
            for key in OzonMarginInputs.model_fields
        }
    )
    return calculated.model_copy(
        update={
            "total_drr": total_drr,
            "advertising_amount": advertising_amount,
            "result": unit_margin(inputs),
        }
    )


def calculate(request: OzonCalculatorRequest) -> OzonCalculatorValues:
    values = request.values
    for edit in request.edits:
        changes: dict[str, object] = {edit.field.value: edit.value}
        if edit.field == OzonScenarioField.OTHER_SPEND:
            changes["other_breakdown"] = None
        if edit.field == OzonScenarioField.ADVERTISING_AMOUNT:
            changes["advertising_manual"] = True
        elif edit.field == OzonScenarioField.BUYOUT:
            changes["advertising_manual"] = False
        match edit.field:
            case OzonScenarioField.CLICK_DRR | OzonScenarioField.CLICK_SPEND:
                changes["click_basis"] = (
                    OzonScenarioBasis.PERCENT
                    if edit.field == OzonScenarioField.CLICK_DRR
                    else OzonScenarioBasis.AMOUNT
                )
            case OzonScenarioField.ORDER_DRR | OzonScenarioField.ORDER_SPEND:
                changes["order_basis"] = (
                    OzonScenarioBasis.PERCENT
                    if edit.field == OzonScenarioField.ORDER_DRR
                    else OzonScenarioBasis.AMOUNT
                )
            case OzonScenarioField.ACQUIRING_PERCENT | OzonScenarioField.ACQUIRING_AMOUNT:
                changes["acquiring_basis"] = (
                    OzonScenarioBasis.PERCENT
                    if edit.field == OzonScenarioField.ACQUIRING_PERCENT
                    else OzonScenarioBasis.AMOUNT
                )
            case OzonScenarioField.TEAM_PERCENT | OzonScenarioField.TEAM_AMOUNT:
                changes["team_basis"] = (
                    OzonScenarioBasis.PERCENT
                    if edit.field == OzonScenarioField.TEAM_PERCENT
                    else OzonScenarioBasis.AMOUNT
                )
            case OzonScenarioField.UNALLOCATED_PERCENT | OzonScenarioField.UNALLOCATED_AMOUNT:
                changes["unallocated_basis"] = (
                    OzonScenarioBasis.PERCENT
                    if edit.field == OzonScenarioField.UNALLOCATED_PERCENT
                    else OzonScenarioBasis.AMOUNT
                )
            case OzonScenarioField.VAT_PERCENT | OzonScenarioField.VAT_AMOUNT:
                changes["vat_basis"] = (
                    OzonScenarioBasis.PERCENT
                    if edit.field == OzonScenarioField.VAT_PERCENT
                    else OzonScenarioBasis.AMOUNT
                )
            case OzonScenarioField.USN_PERCENT | OzonScenarioField.USN_AMOUNT:
                changes["usn_basis"] = (
                    OzonScenarioBasis.PERCENT
                    if edit.field == OzonScenarioField.USN_PERCENT
                    else OzonScenarioBasis.AMOUNT
                )
        values = OzonCalculatorValues.model_validate(values.model_dump() | changes)
    return recalculate(values)
