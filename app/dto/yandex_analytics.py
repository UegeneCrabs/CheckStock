from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from app.core.constants import WB_ANALYZER, YANDEX_ANALYTICS
from app.dto.common import DtoModel

Number = FiniteFloat | None
Counter = Annotated[int, Field(strict=True, ge=0)] | None
Kind = Literal["competitor", "decision", "next"]
FunnelDateMode = Literal["iso_date", "local_date", "local_dash_date", "day_of_month", "request_only"]


class AnalyticsSourceError(ValueError):
    def __init__(self, reason: str, *, field: str, value: str | int | None):
        self.reason = reason
        self.field = field
        self.value = str(value)[: YANDEX_ANALYTICS.diagnostic_value_limit] if value is not None else None
        super().__init__(f"{reason}: {field}={self.value!r}")


class AnalyticsModel(DtoModel):
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=False, allow_inf_nan=False)


class ProductKey(AnalyticsModel):
    store: str = Field(min_length=1, max_length=100)
    article: str = Field(min_length=1, max_length=200)


class CommentKey(ProductKey):
    week: date
    kind: Kind


class CommentChange(CommentKey):
    text: str = Field(max_length=YANDEX_ANALYTICS.text_limit)
    version: Annotated[int, Field(strict=True, ge=0, le=YANDEX_ANALYTICS.version_limit)]


class Comment(CommentChange):
    author_id: int
    author: str
    updated_at: str


class NoteCreate(ProductKey):
    day: date
    note: str = Field(min_length=1, max_length=YANDEX_ANALYTICS.text_limit)

    @field_validator("note")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty_note")
        return value


class NoteChange(AnalyticsModel):
    note: str = Field(min_length=1, max_length=YANDEX_ANALYTICS.text_limit)
    previous_note: str = Field(min_length=1, max_length=YANDEX_ANALYTICS.text_limit)

    @field_validator("note")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty_note")
        return value


class NoteRevision(AnalyticsModel):
    id: str
    action_id: int
    previous_note: str
    note: str
    user_id: int
    user_name: str
    edited_at: str


class DayNote(AnalyticsModel):
    id: int
    store_slug: str
    article: str
    action_date: str
    note: str
    user_name: str
    created_at: str
    updated_at: str | None = None
    updated_by: str | None = None
    revisions: tuple[NoteRevision, ...] = ()
    can_edit: bool = False


class NoteResponse(AnalyticsModel):
    ok: bool = True
    note: DayNote


class CommentResponse(AnalyticsModel):
    ok: bool = True
    comment: Comment


class RevisionResponse(AnalyticsModel):
    ok: bool = True
    revisions: tuple[Comment, ...]


class HourD(AnalyticsModel):
    hour: Annotated[int, Field(strict=True, ge=0, lt=YANDEX_ANALYTICS.hours)]
    value: Annotated[FiniteFloat, Field(ge=0, le=WB_ANALYZER.max_d)] | None = None

    @field_validator("value", mode="before")
    @classmethod
    def numeric(cls, value: object) -> object:
        if isinstance(value, bool) or (value is not None and not isinstance(value, (int, float))):
            raise ValueError("invalid_coefficient")
        return value


class DSettings(AnalyticsModel):
    hourly_d_coefficients: tuple[HourD, ...] = ()
    revision: Annotated[int, Field(strict=True, ge=0, le=YANDEX_ANALYTICS.version_limit)] = 0

    @model_validator(mode="after")
    def unique_hours(self) -> "DSettings":
        if len({item.hour for item in self.hourly_d_coefficients}) != len(self.hourly_d_coefficients):
            raise ValueError("duplicate_hour")
        return self

    def coefficient(self, hour: int) -> float | None:
        return next((item.value for item in self.hourly_d_coefficients if item.hour == hour), None)


class Product(AnalyticsModel):
    store_slug: str
    article: str
    name: str = ""
    barcode: str = ""
    barcodes: tuple[str, ...] = ()
    article_aliases: tuple[str, ...] = ()
    image_url: str = ""
    fbs_stock: Number = None
    fbo_stock: Number = None
    ff_available: Number = None
    mp_updated_at: str | None = None


class Reference(AnalyticsModel):
    manager: str | None = None
    abc_code: str | None = None
    goal_week: Number = None
    goal_day: Number = None
    stock_status: str | None = None
    stock_end_week: str | None = None
    synced_at: str | None = None


class Prices(AnalyticsModel):
    seller_price: Number = None
    buyer_price: Number = None
    pay_price: Number = None
    buyer_estimated: bool = False
    pay_estimated: bool = False
    stale: bool = False
    updated_at: str | None = None


class FunnelRequest(AnalyticsModel):
    date_from: date
    date_to: date
    grouping: Literal["OFFERS"]


class FunnelDateComponents(AnalyticsModel):
    day_present: bool
    day: str | None = None
    month_present: bool
    month: str | None = None
    year_present: bool
    year: int | None = None


class ParsedFunnelDate(AnalyticsModel):
    source_date: date
    mode: FunnelDateMode


class DailyMetric(AnalyticsModel):
    article: str
    day: str
    orders_count: Counter = None
    orders_amount: Number = None
    cancel_count: Counter = None
    cancel_amount: Number = None
    spend: Number = None
    impressions: Counter = None
    clicks: Counter = None
    cart_count: Counter = None
    timezone: str | None = None
    provisional: bool = False
    received_at: AwareDatetime | None = None
    source_scope: Literal["report_date"] | None = None
    source_date_origin: Literal["explicit_day", "single_day_request"] | None = None
    source_date_parse_mode: FunnelDateMode | None = None
    source_date_components: FunnelDateComponents | None = None


class CartSource(AnalyticsModel):
    source_date: date
    source_scope: Literal["report_date"] = "report_date"
    timezone: Literal["unknown"] = "unknown"
    received_at: AwareDatetime | None = None
    provisional: Literal[True] = True
    complete: Literal[False] = False
    known: bool = False
    stale: bool = False
    reason: str | None = None
    source_date_origin: Literal["explicit_day", "single_day_request"] | None = None
    source_date_parse_mode: FunnelDateMode | None = None
    source_date_components: FunnelDateComponents | None = None


class EconomicsDay(AnalyticsModel):
    day: str
    buyout: Number = None
    profit: Number = None
    purchase: Number = None
    complete: bool = False
    messages: tuple[str, ...] = ()
    goal: Number = None
    goal_known: bool = False
    prices: Prices = Field(default_factory=Prices)


class CurrentEconomics(AnalyticsModel):
    roi: Number = None
    profit: Number = None
    purchase: Number = None
    complete: bool = False


class Coverage(AnalyticsModel):
    expected: int = 0
    known: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    complete: bool = False


class MetricQuality(AnalyticsModel):
    complete: bool
    stale: bool = False
    reasons: tuple[str, ...] = ()
    expected_dates: tuple[str, ...] = ()
    known_dates: tuple[str, ...] = ()


class RatioParts(AnalyticsModel):
    numerator: Number = None
    denominator: Number = None
    value: Number = None
    coverage: Coverage = Field(default_factory=Coverage)
    quality: MetricQuality


class StockProduct(AnalyticsModel):
    article: str
    fbs: Number = None
    fbo: Number = None
    ff: Number = None
    fromCustomer: Number = None


class WeeklyStock(AnalyticsModel):
    store: str
    week: date
    captured_at: datetime
    products: tuple[StockProduct, ...]


class TransitSnapshot(AnalyticsModel):
    store: str
    quantities: dict[str, StrictInt]
    received_at: datetime
    error: str | None = None

    @field_validator("quantities")
    @classmethod
    def nonnegative(cls, values: dict[str, int]) -> dict[str, int]:
        if any(not article or count < 0 for article, count in values.items()):
            raise ValueError("invalid_return_quantity")
        return values


class FunnelDay(AnalyticsModel):
    store: str
    source_date: date
    timezone: Literal["unknown"] = "unknown"
    source_scope: Literal["report_date"] = "report_date"
    source_date_origin: Literal["single_day_request"] | None = None
    request: FunnelRequest | None = None
    received_at: AwareDatetime
    provisional: Literal[True] = True
    complete: Literal[False] = False
    products: tuple[DailyMetric, ...]


class Weights(AnalyticsModel):
    currentProfit: Number = None
    currentPurchase: Number = None
    spend: Number = None
    boughtAmount: Number = None
    yesterdaySpend: Number = None
    yesterdayAmount: Number = None
    impressions: Number = None
    clicks: Number = None
    profit: Number = None
    purchase: Number = None
    previousCount: Number = None
    previousAmount: Number = None


class AnalyzerRow(AnalyticsModel):
    id: str
    store_slug: str
    project: str
    article: str
    name: str
    barcode: str = ""
    barcodes: tuple[str, ...] = ()
    image: str = ""
    manager: str = ""
    category: str = ""
    code: str = ""
    metricIdentity: Literal["fullArticle"] = "fullArticle"
    stock: Number = None
    stockPartial: bool = False
    stockDays: Number = None
    stockDailyOrders: Number = None
    stockOrdersComplete: bool = False
    stockStatus: str | None = None
    stockEnds: str | None = None
    roi: Number = None
    currentRoi: Number = None
    drr: Number = None
    previousPrice: Number = None
    impressions: tuple[Number, ...] = ()
    clicks: tuple[Number, ...] = ()
    ctr: tuple[Number, ...] = ()
    carts: tuple[Number, ...] = ()
    cartSources: tuple[CartSource, ...] = ()
    spend: Number = None
    yesterdayDrr: Number = None
    totalCtr: Number = None
    rating: Number = None
    week: tuple[Number, ...] = ()
    goal: Number = None
    dayGoal: Number = None
    spp: Number = None
    wallet: Number = None
    price: Number = None
    coeff: Number = None
    fact: Number = None
    forecast: Number = None
    difference: Number = None
    deviation: Number = None
    plan: Number = None
    notes: tuple[tuple[DayNote, ...], ...] = ()
    noteHistory: tuple[DayNote, ...] = ()
    updated: dict[str, str | None] = Field(default_factory=dict)
    coverage: dict[str, int | bool | tuple[str, ...] | Coverage] = Field(default_factory=dict)
    reasons: dict[str, str] = Field(default_factory=dict)
    weights: Weights = Field(default_factory=Weights)
    quality: dict[str, MetricQuality] = Field(default_factory=dict)


class AnalyzerResponse(AnalyticsModel):
    ok: bool = True
    rows: tuple[AnalyzerRow, ...]
    week: tuple[str, ...]
    traffic_days: tuple[str, ...]
    today: str
    anchor: str
    period_from: str
    period_to: str
    previous_from: str
    previous_to: str
    stockPreviousFrom: str
    stockPreviousTo: str
    turnover_day: str
    loaded_at: str
    coefficient_hour: int = 0
    coefficient_valid_until: str | None = None
    stores: tuple[str, ...] = ()


class SalesRow(AnalyticsModel):
    id: str
    store_slug: str
    project: str
    article: str
    name: str
    image: str = ""
    category: str = ""
    barcode: str = ""
    barcodes: tuple[str, ...] = ()
    article_aliases: tuple[str, ...] = ()
    days: tuple[Number, ...]
    cancellations: tuple[Number, ...]
    orders: Number = None
    cancels: Number = None
    known_days: int = 0
    complete: bool = False
    today_turnover: Number = None
    quality: dict[str, MetricQuality] = Field(default_factory=dict)


class ArticleBounds(AnalyticsModel):
    article: str
    first_day: date
    last_day: date


class StoreBounds(AnalyticsModel):
    first_day: date | None = None
    last_day: date | None = None
    articles: tuple[ArticleBounds, ...] = ()


class SalesResponse(AnalyticsModel):
    ok: bool = True
    rows: tuple[SalesRow, ...]
    dates: tuple[str, ...]
    date_from: str
    date_to: str
    available_from: str | None = None
    available_to: str | None = None
    today: str
    turnover_day: str
    updated_at: str | None = None
    loaded_at: str
    max_days: int = YANDEX_ANALYTICS.max_days


class WeekInfo(AnalyticsModel):
    start: str
    end: str
    year: int
    number: int


class EphemeridesWeek(AnalyticsModel):
    spp: Number = None
    wallet: Number = None
    price: Number = None
    priceDay: str | None = None
    goal: Number = None
    fact: Number = None
    factNet: bool
    turnover: Number = None
    impressions: Number = None
    ctr: Number = None
    roi: Number = None
    drr: Number = None
    weights: Weights
    coverage: dict[str, int | bool | tuple[str, ...] | Coverage]
    quality: dict[str, MetricQuality] = Field(default_factory=dict)


class EphemeridesRow(AnalyticsModel):
    id: str
    store_slug: str
    article: str
    name: str
    barcode: str = ""
    image: str = ""
    project: str
    manager: str = ""
    category: str = ""
    code: str = ""
    metricIdentity: Literal["fullArticle"] = "fullArticle"
    fbs: Number = None
    fbo: Number = None
    ff: Number = None
    fromCustomer: Number = None
    stock: Number = None
    stockPartial: bool = True
    stockUpdated: str | None = None
    transitUpdated: str | None = None
    stockWeek: str | None = None
    previous: EphemeridesWeek | None = None
    current: EphemeridesWeek | None = None
    rating: Number = None
    dayTurnover: dict[str, Number] = Field(default_factory=dict)
    comments: dict[str, Comment] = Field(default_factory=dict)
    quality: dict[str, MetricQuality] = Field(default_factory=dict)


class EphemeridesResponse(AnalyticsModel):
    ok: bool = True
    rows: tuple[EphemeridesRow, ...]
    weeks: tuple[WeekInfo, ...]
    current: WeekInfo | None = None
    previous: WeekInfo | None = None
    today: str
    through: str | None = None
    loaded_at: str | None = None
