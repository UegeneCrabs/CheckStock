from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import ConfigDict, Field, model_validator

from app.core.constants import OZON_REPORTS, TIME, WEB, OzonReportGroup, OzonReportRowKind, OzonTargetIssue
from app.dto.common import DtoModel
from app.dto.ozon_cabinet import OzonCabinetGoals, OzonProductGoals, Percent, RoiPercent
from app.dto.ozon_calculator import OzonCalculatorValues
from app.dto.ozon_economics import OzonProduct, OzonTurnoverData

Number = Annotated[float, Field(allow_inf_nan=False)]


class OzonProfitQuery(DtoModel):
    date_from: date = Field(default_factory=lambda: datetime.now(TIME.moscow_timezone).date())
    date_to: date = Field(default_factory=lambda: datetime.now(TIME.moscow_timezone).date())
    store: tuple[str, ...] = ()
    subject: tuple[str, ...] = ()
    manager: tuple[str, ...] = ()
    article: tuple[str, ...] = ()
    group_by: OzonReportGroup = OzonReportGroup.PRODUCT
    daily_details: bool = False
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=OZON_REPORTS.default_page_size, ge=1, le=OZON_REPORTS.max_page_size)

    @model_validator(mode="after")
    def valid_dates(self) -> Self:
        if self.date_from > self.date_to or (self.date_to - self.date_from).days >= WEB.report_max_days:
            raise ValueError("invalid_report_period")
        if self.date_to > datetime.now(TIME.moscow_timezone).date():
            raise ValueError("future_report_period")
        return self


class OzonReportIdentity(DtoModel):
    store_slug: str
    article: str
    subject: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonReportCategories:
    identities: tuple[OzonReportIdentity, ...]
    unavailable_stores: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonReportEvidence:
    products: tuple[OzonProduct, ...]
    turnover: OzonTurnoverData
    identities: tuple[OzonReportIdentity, ...]
    stocked: frozenset[tuple[str, str]]


class OzonProfitMetrics(DtoModel):
    orders_count: Number | None = None
    cancel_count: Number | None = None
    net_orders_count: Number | None = None
    orders_amount: Number | None = None
    cancel_amount: Number | None = None
    net_orders_amount: Number | None = None
    buyout_percent: Number | None = None
    buyout_orders_count: Number | None = None
    expected_buyout_amount: Number | None = None
    stock: Number | None = None
    stock_fbs: Number | None = None
    stock_fbo: Number | None = None
    stock_fulfillment: Number | None = None
    stock_days: Number | None = None
    stock_average_daily_orders: Number | None = None
    impressions: Number | None = None
    clicks: Number | None = None
    ctr: Number | None = None
    cpc: Number | None = None
    click_spend: Number | None = None
    advertising_spend: Number | None = None
    drr: Number | None = None
    drr_spend: Number | None = None
    margin_orders_count: Number | None = None
    margin: Number | None = None
    purchase_value: Number | None = None
    roi_purchase_value: Number | None = None
    roi: Number | None = None
    margin_complete: bool = False
    margin_missing_days: tuple[date, ...] = ()
    margin_missing_days_count: int = 0
    margin_expected_days: int = 0
    margin_available_days: int = 0
    unavailable_days: tuple[date, ...] = ()


class OzonProfitDay(DtoModel):
    date: date
    orders_count: int | None = None
    net_orders_count: int | None = None
    buyout_percent: Number | None = None
    expected_buyouts: Number | None = None
    retail_price: Number | None = None
    purchase_price: Number | None = None
    fulfillment_expense: Number | None = None
    commission_value: Number | None = None
    acquiring_value: Number | None = None
    team_commission_value: Number | None = None
    unallocated_value: Number | None = None
    vat_value: Number | None = None
    usn_value: Number | None = None
    logistics: Number | None = None
    storage: Number | None = None
    advertising_spend: Number | None = None
    advertising_per_unit: Number | None = None
    total_cost: Number | None = None
    net_profit: Number | None = None
    day_profit: Number | None = None
    day_purchase_value: Number | None = None
    available: bool = False
    complete: bool = False
    missing: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


class OzonProfitRow(OzonProfitMetrics):
    store_slug: str = ""
    store_name: str = ""
    article: str = ""
    name: str = ""
    image_url: str | None = None
    subject: str = ""
    manager: str | None = None
    code: str | None = None
    row_kind: OzonReportRowKind = OzonReportRowKind.PRODUCT
    product_count: int = 1
    daily_calculations: tuple[OzonProfitDay, ...] = ()


class OzonReportArticle(DtoModel):
    article: str
    name: str
    store_slug: str
    store_name: str
    image_url: str | None = None


class OzonReportFilters(DtoModel):
    subjects: tuple[str, ...] = ()
    managers: tuple[str, ...] = ()
    articles: tuple[OzonReportArticle, ...] = ()


class OzonReportPagination(DtoModel):
    enabled: bool = False
    page: int = 1
    page_size: int = OZON_REPORTS.default_page_size
    total_count: int = 0
    total_pages: int = 1


class OzonReportCatalogItem(OzonReportArticle):
    subject: str | None = None
    manager: str | None = None


class OzonFilterResult(DtoModel):
    ok: bool = True
    filters: OzonReportFilters
    category_unavailable_stores: tuple[str, ...] = ()


class OzonProfitReport(DtoModel):
    ok: bool = True
    marketplace: str = "OZON"
    period_from: date
    period_to: date
    rows: tuple[OzonProfitRow, ...]
    category_rows: tuple[OzonProfitRow, ...] = ()
    totals: OzonProfitMetrics
    filters: OzonReportFilters
    category_unavailable_stores: tuple[str, ...] = ()
    group_by: OzonReportGroup = OzonReportGroup.PRODUCT
    daily_details: bool = False
    pagination: OzonReportPagination = OzonReportPagination()


class OzonTargetRecord(OzonProductGoals):
    ok: bool = True
    revision: str = ""


class OzonTargetConflict(Exception):
    pass


class OzonTargetChange(DtoModel):
    article: str = Field(min_length=1)
    revision: str = ""
    target_drr_percent: Percent | None = None
    target_roi_percent: RoiPercent | None = None


class OzonTargetPreview(DtoModel):
    article: str = Field(min_length=1)
    target_drr_percent: Percent
    target_roi_percent: RoiPercent
    values: OzonCalculatorValues | None = None


class OzonTargetSave(DtoModel):
    store_slug: str
    change: OzonTargetChange
    user_id: int
    user_name: str
    saved_at: datetime


class OzonTargetWeekly(DtoModel):
    period_from: date
    period_to: date
    days: int
    spend: Number | None
    orders_amount: Number | None
    orders_count: int | None
    buyout_percent: Number | None
    advertising_per_unit: Number | None
    complete: bool


class OzonTargetCalculation(DtoModel):
    margin: Decimal | None = None
    roi: Decimal | None = None
    costs: OzonCalculatorValues | None = None


class OzonTargetRow(DtoModel):
    store_slug: str
    store_name: str
    article: str
    name: str
    image_url: str | None
    code: str | None
    orders_amount: Number | None
    current_price: Number | None
    current_drr: Number | None
    current_roi: Number | None
    current_missing: tuple[str, ...] = ()
    target_price: Number | None = None
    target_retail_price: Number | None = None
    target_spp_price: Number | None = None
    target_actual_roi: Number | None = None
    target_drr: Number
    target_roi: Number
    target_overridden: bool
    target_revision: str
    weekly: OzonTargetWeekly
    source_values: OzonCalculatorValues
    calculator_values: OzonCalculatorValues | None = None
    target_calculation: OzonTargetCalculation | None = None
    issues: tuple[OzonTargetIssue, ...] = ()
    missing: tuple[str, ...] = ()


class OzonTargetReport(DtoModel):
    ok: bool = True
    period_from: date
    period_to: date
    rows: tuple[OzonTargetRow, ...]


class OzonGoalSettings(DtoModel):
    ok: bool = True
    revision: int
    values: OzonCabinetGoals


class OzonGoalSettingsChange(OzonCabinetGoals):
    revision: int = Field(ge=0)


class OzonTargetExport(DtoModel):
    rows: tuple["OzonTargetExportIdentity", ...] = Field(max_length=50_000)


class OzonTargetExportIdentity(OzonReportIdentity):
    model_config = ConfigDict(extra="ignore")


class OzonExportColumn(DtoModel):
    key: str
    label: str
    format: str = "number"


class OzonReportColumns(DtoModel):
    profit_title: str
    daily_title: str
    target_title: str
    totals_label: str
    profit: tuple[OzonExportColumn, ...]
    daily: tuple[OzonExportColumn, ...]
    daily_diagnostics: tuple[OzonExportColumn, ...]
    target: tuple[OzonExportColumn, ...]
