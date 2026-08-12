from datetime import datetime

from pydantic import Field, RootModel

from app.dto.common import DtoModel


class PersistedFulfillmentRate(DtoModel):
    name: str = Field(min_length=1, max_length=200)
    storage_per_m3_day: float | None = Field(default=None, ge=0, le=1_000_000)
    acceptance_per_unit: float | None = Field(default=None, ge=0, le=1_000_000)
    fulfillment_per_unit: float | None = Field(default=None, ge=0, le=1_000_000)
    updated_at: datetime | None = None


class PersistedFulfillmentRates(RootModel[tuple[PersistedFulfillmentRate, ...]]):
    root: tuple[PersistedFulfillmentRate, ...] = ()


class UnitEconomicsStore(DtoModel):
    slug: str
    name: str
    tax: int = Field(ge=0, le=100)


class FulfillmentRate(DtoModel):
    name: str
    storage: float | None = None
    accept: float | None = None
    fulfillment: float | None = None


class CalculationDefaults(DtoModel):
    acquiring: float
    advertising: float
    overhead: float
    team: float
    contribution: float
    spp: float
    stock_rate: float
    stock_days: int


class UnitEconomicsConfiguration(DtoModel):
    stores: tuple[UnitEconomicsStore, ...]
    fulfillments: tuple[FulfillmentRate, ...]
    defaults: CalculationDefaults


class UnitEconomicsCalculationRequest(DtoModel):
    store: str = Field(default="tris", min_length=1, max_length=100)


class FulfillmentRateInput(DtoModel):
    name: str = Field(min_length=1, max_length=200)
    storage: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    accept: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    fulfillment: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)


class FulfillmentRateInputs(RootModel[tuple[FulfillmentRateInput, ...]]):
    root: tuple[FulfillmentRateInput, ...] = Field(min_length=1)


class FulfillmentRatesRequest(DtoModel):
    rates: FulfillmentRateInputs


class FulfillmentNames(RootModel[tuple[str, ...]]):
    root: tuple[str, ...] = ()


class SaveFulfillmentRatesCommand(DtoModel):
    rates: FulfillmentRateInputs
    updated_at: datetime


class FulfillmentRatesResult(DtoModel):
    rates: tuple[FulfillmentRate, ...]
    updated_at: datetime
