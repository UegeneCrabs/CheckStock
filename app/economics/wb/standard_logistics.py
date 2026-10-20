"""Delivery and repeat-trip estimate, independent of calculator and persistence."""

from app.dto.wb_standard_costs import CalculatorProduct, LogisticsReturns, ReportRow
from app.economics.wb.standard_costs import number

# Explicit user-approved defaults; not rates downloaded from WB.
RETURN_BASE_RUB = 46
RETURN_EXTRA_LITER_RUB = 14


def logistics_with_returns(row: ReportRow, product: CalculatorProduct) -> LogisticsReturns:
    """Expected delivery cost per buyout; return rates are explicit report defaults."""
    forward = number(row.logistics)
    volume = number(row.volume)
    buyout = number(product.details.buyout_percent)
    reverse = (
        RETURN_BASE_RUB + RETURN_EXTRA_LITER_RUB * max(volume - 1, 0)
        if volume is not None and volume > 0
        else None
    )
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
        return_source="default",
        extra_forward=forward * retries if forward is not None and retries is not None else None,
        reverse_per_buyout=reverse * retries
        if reverse is not None and retries is not None
        else (0 if retries == 0 else None),
    )
