from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from app.core.constants import OZON_LOGISTICS, TIME, OzonLogisticsPart, OzonLogisticsState, OzonReturnSchema
from app.dto.common import DtoModel
from app.dto.ozon_logistics_volume import LogisticsVolumeEstimate, LogisticsVolumeScenario
from app.dto.ozon_orders import Identifier, OptionalDate, OzonPayload, OzonPosting, Quantity

Money = Annotated[Decimal, Field(allow_inf_nan=False)]
ScenarioMoney = Annotated[Decimal, Field(ge=-1_000_000_000, le=1_000_000_000, allow_inf_nan=False)]


class LogisticsPeriod(DtoModel):
    day: date
    start: date
    end: date

    @classmethod
    def for_day(cls, day: date) -> Self:
        return cls(
            day=day, start=day - timedelta(days=OZON_LOGISTICS.window_days), end=day - timedelta(days=1)
        )

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if self.end != self.day - timedelta(days=1) or self.start != self.day - timedelta(
            days=OZON_LOGISTICS.window_days
        ):
            raise ValueError("Logistics requires the previous 30 completed Moscow days")
        return self


class LogisticsAccrued(OzonPayload):
    amount: Money
    currency: str


class LogisticsAccrual(OzonPayload):
    type_id: int
    sku: Identifier = ""
    quantity: Quantity | None = None
    accrued: LogisticsAccrued
    accrual_date: date


class PostingAccruals(OzonPayload):
    posting_number: Identifier
    accruals: tuple[LogisticsAccrual, ...]


class AccrualsPage(OzonPayload):
    posting_accruals: tuple[PostingAccruals, ...]


class AccrualsRequest(DtoModel):
    posting_numbers: tuple[str, ...]


class AccrualType(OzonPayload):
    id: int
    name: str


class AccrualTypesPage(OzonPayload):
    accrual_types: tuple[AccrualType, ...]


class ReturnProduct(OzonPayload):
    sku: Identifier
    offer_id: str
    quantity: Quantity


class ReturnLogistic(OzonPayload):
    return_date: OptionalDate = None


class LogisticsReturn(OzonPayload):
    id: int
    source_id: int | None = None
    posting_number: Identifier
    type: str
    product: ReturnProduct
    logistic: ReturnLogistic


class ReturnsPage(OzonPayload):
    returns: tuple[LogisticsReturn, ...]
    has_next: StrictBool


class ReturnsFilter(DtoModel):
    return_schema: OzonReturnSchema | None = OzonReturnSchema.FBO


class ReturnsRequest(DtoModel):
    filter: ReturnsFilter = ReturnsFilter()
    limit: int = OZON_LOGISTICS.returns_page_size
    last_id: int = 0


class LogisticsDimensions(OzonPayload):
    offer_id: str
    depth: Money | None = None
    width: Money | None = None
    height: Money | None = None
    dimension_unit: str | None = None

    def volume(self) -> Decimal | None:
        if self.dimension_unit != "mm" or any(
            value is None or value <= 0 for value in (self.depth, self.width, self.height)
        ):
            return None
        assert self.depth is not None and self.width is not None and self.height is not None
        return self.depth * self.width * self.height / Decimal(1_000_000)


class AttributesPage(OzonPayload):
    result: tuple[LogisticsDimensions, ...]
    last_id: str | int | None = None


class AttributesFilter(DtoModel):
    offer_id: tuple[str, ...]
    visibility: Literal["ALL"] = "ALL"


class AttributesRequest(DtoModel):
    filter: AttributesFilter
    limit: int = OZON_LOGISTICS.attributes_batch_size


class LogisticsEvidence(DtoModel):
    period: LogisticsPeriod
    postings: tuple[OzonPosting, ...]
    accruals: tuple[PostingAccruals, ...]
    returns: tuple[LogisticsReturn, ...]
    types: tuple[AccrualType, ...]
    dimensions: tuple[LogisticsDimensions, ...] = ()
    dimensions_available: bool = False


class LogisticsCharge(DtoModel):
    type_id: int
    name: str
    part: OzonLogisticsPart
    amount: Money
    charged_units: int


class LogisticsInputs(DtoModel):
    forward: Money | None = None
    reverse: Money | None = None
    handling: Money | None = None
    last_mile: Money | None = None
    extra: Money | None = None
    markup: Money = Decimal(0)
    buyout_percent: Money | None = None


class LogisticsScenario(DtoModel):
    forward: ScenarioMoney | None = None
    reverse: ScenarioMoney | None = None
    handling: ScenarioMoney | None = None
    last_mile: ScenarioMoney | None = None
    extra: ScenarioMoney | None = None
    markup: ScenarioMoney = Decimal(0)
    buyout_percent: Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)] | None = None
    total: ScenarioMoney | None = None
    volume: LogisticsVolumeScenario | None = None


class LogisticsCalculation(DtoModel):
    total: Money | None
    modeled: Money | None
    manual: bool = False
    volume: LogisticsVolumeEstimate | None = None
    inputs: LogisticsInputs | None = None


class LogisticsSnapshot(DtoModel):
    store_slug: str
    article: str
    period: LogisticsPeriod
    observed_at: datetime
    version: int = OZON_LOGISTICS.formula_version
    state: OzonLogisticsState
    inputs: LogisticsInputs
    actual_per_unit: Money | None
    modeled_per_unit: Money | None
    total_expenses: Money
    volume_l: Money | None = None
    ordered_units: int
    paid_units: int
    forward_units: int
    reverse_units: int
    handling_units: int
    received_units: int
    returned_units: int
    retained_units: int
    cancelled_units: int
    pending_paid_units: int
    unpaid_units: int
    charges: tuple[LogisticsCharge, ...]
    issues: tuple[str, ...] = ()


class LogisticsData(DtoModel):
    snapshot: LogisticsSnapshot | None = None
    stale: bool = False
    error: str | None = None
    attempted_at: datetime | None = None


class LogisticsStoreSnapshot(DtoModel):
    store_slug: str
    period: LogisticsPeriod
    observed_at: datetime
    products: tuple[LogisticsSnapshot, ...]


class LogisticsSyncResult(DtoModel):
    stores: int = 0
    products: int = 0
    skipped: int = 0
    error: str | None = None


class LogisticsStoreData(DtoModel):
    store_slug: str
    products: tuple[LogisticsSnapshot, ...] = ()
    attempted_at: datetime | None = None
    error: str | None = None


def moscow_day(moment: datetime) -> date:
    return moment.astimezone(TIME.moscow_timezone).date()
