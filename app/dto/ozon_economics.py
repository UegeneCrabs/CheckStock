from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Self

from pydantic import Field, model_validator

from app.core.constants import TIME, WEB, InboundAvailability, OzonDataIssueCode
from app.dto.common import DtoModel
from app.dto.ozon_advertising import (
    AdvertisingBreakdown,
    AdvertisingDailyExpense,
    AdvertisingSpendSummary,
    StoreAdvertisingBreakdown,
)
from app.dto.ozon_cabinet import OzonEffectiveBuyout, OzonEffectiveParameters
from app.dto.ozon_calculator import OzonCalculatorAdvertisingWeek
from app.dto.ozon_commissions import OzonCommissionData
from app.dto.ozon_logistics import LogisticsData
from app.dto.ozon_margin import OzonMarginDay
from app.dto.ozon_newness import OzonNewness
from app.dto.ozon_prices import PriceValues
from app.dto.ozon_storage import StorageData


class OzonDataIssue(DtoModel):
    code: OzonDataIssueCode
    source: str | None = None
    field: str | None = None
    detail: str | None = None


class OzonTag(DtoModel):
    goal_week: float | None = None
    goal_day: float | None = None
    status: str | None = None
    ends: str | int | None = None
    code: str | None = None
    fact: float | None = None
    plan: float | None = None


class OzonPrices(PriceValues):
    current: float | None = None
    with_spp: float | None = None
    with_wallet: float | None = None


class OzonCurrentEconomics(DtoModel):
    margin: float | None = None
    roi: float | None = None
    orders: int | None = None
    buyout_percent: float | None = None
    advertising_spend: float | None = None
    period_to: str | None = None
    day_profit: Decimal | None = None
    expected_buyouts: Decimal | None = None
    day_purchase_value: Decimal | None = None
    purchase_value: Decimal | None = None
    complete: bool = False
    daily_complete: bool = False
    calculation: OzonMarginDay | None = None


class OzonCoverage(DtoModel):
    dates: tuple[str, ...]
    days: int
    expected_days: int
    complete: bool
    missing_dates: tuple[str, ...]
    partial_dates: tuple[str, ...] = ()
    stale: bool = False


class OzonOrderDay(DtoModel):
    store_slug: str
    article: str
    day: date
    amount: Decimal
    ordered: int = 0
    received: int = 0
    cancelled: int = 0
    fbs_ordered: int = 0


class OzonConfirmedDay(DtoModel):
    store_slug: str
    day: date


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonTurnoverData:
    products: dict[tuple[str, str], tuple[OzonOrderDay, ...]]
    confirmed_days: dict[str, frozenset[date]]
    stale_stores: frozenset[str]
    eligible_days: dict[str, frozenset[date]]
    updated_at: dict[tuple[str, date], datetime]


class OzonTurnoverValue(DtoModel):
    amount: Decimal | None
    coverage: OzonCoverage


class OzonPeriodEconomics(DtoModel):
    turnover: float | None
    turnover_coverage: OzonCoverage | None
    margin: float | None
    roi: float | None
    period_from: str
    period_to: str
    margin_coverage: OzonCoverage | None = None
    roi_coverage: OzonCoverage | None = None
    complete: bool = False
    purchase_value: Decimal | None = None
    roi_purchase_value: Decimal | None = None
    daily: tuple[OzonMarginDay, ...] = ()


class OzonTrafficMetrics(DtoModel):
    click_spend: Decimal | None
    impressions: int | None
    clicks: int | None
    ctr: float | None
    cpc: float | None
    coverage: OzonCoverage


class OzonDrrMetrics(DtoModel):
    buyout: OzonEffectiveBuyout = OzonEffectiveBuyout()
    turnover: Decimal | None
    spend: Decimal | None
    received: int | None
    cancelled: int | None
    buyout_percent: float | None
    drr: float | None
    coverage: OzonCoverage
    updated_at: datetime | None


class OzonCalculatorMetrics(DtoModel):
    advertising_week: OzonCalculatorAdvertisingWeek | None = None
    orders_count: int | None = None
    other_spend: Decimal | None = None
    other_breakdown: tuple[AdvertisingDailyExpense, ...] = ()
    day: date
    turnover: Decimal | None
    buyout_percent: float | None
    buyout: OzonEffectiveBuyout
    updated_at: datetime | None
    clicks: OzonDrrMetrics
    orders: OzonDrrMetrics
    click_spend: Decimal | None
    order_spend: Decimal | None
    click_updated_at: datetime | None
    order_updated_at: datetime | None


class OzonAdvertisingMetrics(DtoModel):
    breakdown: tuple[AdvertisingBreakdown, ...] = ()
    spend_summary: AdvertisingSpendSummary | None = None
    click_spend: Decimal | None = None
    traffic_coverage: OzonCoverage | None = None
    drr: float | None
    spend: float | None
    ctr: float | None
    cpc: float | None
    impressions: int | None
    clicks: int | None
    orders_amount: float | None
    period_from: str
    period_to: str
    coverage: OzonCoverage | None
    drr_coverage: OzonCoverage | None
    drr_basis: OzonDrrMetrics | None = None


class OzonStockMetrics(DtoModel):
    total: int | None
    fbs: int | None
    fbo: int | None
    fulfillment: int | None
    inbound: int | None
    inbound_partial: bool
    inbound_state: InboundAvailability
    days: float | None
    state: str | None
    period_days: int
    orders_21d: int | None
    average_daily_orders: float | None
    coverage: OzonCoverage | None


class OzonSourceDetails(DtoModel):
    purchase_cost: float | None
    fulfillment_cost: float | None
    team_commission_percent: float | None
    source_sheet_title: str | None
    source_row: int | None
    source_synced_at: str | None


class OzonProduct(DtoModel):
    id: str
    marketplace: str
    store_slug: str
    store_name: str
    article: str
    barcode: str | None
    name: str
    image_url: str | None
    mp_sku: str | None
    mp_product_id: str | None
    rating: float | None
    reviews_count: int | None
    manager: str | None
    tag: str | None
    tag_data: OzonTag
    is_new: bool | None
    sales_days: int | None
    newness: OzonNewness = OzonNewness()
    price: OzonPrices
    commission: OzonCommissionData = OzonCommissionData()
    logistics: LogisticsData = LogisticsData()
    storage: StorageData = StorageData()
    cabinet: OzonEffectiveParameters | None = None
    current_economics: OzonCurrentEconomics
    economics_7d: OzonPeriodEconomics
    advertising: OzonAdvertisingMetrics
    stock: OzonStockMetrics
    details: OzonSourceDetails
    history: None = None
    calculator: OzonCalculatorMetrics | None = None
    data_issues: tuple[OzonDataIssue, ...]


class OzonProductsQuery(DtoModel):
    stores: tuple[str, ...]
    article: str = ""
    period_from: date
    period_to: date


class OzonReportRequest(DtoModel):
    period_days: int = Field(default=WEB.week_days, ge=1, le=WEB.report_max_days)
    date_from: date | None = None
    date_to: date | None = None
    store: str = ""
    article: str = ""

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if bool(self.date_from) != bool(self.date_to):
            raise ValueError("Both period dates are required")
        if self.date_from and self.date_to:
            if self.date_from > self.date_to or (self.date_to - self.date_from).days >= WEB.report_max_days:
                raise ValueError("Invalid period")
            if self.date_to >= datetime.now(TIME.moscow_timezone).date():
                raise ValueError("Only completed days are available")
        return self

    def query(self, stores: tuple[str, ...]) -> OzonProductsQuery:
        end = self.date_to or datetime.now(TIME.moscow_timezone).date() - timedelta(days=1)
        return OzonProductsQuery(
            stores=(self.store,) if self.store else stores,
            article=self.article,
            period_from=self.date_from or end - timedelta(days=self.period_days - 1),
            period_to=end,
        )


class OzonProducts(DtoModel):
    advertising_unallocated: tuple[StoreAdvertisingBreakdown, ...] = ()
    ok: bool = True
    products: tuple[OzonProduct, ...]
    period_days: int
    period_from: date
    period_to: date
    period_mode: str
    last_complete_day: date


class OzonProductDetail(DtoModel):
    ok: bool = True
    product: OzonProduct
