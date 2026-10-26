from datetime import date
from decimal import Decimal

from app.core.constants import OZON_LOGISTICS, OZON_SCENARIO, OzonVolumeState
from app.dto.ozon_logistics import LogisticsCalculation, LogisticsInputs, LogisticsScenario
from app.dto.ozon_logistics_volume import (
    LogisticsTariffCatalog,
    LogisticsTariffOptions,
    LogisticsVolumeEstimate,
    LogisticsVolumeScenario,
)
from app.ozon.logistics_normalizer import calculate as calculate_inputs


def tariff_options(catalog: LogisticsTariffCatalog | None, day: date) -> LogisticsTariffOptions:
    if catalog is None:
        return LogisticsTariffOptions(available=False)
    return LogisticsTariffOptions(
        available=catalog.effective_from <= day <= catalog.effective_until,
        origins=tuple(sorted({route.origin for route in catalog.routes})),
        destinations=tuple(sorted({route.destination for route in catalog.routes})),
        effective_from=catalog.effective_from,
        effective_until=catalog.effective_until,
        source_url=catalog.source_url,
    )


def estimate_volume(
    scenario: LogisticsVolumeScenario,
    catalog: LogisticsTariffCatalog | None,
    day: date,
    needs_reverse: bool,
) -> LogisticsVolumeEstimate:
    if scenario.volume_l is None:
        return LogisticsVolumeEstimate(state=OzonVolumeState.VOLUME_MISSING)
    if catalog is None or not catalog.effective_from <= day <= catalog.effective_until:
        return LogisticsVolumeEstimate(state=OzonVolumeState.TARIFF_UNAVAILABLE, volume_l=scenario.volume_l)
    base = LogisticsVolumeEstimate(
        state=OzonVolumeState.AVAILABLE,
        volume_l=scenario.volume_l,
        effective_from=catalog.effective_from,
        effective_until=catalog.effective_until,
    )
    if scenario.seller_price is None:
        return base.model_copy(update={"state": OzonVolumeState.PRICE_MISSING})
    if scenario.origin is None or scenario.destination is None:
        return base.model_copy(update={"state": OzonVolumeState.ROUTE_MISSING})
    index = next(
        index for index, upper in enumerate(catalog.upper_l) if upper is None or scenario.volume_l <= upper
    )
    routes = {(route.origin, route.destination): route for route in catalog.routes}
    forward = routes.get((scenario.origin, scenario.destination))
    reverse = routes.get((scenario.destination, scenario.return_cluster or scenario.destination))
    if forward is None or (needs_reverse and reverse is None):
        return base.model_copy(update={"state": OzonVolumeState.ROUTE_UNAVAILABLE})
    cheap = scenario.seller_price <= OZON_LOGISTICS.tariff_price_limit
    return base.model_copy(
        update={
            "lower_l": catalog.upper_l[index - 1] if index else Decimal(0),
            "upper_l": catalog.upper_l[index],
            "forward": (forward.under_300 if cheap else forward.over_300)[index],
            "reverse": (reverse.under_300 if cheap else reverse.over_300)[index] if reverse else None,
        }
    )


def calculate(
    values: LogisticsScenario, catalog: LogisticsTariffCatalog | None, day: date
) -> LogisticsCalculation:
    if values.volume is None:
        return calculate_inputs(values)
    needs_reverse = values.buyout_percent is None or values.buyout_percent < OZON_SCENARIO.percent_factor
    estimate = estimate_volume(values.volume, catalog, day, needs_reverse)
    inputs = LogisticsInputs(
        forward=values.forward if values.volume.forward_manual else estimate.forward,
        reverse=values.reverse if values.volume.reverse_manual else estimate.reverse,
        handling=values.handling,
        last_mile=values.last_mile,
        extra=values.extra,
        markup=values.markup,
        buyout_percent=values.buyout_percent,
    )
    if estimate.state != OzonVolumeState.AVAILABLE:
        return LogisticsCalculation(
            total=values.total, modeled=None, manual=values.total is not None, volume=estimate, inputs=inputs
        )
    scenario = LogisticsScenario(**inputs.model_dump(), total=values.total)
    calculated = calculate_inputs(scenario)
    return calculated.model_copy(update={"volume": estimate, "inputs": inputs})
