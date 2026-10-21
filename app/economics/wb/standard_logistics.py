"""Delivery and repeat-trip estimate, independent of calculator and persistence."""

from app.dto.wb_standard_costs import CalculatorProduct, LogisticsReturns, ReportRow, ReturnVolumeTerm
from app.economics.wb.standard_costs import number
from app.economics.wb.standard_delivery_tariffs import (
    RETURN_BASE_RUB,
    RETURN_EXTRA_LITER_RUB,
    volume_tariff,
)


def logistics_with_returns(row: ReportRow, product: CalculatorProduct) -> LogisticsReturns:
    """Expected delivery cost per buyout; return rates are explicit report defaults."""
    forward = number(row.logistics)
    calculation = row.logistics_calculation
    # Use exactly the same volumes and weights as the forward trip, before averaging tariffs.
    if calculation and calculation.terms:
        terms = tuple(
            ReturnVolumeTerm(volume=t.volume, quantity=t.quantity, rate=volume_tariff(t.volume))
            for t in calculation.terms
            if t.quantity > 0
        )
        source = "stock" if calculation.basis == "stock" else "card"
    else:
        terms = (ReturnVolumeTerm(volume=number(row.volume), quantity=1, rate=volume_tariff(row.volume)),)
        source = "card"
    weight = sum(t.quantity for t in terms)
    reverse = (
        sum(t.rate * t.quantity for t in terms) / weight
        if weight > 0 and all(t.rate is not None for t in terms)
        else None
    )
    volumes = {t.volume for t in terms}
    volume = next(iter(volumes)) if len(volumes) == 1 else None
    buyout = number(product.details.buyout_percent)
    retries = (100 - buyout) / buyout if buyout is not None and 0 < buyout <= 100 else None
    missing = []
    if forward is None or forward < 0:
        missing.append("Нет тарифа прямой доставки")
    if retries is None:
        missing.append("Нужен процент выкупа больше 0 и не больше 100")
    if reverse is None and retries != 0:
        missing.append("Нет объёма товара для обратной доставки")
    total = None if missing else forward + (forward + (reverse or 0)) * retries
    return LogisticsReturns(
        forward=forward,
        reverse=reverse,
        volume=volume,
        buyout_percent=buyout,
        retries=retries,
        total=total,
        missing=missing,
        return_base=RETURN_BASE_RUB,
        return_liter=RETURN_EXTRA_LITER_RUB,
        return_source="wb_volume_tariff",
        volume_source=source,
        volume_terms=terms,
        extra_forward=forward * retries if forward is not None and retries is not None else None,
        reverse_per_buyout=reverse * retries
        if reverse is not None and retries is not None
        else (0 if retries == 0 else None),
    )
