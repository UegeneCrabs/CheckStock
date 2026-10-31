from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.constants import (
    OZON_ANALYTICS,
    TIME,
    OzonAnalyticsCommentKind,
    OzonAnalyticsReason,
    OzonAnalyticsRunState,
)
from app.dto.common import DtoModel
from app.dto.ozon_advertising import AdvertisingSourceStatus
from app.dto.ozon_cabinet import OzonBuyoutSnapshot, OzonCabinetForecast, OzonCabinetRecord
from app.dto.ozon_economics import OzonProduct, OzonTurnoverData
from app.dto.ozon_margin import OzonMarginSnapshot
from app.dto.ozon_prices import PriceValues

AnalyticsNumber = Annotated[float, Field(allow_inf_nan=False)]
AnalyticsCounter = Annotated[int, Field(strict=True, ge=0, le=OZON_ANALYTICS.max_counter)]
AnalyticsVersion = Annotated[int, Field(strict=True, ge=0, le=OZON_ANALYTICS.max_version)]
AnalyticsExternalId = Annotated[StrictInt, Field(gt=0)] | Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
AnalyticsWarehouseId = StrictInt | Annotated[str, Field(pattern=r"^-?[1-9][0-9]*$")]
AnalyticsCommentText = Annotated[
    str, StringConstraints(strip_whitespace=False, max_length=OZON_ANALYTICS.text_max_length)
]


class OzonAnalyticsModel(DtoModel):
    model_config = ConfigDict(allow_inf_nan=False)


class OzonAnalyticsForecastSettings(OzonCabinetForecast):
    revision: AnalyticsVersion = 0


class OzonAnalyticsWeekQuery(OzonAnalyticsModel):
    week: date | None = None

    def start(self, today: date) -> date:
        selected = self.week or today
        start = selected.fromordinal(selected.toordinal() - selected.weekday())
        if start < OZON_ANALYTICS.first_week or start > today:
            raise ValueError(OzonAnalyticsReason.INVALID_PERIOD)
        return start


class OzonAnalyticsSalesQuery(OzonAnalyticsModel):
    date_from: date | None = None
    date_to: date | None = None

    @model_validator(mode="after")
    def valid_dates(self) -> Self:
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError(OzonAnalyticsReason.INVALID_PERIOD)
        if self.date_from is not None and self.date_to is not None:
            if (
                self.date_from < OZON_ANALYTICS.first_sales_day
                or self.date_to < self.date_from
                or (self.date_to - self.date_from).days >= OZON_ANALYTICS.max_sales_days
                or self.date_to > datetime.now(TIME.moscow_timezone).date()
            ):
                raise ValueError(OzonAnalyticsReason.INVALID_PERIOD)
        return self


class OzonAnalyticsProductKey(OzonAnalyticsModel):
    store: str = Field(min_length=1, max_length=OZON_ANALYTICS.store_max_length)
    article: str = Field(min_length=1, max_length=OZON_ANALYTICS.article_max_length)


class OzonAnalyticsNoteCreate(OzonAnalyticsProductKey):
    day: date
    note: str = Field(min_length=1, max_length=OZON_ANALYTICS.text_max_length)


class OzonAnalyticsNoteUpdate(OzonAnalyticsModel):
    note: str = Field(min_length=1, max_length=OZON_ANALYTICS.text_max_length)
    previous_note: str = Field(min_length=1, max_length=OZON_ANALYTICS.text_max_length)


class OzonAnalyticsCommentKey(OzonAnalyticsProductKey):
    week: date
    kind: OzonAnalyticsCommentKind

    @field_validator("week")
    @classmethod
    def monday_only(cls, value: date) -> date:
        if value.weekday():
            raise ValueError(OzonAnalyticsReason.INVALID_PERIOD)
        return value


class OzonAnalyticsCommentChange(OzonAnalyticsCommentKey):
    text: AnalyticsCommentText
    version: AnalyticsVersion


class OzonAnalyticsAuthor(OzonAnalyticsModel):
    id: int | None = None
    name: str


class OzonAnalyticsNote(OzonAnalyticsModel):
    id: int
    store_slug: str
    article: str
    action_date: date
    note: str
    user_id: int | None = None
    user_name: str
    created_at: datetime
    can_edit: bool = False
    updated_at: datetime | None = None
    updated_by: str | None = None
    revisions: tuple["OzonAnalyticsNoteRevision", ...] = ()


class OzonAnalyticsNoteRevision(OzonAnalyticsModel):
    id: str
    action_id: int
    previous_note: str
    note: str
    user_id: int | None = None
    user_name: str
    edited_at: datetime


class OzonAnalyticsComment(OzonAnalyticsModel):
    store_slug: str
    article: str
    week: date
    kind: OzonAnalyticsCommentKind
    text: AnalyticsCommentText
    version: int
    author_id: int | None = None
    author: str
    updated_at: datetime


class OzonAnalyticsCommentRevision(OzonAnalyticsComment):
    previous_text: AnalyticsCommentText | None


class OzonAnalyticsIssue(OzonAnalyticsModel):
    field: str
    reason: OzonAnalyticsReason
    day: date | None = None
    observed_at: datetime | None = None
    next_retry_at: datetime | None = None


class OzonAnalyticsMetricQuality(OzonAnalyticsModel):
    complete: bool
    stale: bool = False
    reasons: tuple[OzonAnalyticsReason, ...] = ()
    expected_dates: tuple[date, ...] = ()
    known_dates: tuple[date, ...] = ()


class OzonAnalyticsCatalog(OzonAnalyticsModel):
    store_slug: str
    article: str
    name: str
    barcode: str = ""
    image: str = ""
    manager: str = ""
    category: str = ""
    code: str = ""
    goal_week: float | None = None
    goal_day: float | None = None
    stock_status: str | None = None
    stock_end_week: str | int | None = None
    source_synced_at: datetime | None = None
    mp_sku: str | None = None
    mp_product_id: str | None = None
    stock_updated_at: datetime | None = None


class OzonAnalyticsReference(OzonAnalyticsModel):
    store_slug: str
    article: str
    day: date
    goal_week: float | None
    goal_day: float | None
    source_synced_at: datetime | None = None
    captured_at: datetime


class OzonAnalyticsPrice(OzonAnalyticsModel):
    store_slug: str
    article: str
    product_id: str
    day: date
    values: PriceValues


class OzonAnalyticsStock(OzonAnalyticsModel):
    article: str
    fbs: int | None = None
    fbo: int | None = None
    ff: int | None = None
    from_customer: int | None = None
    to_customer: int | None = None
    field_issues: tuple[OzonAnalyticsIssue, ...] = ()


class OzonAnalyticsStockSnapshot(OzonAnalyticsModel):
    store_slug: str
    week: date
    captured_at: datetime
    products: tuple[OzonAnalyticsStock, ...]


class OzonAnalyticsTransit(OzonAnalyticsModel):
    store_slug: str
    observed_at: datetime
    products: tuple[OzonAnalyticsStock, ...]


class OzonAnalyticsSourceStatus(OzonAnalyticsModel):
    store_slug: str
    source: str
    source_day: date | None = None
    attempted_at: datetime
    reason: OzonAnalyticsReason | None = None
    next_retry_at: datetime | None = None


class OzonAnalyticsFunnelValue(OzonAnalyticsModel):
    sku: str
    article: str
    carts: AnalyticsCounter


class OzonAnalyticsFunnelDay(OzonAnalyticsModel):
    store_slug: str
    source_day: date
    observed_at: datetime
    products: tuple[OzonAnalyticsFunnelValue, ...]
    source_timezone: str = OZON_ANALYTICS.source_timezone
    traversal_complete: bool = True
    temporal_finalized: bool = False
    mapping_complete: bool = False
    source_timestamp: str | None = None


class OzonAnalyticsFunnelRun(OzonAnalyticsModel):
    account: str
    store_slug: str
    source_day: date
    generation: str
    queue: str
    state: OzonAnalyticsRunState
    offset: int = 0
    page_attempts: int = 0
    reason: OzonAnalyticsReason | None = None
    next_retry_at: datetime | None = None
    updated_at: datetime
    started_at: datetime
    lease_token: str | None = None
    lease_until: datetime | None = None
    raw_payload: str | None = None


class OzonAnalyticsReservation(OzonAnalyticsModel):
    allowed: bool
    next_retry_at: datetime | None = None
    page_attempts: int = 0
    reason: OzonAnalyticsReason | None = None


class OzonAnalyticsFunnelPage(OzonAnalyticsModel):
    items: tuple[OzonAnalyticsFunnelValue, ...]
    rows: int = Field(ge=0, le=OZON_ANALYTICS.page_size)
    source_timestamp: str | None = None

    @model_validator(mode="after")
    def row_count(self) -> Self:
        if self.rows != len(self.items):
            raise ValueError(OzonAnalyticsReason.MALFORMED_SOURCE)
        return self


class OzonAnalyticsSyncResult(OzonAnalyticsModel):
    stores: int = 0
    products: int = 0
    attempts: int = 0
    deferred: int = 0
    error: str | None = None


class OzonAnalyticsConflict(Exception):
    pass


class OzonAnalyticsApiError(Exception):
    def __init__(
        self,
        reason: OzonAnalyticsReason,
        *,
        retry_seconds: float | None = None,
        status: int | None = None,
        raw_payload: str | None = None,
    ):
        self.reason = reason
        self.retry_seconds = retry_seconds
        self.status = status
        self.raw_payload = raw_payload
        super().__init__(reason)


class OzonAnalyticsRequestError(Exception):
    def __init__(self, status: int, reason: OzonAnalyticsReason):
        self.status = status
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonAnalyticsSalesSources:
    catalog: tuple[OzonAnalyticsCatalog, ...]
    turnover: OzonTurnoverData
    first_day: date | None
    last_day: date | None


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonAnalyticsSourceBundle:
    catalog: tuple[OzonAnalyticsCatalog, ...]
    current: tuple[OzonProduct, ...]
    turnover: OzonTurnoverData
    advertising: tuple[AdvertisingSourceStatus, ...]
    advertising_configured: frozenset[str]
    cabinets: tuple[OzonCabinetRecord, ...]
    buyouts: tuple[OzonBuyoutSnapshot, ...]
    margins: tuple[OzonMarginSnapshot, ...]
    prices: tuple[OzonAnalyticsPrice, ...]
    references: tuple[OzonAnalyticsReference, ...]
    notes: tuple[OzonAnalyticsNote, ...]
    comments: tuple[OzonAnalyticsComment, ...]
    stocks: tuple[OzonAnalyticsStockSnapshot, ...]
    funnel: tuple[OzonAnalyticsFunnelDay, ...]
    statuses: tuple[OzonAnalyticsSourceStatus, ...]


class OzonAnalyticsWeights(OzonAnalyticsModel):
    currentProfit: float | None = None
    currentPurchase: float | None = None
    spend: float | None = None
    boughtAmount: float | None = None
    yesterdaySpend: float | None = None
    yesterdayAmount: float | None = None
    impressions: float | None = None
    clicks: float | None = None
    profit: float | None = None
    purchase: float | None = None
    previousCount: float | None = None
    previousAmount: float | None = None


class OzonAnalyticsCoverage(OzonAnalyticsModel):
    orders: int = 0
    advertising: int = 0
    expected: int = 0
    drr: int = 0
    previousPrice: int = 0
    traffic: int = 0
    trafficComplete: bool = False
    roiComplete: bool = False
    currentRoiComplete: bool = False
    roiMessages: tuple[str, ...] = ()
    stale: bool = False
    partial_dates: tuple[date, ...] = ()


class OzonAnalyticsTurnover(OzonAnalyticsModel):
    plan: float | None = None
    fact: float | None = None
    forecast: float | None = None
    difference: float | None = None
    deviation: float | None = None


class OzonAnalyticsRow(OzonAnalyticsTurnover):
    metricIdentity: str = OZON_ANALYTICS.metric_identity
    id: str
    store_slug: str
    marketplace: str
    metric_key: str
    project: str
    article: str
    barcode: str = ""
    barcodes: tuple[str, ...] = ()
    name: str
    image: str = ""
    manager: str = ""
    category: str = ""
    code: str = ""
    is_new: bool | None = None
    stock: float | None = None
    stockPartial: bool = False
    stockDays: float | None = None
    stockDailyOrders: float | None = None
    stockOrdersComplete: bool = False
    stockStatus: str | None = None
    stockEnds: str | int | None = None
    roi: float | None = None
    currentRoi: float | None = None
    drr: float | None = None
    previousPrice: float | None = None
    impressions: tuple[float | None, ...] = ()
    clicks: tuple[float | None, ...] = ()
    ctr: tuple[float | None, ...] = ()
    carts: tuple[int | None, ...] = ()
    spend: float | None = None
    yesterdayDrr: float | None = None
    totalCtr: float | None = None
    rating: float | None = None
    week: tuple[float | None, ...] = ()
    goal: float | None = None
    dayGoal: float | None = None
    spp: float | None = None
    wallet: float | None = None
    price: float | None = None
    coeff: float | None = None
    notes: tuple[tuple[OzonAnalyticsNote, ...], ...] = ()
    noteHistory: tuple[OzonAnalyticsNote, ...] = ()
    updated: dict[str, datetime | date | None] = Field(default_factory=dict)
    coverage: OzonAnalyticsCoverage = OzonAnalyticsCoverage()
    weights: OzonAnalyticsWeights = OzonAnalyticsWeights()
    field_issues: tuple[OzonAnalyticsIssue, ...] = ()
    quality: dict[str, OzonAnalyticsMetricQuality] = Field(default_factory=dict)
    carts_source_days: tuple[date, ...] = ()
    carts_provisional: tuple[bool, ...] = ()


class OzonAnalyticsAnalyzer(OzonAnalyticsModel):
    ok: bool = True
    rows: tuple[OzonAnalyticsRow, ...]
    week: tuple[date, ...]
    traffic_days: tuple[date, ...]
    today: date
    anchor: date
    period_from: date
    period_to: date
    previous_from: date
    previous_to: date
    stockPreviousFrom: date
    stockPreviousTo: date
    turnover_day: date
    coefficient_hour: int
    coefficient_valid_until: datetime
    loaded_at: datetime


class OzonAnalyticsWeekInfo(OzonAnalyticsModel):
    start: date
    end: date
    year: int
    number: int


class OzonAnalyticsWeekMetrics(OzonAnalyticsModel):
    spp: float | None = None
    wallet: float | None = None
    price: float | None = None
    priceDay: date | None = None
    goal: float | None = None
    fact: float | None = None
    factNet: bool = False
    turnover: float | None = None
    impressions: float | None = None
    ctr: float | None = None
    roi: float | None = None
    drr: float | None = None
    weights: OzonAnalyticsWeights = OzonAnalyticsWeights()
    coverage: OzonAnalyticsCoverage = OzonAnalyticsCoverage()
    quality: dict[str, OzonAnalyticsMetricQuality] = Field(default_factory=dict)


class OzonAnalyticsEphemeridesRow(OzonAnalyticsCatalog):
    metricIdentity: str = OZON_ANALYTICS.metric_identity
    id: str
    marketplace: str
    metric_key: str
    project: str
    stock: float | None = None
    stockPartial: bool = False
    stockWeek: date | None = None
    stockUpdated: datetime | None = None
    transitUpdated: datetime | None = None
    fbs: int | None = None
    fbo: int | None = None
    ff: int | None = None
    fromCustomer: int | None = None
    stockStatus: str | None = None
    stockEnds: str | int | None = None
    rating: float | None = None
    is_new: bool | None = None
    previous: OzonAnalyticsWeekMetrics = OzonAnalyticsWeekMetrics()
    current: OzonAnalyticsWeekMetrics = OzonAnalyticsWeekMetrics()
    dayTurnover: OzonAnalyticsTurnover = OzonAnalyticsTurnover()
    comments: dict[str, OzonAnalyticsComment] = Field(default_factory=dict)
    field_issues: tuple[OzonAnalyticsIssue, ...] = ()
    quality: dict[str, OzonAnalyticsMetricQuality] = Field(default_factory=dict)


class OzonAnalyticsEphemerides(OzonAnalyticsModel):
    ok: bool = True
    rows: tuple[OzonAnalyticsEphemeridesRow, ...]
    current: OzonAnalyticsWeekInfo
    previous: OzonAnalyticsWeekInfo
    weeks: tuple[OzonAnalyticsWeekInfo, ...]
    today: date
    through: date
    coefficient_hour: int
    coefficient_valid_until: datetime
    loaded_at: datetime


class OzonAnalyticsHistory(OzonAnalyticsModel):
    ok: bool = True
    rows: tuple[OzonAnalyticsEphemeridesRow, ...]
    weeks: tuple[OzonAnalyticsWeekInfo, ...]


class OzonAnalyticsSalesRow(OzonAnalyticsModel):
    id: str
    store_slug: str
    marketplace: str
    metric_key: str
    project: str
    article: str
    name: str
    image: str = ""
    category: str = ""
    barcode: str = ""
    barcodes: tuple[str, ...] = ()
    article_aliases: tuple[str, ...] = ()
    days: tuple[float | None, ...]
    cancellations: tuple[float | None, ...]
    orders: float | None
    cancels: float | None
    known_days: int
    complete: bool
    today_turnover: float | None
    field_issues: tuple[OzonAnalyticsIssue, ...] = ()
    quality: dict[str, OzonAnalyticsMetricQuality] = Field(default_factory=dict)


class OzonAnalyticsSales(OzonAnalyticsModel):
    ok: bool = True
    rows: tuple[OzonAnalyticsSalesRow, ...]
    dates: tuple[date, ...]
    date_from: date
    date_to: date
    available_from: date | None
    available_to: date | None
    today: date
    turnover_day: date
    updated_at: datetime | None
    loaded_at: datetime
    max_days: int = OZON_ANALYTICS.max_sales_days


class OzonAnalyticsNoteResponse(OzonAnalyticsModel):
    ok: bool = True
    note: OzonAnalyticsNote


class OzonAnalyticsNoteRevisionsResponse(OzonAnalyticsModel):
    ok: bool = True
    revisions: tuple[OzonAnalyticsNoteRevision, ...]


class OzonAnalyticsCommentResponse(OzonAnalyticsModel):
    ok: bool = True
    comment: OzonAnalyticsComment


class OzonAnalyticsCommentRevisionsResponse(OzonAnalyticsModel):
    ok: bool = True
    revisions: tuple[OzonAnalyticsCommentRevision, ...]


class OzonAnalyticsApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class OzonAnalyticsFunnelRequest(OzonAnalyticsModel):
    date_from: date
    date_to: date
    dimension: tuple[str, ...] = OZON_ANALYTICS.funnel_dimensions
    metrics: tuple[str, ...] = OZON_ANALYTICS.funnel_metrics
    limit: int = OZON_ANALYTICS.page_size
    offset: int = Field(ge=0)
    filters: tuple[object, ...] = ()

    @model_validator(mode="after")
    def single_day(self) -> Self:
        if self.date_from != self.date_to or self.date_to > datetime.now(UTC).date():
            raise ValueError(OzonAnalyticsReason.INVALID_PERIOD)
        return self


class OzonAnalyticsDimension(OzonAnalyticsApiModel):
    id: str = Field(min_length=1)
    name: str | None = None

    @field_validator("id")
    @classmethod
    def sku_only(cls, value: str) -> str:
        if not value.isdecimal() or int(value) <= 0:
            raise ValueError(OzonAnalyticsReason.MALFORMED_SOURCE)
        return value


class OzonAnalyticsApiRow(OzonAnalyticsApiModel):
    dimensions: tuple[OzonAnalyticsDimension, ...] = Field(min_length=1, max_length=1)
    metrics: tuple[AnalyticsNumber, ...] = Field(min_length=1, max_length=1)

    @field_validator("metrics", mode="before")
    @classmethod
    def valid_counter(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)) or any(
            isinstance(item, bool) or not isinstance(item, (int, float)) for item in value
        ):
            raise ValueError(OzonAnalyticsReason.MALFORMED_SOURCE)
        return value

    @model_validator(mode="after")
    def integer_counter(self) -> Self:
        if any(
            item < 0 or item > OZON_ANALYTICS.max_counter or not item.is_integer() for item in self.metrics
        ):
            raise ValueError(OzonAnalyticsReason.MALFORMED_SOURCE)
        return self


class OzonAnalyticsApiResult(OzonAnalyticsApiModel):
    data: tuple[OzonAnalyticsApiRow, ...]


class OzonAnalyticsApiResponse(OzonAnalyticsApiModel):
    result: OzonAnalyticsApiResult
    timestamp: str | None = None


class OzonAnalyticsStockCounter(OzonAnalyticsApiModel):
    type: str = Field(min_length=1)
    sku: AnalyticsExternalId


class OzonAnalyticsStockProduct(OzonAnalyticsApiModel):
    product_id: Annotated[StrictInt, Field(gt=0)]
    offer_id: str = Field(min_length=1)
    stocks: tuple[OzonAnalyticsStockCounter, ...]


class OzonAnalyticsStocksPage(OzonAnalyticsApiModel):
    items: tuple[OzonAnalyticsStockProduct, ...]
    cursor: str = ""
    total: int | None = None
    total_items: int | None = None


class OzonAnalyticsTransitRow(OzonAnalyticsApiModel):
    sku: AnalyticsExternalId
    offer_id: str = Field(min_length=1)
    warehouse_id: AnalyticsWarehouseId
    return_from_customer_stock_count: AnalyticsCounter
    outbound_pending_delivery: AnalyticsCounter | None = None
    item_tags: tuple[str, ...] = ()

    @field_validator("warehouse_id")
    @classmethod
    def warehouse_identity(cls, value: int | str) -> int:
        if not value:
            raise ValueError(OzonAnalyticsReason.MALFORMED_SOURCE)
        return int(value)
