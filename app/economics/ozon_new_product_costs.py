from decimal import Decimal

from app.core.constants import OZON_LOGISTICS, OZON_NEW_PRODUCT_LOGISTICS
from app.dto.ozon_logistics import LogisticsInputs, LogisticsScenario
from app.dto.ozon_new_product_costs import OzonNewLogisticsEstimate, OzonNewLogisticsSource
from app.ozon.logistics_normalizer import modeled_cost


def estimate_logistics(
    source: OzonNewLogisticsSource,
    seller_price: Decimal | None,
    buyout_percent: Decimal | None,
    overrides: LogisticsScenario | None,
) -> OzonNewLogisticsEstimate:
    settings = OZON_NEW_PRODUCT_LOGISTICS
    price_limit = Decimal(OZON_LOGISTICS.tariff_price_limit)
    band = (
        next((row for row in settings.bands if source.volume_l <= row.upper_l), None)
        if source.volume_l
        else None
    )
    share = source.locality.local_share
    up_to_limit = seller_price <= price_limit if seller_price is not None else None
    local = nonlocal_tariff = forward = None
    if band is not None and up_to_limit is not None:
        local = band.local_up_to_300 if up_to_limit else band.local_over_300
        nonlocal_tariff = band.nonlocal_up_to_300 if up_to_limit else band.nonlocal_over_300
        if share is not None:
            forward = local * share + nonlocal_tariff * (1 - share)
    edits = overrides or LogisticsScenario()
    inputs = LogisticsInputs(
        forward=edits.forward if edits.forward is not None else forward,
        reverse=edits.reverse if edits.reverse is not None else forward,
        handling=edits.handling if edits.handling is not None else settings.return_handling,
        last_mile=edits.last_mile if edits.last_mile is not None else settings.last_mile_estimate,
        extra=edits.extra if edits.extra is not None else Decimal(0),
        markup=edits.markup,
        buyout_percent=edits.buyout_percent if edits.buyout_percent is not None else buyout_percent,
    )
    modeled = modeled_cost(inputs)
    return OzonNewLogisticsEstimate(
        inputs=inputs,
        total=edits.total if edits.total is not None else modeled,
        modeled=modeled,
        local_tariff=local,
        nonlocal_tariff=nonlocal_tariff,
        upper_l=band.upper_l if band else None,
        price_limit=price_limit,
        up_to_price_limit=up_to_limit,
    )
