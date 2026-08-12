from app.dto.stores import StoreCollection
from app.dto.unit_economics import (
    CalculationDefaults,
    FulfillmentRate,
    PersistedFulfillmentRates,
    UnitEconomicsConfiguration,
    UnitEconomicsStore,
)

DEFAULT_TAX_RATE = 6

TAX_RATES = {
    "rimili": 6,
    "tris": 8,
    "rockkiddo": 6,
    "trusthome": 8,
    "sokoloff": 8,
    "gogol": 6,
    "toyka": 7,
}

CALCULATION_DEFAULTS = {
    "acquiring": 3.8,
    "advertising": 10,
    "overhead": 1,
    "team": 3,
    "contribution": 0,
    "spp": 26,
    "stock_rate": 18.5,
    "stock_days": 56,
}


def build_configuration(
    stores: StoreCollection,
    rates: PersistedFulfillmentRates,
) -> UnitEconomicsConfiguration:
    return UnitEconomicsConfiguration(
        stores=tuple(
            UnitEconomicsStore(
                slug=item.slug,
                name=item.store.name,
                tax=TAX_RATES.get(item.slug, DEFAULT_TAX_RATE),
            )
            for item in stores.root
        ),
        fulfillments=tuple(
            FulfillmentRate(
                name=row.name,
                storage=row.storage_per_m3_day,
                accept=row.acceptance_per_unit,
                fulfillment=row.fulfillment_per_unit,
            )
            for row in rates.root
        ),
        defaults=CalculationDefaults(**CALCULATION_DEFAULTS),
    )
