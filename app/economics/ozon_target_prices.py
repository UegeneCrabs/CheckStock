from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.constants import OZON_REPORTS, OZON_SCENARIO, OzonPriceState, OzonTargetIssue
from app.dto.ozon_calculator import OzonCalculatorValues
from app.dto.ozon_economics import OzonProduct
from app.dto.ozon_reports import (
    OzonTargetCalculation,
    OzonTargetPreview,
    OzonTargetRecord,
    OzonTargetRow,
    OzonTargetWeekly,
)
from app.economics.ozon_calculator import recalculate, rounded
from app.economics.ozon_reports import number


class OzonTargetUnavailable(Exception):
    def __init__(self, issue: OzonTargetIssue, missing: tuple[str, ...] = ()):
        self.issue = issue
        self.missing = missing
        super().__init__(issue)


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonTargetSolution:
    values: OzonCalculatorValues
    actual_roi: Decimal


def calculator_values(product: OzonProduct) -> OzonCalculatorValues:
    data, cabinet, details = product.calculator, product.cabinet, product.details

    def price(key: str) -> Decimal | None:
        value = getattr(product.price, key)
        return value.amount if value.state != OzonPriceState.CONFLICT else None

    return OzonCalculatorValues(
        advertising_week=data.advertising_week if data else None,
        seller_price=price("seller_price"),
        buyer_price=price("black_price"),
        wallet_price=price("green_price"),
        commission_amount=product.commission.amount,
        logistics_amount=rounded(product.logistics.snapshot.actual_per_unit)
        if product.logistics.snapshot
        else None,
        storage_amount=rounded(product.storage.snapshot.per_unit) if product.storage.snapshot else None,
        buyout_percent=Decimal(str(data.buyout_percent))
        if data and data.buyout_percent is not None
        else None,
        turnover=data.turnover if data else None,
        orders_count=Decimal(data.orders_count) if data and data.orders_count is not None else None,
        click_spend=data.click_spend if data else None,
        order_spend=data.order_spend if data else None,
        other_spend=data.other_spend if data else None,
        purchase=Decimal(str(details.purchase_cost)) if details.purchase_cost is not None else None,
        fulfillment=Decimal(str(details.fulfillment_cost)) if details.fulfillment_cost is not None else None,
        team_percent=Decimal(str(details.team_commission_percent))
        if details.team_commission_percent is not None
        else None,
        acquiring_percent=Decimal(str(cabinet.expenses.acquiring_percent))
        if cabinet and cabinet.expenses.acquiring_percent is not None
        else None,
        vat_percent=Decimal(str(cabinet.expenses.vat_percent))
        if cabinet and cabinet.expenses.vat_percent is not None
        else None,
        usn_percent=Decimal(str(cabinet.expenses.usn_percent))
        if cabinet and cabinet.expenses.usn_percent is not None
        else None,
    )


def solve(values: OzonCalculatorValues, drr: float, roi: float) -> OzonTargetSolution:
    seller, buyer, wallet = values.seller_price, values.buyer_price, values.wallet_price
    if (
        seller is None
        or seller <= 0
        or buyer is None
        or buyer <= 0
        or buyer > seller
        or wallet is None
        or wallet <= 0
        or wallet > buyer
    ):
        raise OzonTargetUnavailable(OzonTargetIssue.PRICES)
    purchase = rounded(values.purchase)
    if purchase is None or purchase <= 0:
        raise OzonTargetUnavailable(OzonTargetIssue.PURCHASE)
    if values.commission_amount is None:
        raise OzonTargetUnavailable(OzonTargetIssue.PARAMETERS, ("commission_amount",))
    buyer_ratio, wallet_ratio = buyer / seller, wallet / seller
    commission_ratio = values.commission_amount / seller
    target_profit = purchase * Decimal(str(roi)) / OZON_SCENARIO.percent_factor

    def at(cents: int) -> OzonCalculatorValues:
        price = Decimal(cents) / OZON_REPORTS.price_scale
        result = recalculate(
            values.model_copy(
                update={
                    "seller_price": price,
                    "buyer_price": rounded(price * buyer_ratio),
                    "wallet_price": rounded(price * wallet_ratio),
                    "commission_amount": rounded(price * commission_ratio),
                    "advertising_amount": rounded(price * Decimal(str(drr)) / OZON_SCENARIO.percent_factor),
                    "advertising_manual": True,
                }
            )
        )
        if result.result.margin is None:
            raise OzonTargetUnavailable(OzonTargetIssue.PARAMETERS, result.result.missing)
        return result

    low, high = 1, OZON_REPORTS.max_price * OZON_REPORTS.price_scale
    ceiling = at(high)
    if ceiling.result.margin is None or ceiling.result.margin < target_profit:
        raise OzonTargetUnavailable(OzonTargetIssue.UNREACHABLE)
    while low < high:
        middle = (low + high) // 2
        value = at(middle)
        if value.result.margin is not None and value.result.margin >= target_profit:
            high = middle
        else:
            low = middle + 1
    result = at(low)
    assert result.result.margin is not None
    actual_roi = result.result.margin / purchase * OZON_SCENARIO.percent_factor
    return OzonTargetSolution(values=result, actual_roi=actual_roi)


def target_row(
    product: OzonProduct, saved: OzonTargetRecord, preview: OzonTargetPreview | None = None
) -> OzonTargetRow:
    source = calculator_values(product)
    if preview and preview.values:
        source = OzonCalculatorValues.model_validate(
            source.model_dump() | preview.values.model_dump(exclude_unset=True)
        )
    current = recalculate(source)
    cabinet = product.cabinet
    target_drr = preview.target_drr_percent if preview else cabinet.target_drr_percent if cabinet else 0
    target_roi = preview.target_roi_percent if preview else cabinet.target_roi_percent if cabinet else 0
    week = source.advertising_week
    basis = product.advertising.drr_basis
    result = OzonTargetRow(
        store_slug=product.store_slug,
        store_name=product.store_name,
        article=product.article,
        name=product.name,
        image_url=product.image_url,
        code=product.tag_data.code,
        orders_amount=product.economics_7d.turnover,
        current_price=number(source.wallet_price),
        current_drr=basis.drr if basis else None,
        current_roi=number(current.result.roi),
        current_missing=current.result.missing,
        target_drr=target_drr,
        target_roi=target_roi,
        target_overridden=saved.target_drr_percent is not None or saved.target_roi_percent is not None,
        target_revision=saved.revision,
        weekly=OzonTargetWeekly(
            period_from=date.fromisoformat(product.economics_7d.period_from),
            period_to=date.fromisoformat(product.economics_7d.period_to),
            days=basis.coverage.days if basis else 0,
            spend=number(week.spend) if week else None,
            orders_amount=product.economics_7d.turnover,
            orders_count=week.orders_count if week else None,
            buyout_percent=number(source.buyout_percent),
            advertising_per_unit=number(current.advertising_amount),
            complete=bool(week and week.complete),
        ),
        source_values=source,
        issues=(OzonTargetIssue.PARTIAL,) if not week or not week.complete else (),
    )
    try:
        solution = solve(source, target_drr, target_roi)
    except OzonTargetUnavailable as error:
        return result.model_copy(update={"issues": (*result.issues, error.issue), "missing": error.missing})
    return result.model_copy(
        update={
            "target_price": number(solution.values.wallet_price),
            "target_retail_price": number(solution.values.seller_price),
            "target_spp_price": number(solution.values.buyer_price),
            "target_actual_roi": number(solution.actual_roi),
            "calculator_values": solution.values,
            "target_calculation": OzonTargetCalculation(
                margin=solution.values.result.margin, roi=solution.actual_roi, costs=solution.values
            ),
        }
    )
