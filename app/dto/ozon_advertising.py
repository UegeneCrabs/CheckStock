from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Self

from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.core.constants import (
    OZON_ADVERTISING,
    AdvertisingAllocation,
    AdvertisingError,
    AdvertisingKind,
    AdvertisingSource,
    AdvertisingState,
    HTTPMethod,
)
from app.dto.common import DtoModel


def parse_money(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(AdvertisingError.INVALID_AMOUNT)
    try:
        amount = Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except InvalidOperation:
        raise ValueError(AdvertisingError.INVALID_AMOUNT) from None
    if not amount.is_finite():
        raise ValueError(AdvertisingError.INVALID_AMOUNT)
    return amount


def parse_day(value: object) -> date:
    if isinstance(value, datetime):
        raise ValueError(AdvertisingError.INVALID_DATE)
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(AdvertisingError.INVALID_DATE)
    try:
        return date.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, OZON_ADVERTISING.date_format).date()


def parse_counter(value: object) -> int:
    amount = parse_money(value)
    if amount < 0 or amount != amount.to_integral_value():
        raise ValueError(AdvertisingError.INVALID_COUNTER)
    return int(amount)


Money = Annotated[Decimal, BeforeValidator(parse_money)]
ReportDay = Annotated[date, BeforeValidator(parse_day)]
Counter = Annotated[int, BeforeValidator(parse_counter)]


class PerformanceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, coerce_numbers_to_str=True)


class PerformanceCredentials(DtoModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1, repr=False)


class PerformanceToken(PerformanceResponse):
    access_token: str = Field(min_length=1, repr=False)
    expires_in: int = Field(gt=0)


class PerformanceCampaign(PerformanceResponse):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(min_length=1)
    adv_object_type: str = Field(alias="advObjectType", min_length=1)
    payment_type: str = Field(default="", validation_alias=AliasChoices("PaymentType", "paymentType"))
    placement: tuple[str, ...] | str = ()


class CampaignPage(PerformanceResponse):
    campaigns: tuple[PerformanceCampaign, ...] = Field(alias="list")


class CampaignInventory(DtoModel):
    campaigns: tuple[PerformanceCampaign, ...]


class PerformanceErrorResponse(PerformanceResponse):
    error: str = ""


class CampaignExpense(PerformanceResponse):
    campaign_id: str = Field(alias="id", min_length=1)
    day: ReportDay = Field(alias="date")
    amount: Money = Field(alias="moneySpent")


class DailyCampaignReport(PerformanceResponse):
    rows: tuple[CampaignExpense, ...]


class ProductTraffic(PerformanceResponse):
    campaign_id: str = Field(alias="campaignId", min_length=1)
    sku: str = Field(min_length=1)
    day: ReportDay = Field(alias="date")
    impressions: Counter = Field(alias="views")
    clicks: Counter
    amount: Money = Field(alias="expense")


class ProductTrafficReport(PerformanceResponse):
    rows: tuple[ProductTraffic, ...]


class CampaignProductExpense(PerformanceResponse):
    day: ReportDay = Field(alias="date")
    sku: str | None = None
    amount: Money = Field(alias="moneySpent")
    impressions: Counter | None = Field(alias="views", default=0)
    clicks: Counter | None = 0


class CampaignTotals(PerformanceResponse):
    amount: Money = Field(alias="moneySpent", default=Decimal(0))
    corrections: Money = Decimal(0)


class CampaignReport(PerformanceResponse):
    rows: tuple[CampaignProductExpense, ...]
    totals: CampaignTotals


class CampaignReportEnvelope(PerformanceResponse):
    report: CampaignReport


class SelectedOrderExpense(PerformanceResponse):
    day: ReportDay = Field(alias="date")
    advertised_sku: str = Field(alias="advSku", min_length=1)
    purchased_sku: str = Field(alias="sku", min_length=1)
    order_id: str = Field(alias="orderId", min_length=1)
    orders_source: str = Field(alias="ordersSource", min_length=1)
    amount: Money = Field(alias="moneySpent")


class SelectedOrdersReport(PerformanceResponse):
    rows: tuple[SelectedOrderExpense, ...]
    over_expenses: Money = Field(alias="overExpenses", default=Decimal(0))


class AllProductsOrderExpense(PerformanceResponse):
    day: ReportDay = Field(alias="date")
    advertised_sku: str = Field(alias="adv_sku", min_length=1)
    purchased_sku: str = Field(alias="sku", min_length=1)
    order_id: str = Field(alias="order_id", min_length=1)
    amount: Money = Field(alias="adv_money_spent")


class AllProductsOrdersReport(PerformanceResponse):
    rows: tuple[AllProductsOrderExpense, ...]

    @model_validator(mode="before")
    @classmethod
    def empty_report(cls, value: object) -> object:
        return {"rows": []} if value == {} else value


class ReportTicket(PerformanceResponse):
    uuid: str = Field(alias="UUID", min_length=1)


class ReportStatus(PerformanceResponse):
    state: str = Field(min_length=1)


class AdvertisingPeriod(DtoModel):
    start: date
    end: date

    @model_validator(mode="after")
    def chronological(self) -> Self:
        if self.end < self.start:
            raise ValueError(AdvertisingError.INVALID_PERIOD)
        return self


class PerformanceReportRequest(DtoModel):
    method: HTTPMethod
    path: str
    period: AdvertisingPeriod
    campaigns: tuple[str, ...] = ()
    refresh_slot: str | None = None


class SavedPerformanceReport(DtoModel):
    uuid: str
    pending: bool


class PerformanceRequestEvent(DtoModel):
    store_slug: str
    method: HTTPMethod
    path: str
    period: AdvertisingPeriod | None = None
    campaign_count: int = 0
    export_units: int = 0
    status_code: int | None = None
    error: AdvertisingError | None = None
    attempted_at: datetime


class AdvertisingImport(DtoModel):
    source: AdvertisingSource
    period: AdvertisingPeriod


class AdvertisingFact(DtoModel):
    day: date
    kind: AdvertisingKind
    amount: Money
    impressions: Counter | None = None
    clicks: Counter | None = None
    raw_type: str
    campaign_id: str | None = None
    advertised_sku: str | None = None
    purchased_sku: str | None = None
    order_id: str | None = None
    article: str | None = None
    allocation: AdvertisingAllocation = AdvertisingAllocation.UNALLOCATED


class AdvertisingCorrection(DtoModel):
    campaign_id: str
    kind: AdvertisingKind
    amount: Money


class AdvertisingSnapshot(DtoModel):
    store_slug: str
    source: AdvertisingSource
    period: AdvertisingPeriod
    facts: tuple[AdvertisingFact, ...] = ()
    campaigns: tuple[PerformanceCampaign, ...] = ()
    corrections: tuple[AdvertisingCorrection, ...] = ()
    over_expenses: Money = Decimal(0)
    unavailable_kinds: tuple[AdvertisingKind, ...] = ()
    traffic_collected: bool = False
    includes_cpc: bool = True
    synced_at: datetime
    finalized: bool = True
    refresh_slot: str | None = None


class AdvertisingSourceStatus(DtoModel):
    store_slug: str
    source: AdvertisingSource
    period: AdvertisingPeriod
    state: AdvertisingState
    attempted_at: datetime
    error: str | None = None
    snapshot: AdvertisingSnapshot | None = None
    refresh_slot: str | None = None


class AdvertisingSourceDay(DtoModel):
    day: date
    source: AdvertisingSource
    state: AdvertisingState
    snapshot: AdvertisingSnapshot | None = None
    stale: bool = False
    error: str | None = None


class AdvertisingDay(DtoModel):
    day: date
    amount: Money | None


class AdvertisingDailyExpense(DtoModel):
    kind: AdvertisingKind
    amount: Money | None
    state: AdvertisingState
    stale: bool = False
    raw_types: tuple[str, ...] = ()


class AdvertisingSpendDay(DtoModel):
    day: date
    amount: Money | None
    breakdown: tuple[AdvertisingDailyExpense, ...]
    complete: bool
    stale: bool


class AdvertisingSpendSummary(DtoModel):
    period: AdvertisingPeriod
    amount: Money | None
    days: tuple[AdvertisingSpendDay, ...]
    complete: bool
    stale: bool
    missing_dates: tuple[date, ...] = ()
    partial_dates: tuple[date, ...] = ()


class AdvertisingBreakdown(DtoModel):
    kind: AdvertisingKind
    state: AdvertisingState
    amount: Money | None
    days: tuple[AdvertisingDay, ...]
    allocations: tuple[AdvertisingAllocation, ...] = ()
    raw_types: tuple[str, ...] = ()
    synced_at: datetime | None = None
    stale: bool = False


class AdvertisingWindowAdjustment(DtoModel):
    source: AdvertisingSource
    period: AdvertisingPeriod
    corrections: tuple[AdvertisingCorrection, ...]
    over_expenses: Money
    synced_at: datetime


class StoreAdvertisingBreakdown(DtoModel):
    store_slug: str
    period: AdvertisingPeriod
    unallocated: tuple[AdvertisingBreakdown, ...]
    corrections: tuple[AdvertisingCorrection, ...] = ()
    over_expenses: Money = Decimal(0)
    adjustments: tuple[AdvertisingWindowAdjustment, ...] = ()


class AdvertisingSyncResult(DtoModel):
    stores: tuple[str, ...]
    failed_stores: tuple[str, ...] = ()
    error: str | None = None
