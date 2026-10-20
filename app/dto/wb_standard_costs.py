"""Named contracts for WB comparison; JSON is decoded only at source boundaries."""

from dataclasses import dataclass
from datetime import date
from typing import Literal, Self

from pydantic import ConfigDict, Field, JsonValue, model_validator

from app.dto.common import DtoModel


class ReportModel(DtoModel):
    # Preserve external text verbatim; completed contracts remain immutable.
    model_config = ConfigDict(str_strip_whitespace=False)


class Coverage(ReportModel):
    dates: tuple[str, ...] = Field(default_factory=tuple)
    days: int = 0
    expected_days: int = 0
    complete: bool = False
    period_from: str | None = None
    period_to: str | None = None
    missing_dates: tuple[str, ...] = Field(default_factory=tuple)


class Turnover(ReportModel):
    value: float | None = None
    coverage: Coverage | None = None


class CostValues(ReportModel):
    purchase: float | None = None
    fulfillment: float | None = None
    advertising: float | None = None
    acquiring: float | None = None
    team: float | None = None
    vat: float | None = None
    tax: float | None = None
    logistics: float | None = None
    storage_total: float | None = None
    commission: float | None = None


class MarginRoi(ReportModel):
    margin: float | None = None
    roi: float | None = None


class Scenario(CostValues, MarginRoi):
    retail: float | None = None
    missing: tuple[str, ...] = Field(default_factory=tuple)
    complete: bool = False
    total_cost: float | None = None


class LogisticsReturns(ReportModel):
    forward: float | None
    reverse: float | None
    volume: float | None
    buyout_percent: float | None
    retries: float | None
    total: float | None
    missing: tuple[str, ...]
    return_base: float
    return_liter: float
    return_source: str
    extra_forward: float | None
    reverse_per_buyout: float | None


class StorageCalculation(ReportModel):
    basis: str = "weekly_sales"
    days: int | None = None
    daily_rate: float | None = None
    value: float | None = None
    warning: str | None = None


class DeliveryTerm(ReportModel):
    warehouse: str | None
    tariff: str | None
    volume: float | None
    quantity: float
    base: float | None
    extra: float | None
    rate: float | None


class LogisticsCalculation(ReportModel):
    localization_index: float
    localization_index_source: str
    terms: tuple[DeliveryTerm, ...]
    weight: float
    weighted_sum: float
    basis: str
    tariff_date: str


class StorageDetail(ReportModel):
    day: str
    warehouse: str | None
    quantity: float
    volume: float | None
    storage_rate: float | None
    storage: float | None
    delivery_rate: float | None
    tariff: str | None


class CalculationInputs(ReportModel):
    acquiring: float | None = None
    team_commission_percent: float | None = None
    vat_percent: float | None = None
    usn_percent: float | None = None
    osno_percent: float | None = None
    tax_system: str | None = None
    client_price: float | None = None


class ReportRow(ReportModel):
    article: str = ""
    scheme: str = ""
    name: str = ""
    vendor: str = ""
    subject: str = ""
    volume: float | None = None
    sales: float = 0
    returns: float = 0
    turnover: float = 0
    commission_rate: float | None = None
    commission: float | None = None
    storage: float | None = None
    stock_days: float = 0
    logistics: float | None = None
    details: tuple[StorageDetail, ...] = Field(default_factory=tuple)
    missing: tuple[str, ...] = Field(default_factory=tuple)
    sales_turnover: float = 0
    logistics_calculation: LogisticsCalculation | None = None
    logistics_basis: str = ""
    unit_price: float | None = None
    commission_unit: float | None = None
    storage_unit_day: float | None = None
    complete: bool = False
    managers: tuple[str, ...] = Field(default_factory=tuple)
    image_url: str = ""
    calculator_turnover: Turnover = Field(default_factory=Turnover)
    logistics_returns: LogisticsReturns | None = None
    storage_calculation: StorageCalculation = Field(default_factory=StorageCalculation)
    profit: Scenario = Field(default_factory=Scenario)
    current_profit: Scenario = Field(default_factory=Scenario)
    delta: MarginRoi = Field(default_factory=MarginRoi)
    calculation_inputs: CalculationInputs = Field(default_factory=CalculationInputs)
    calculator_date: str | None = None


class Snapshot(ReportModel):
    version: int = 5
    date_from: str = ""
    date_to: str = ""
    updated_at: str = ""
    commission_date: str = ""
    rows: tuple[ReportRow, ...] = Field(default_factory=tuple)
    finance_rows: int = 0
    storage_rows: int = 0
    storage_dates: tuple[str, ...] = Field(default_factory=tuple)
    # JSON metadata keyed by arbitrary WB operation names, not a record contract.
    excluded_operations: dict[str, int] = Field(default_factory=dict)
    sources: tuple[str, ...] = Field(default_factory=tuple)


class StoreReport(ReportModel):
    slug: str
    name: str
    report: Snapshot | None = None
    error: str | None = None


class Reports(ReportModel):
    stores: tuple[StoreReport, ...]


@dataclass(frozen=True)
class ExportItem:
    row: ReportRow
    store_name: str
    period_from: str
    period_to: str


class ExportRow(ReportModel):
    store_slug: str = Field(min_length=1, max_length=80)
    article: str = Field(min_length=1, max_length=100)
    scheme: Literal["FBW", "FBS"]
    period_from: date
    period_to: date

    @model_validator(mode="after")
    def valid_period(self) -> Self:
        if self.period_to < self.period_from:
            raise ValueError("Конец периода раньше начала")
        return self


class ExportRequest(ReportModel):
    rows: tuple[ExportRow, ...] = Field(min_length=1, max_length=20000)


@dataclass(frozen=True)
class ProductPhoto:
    article: str
    url: str


class SourceBundle(ReportModel):
    date_from: str
    date_to: str
    updated_at: str
    commission_date: str
    # Original WB JSON is retained losslessly for audits and rebuilding snapshots.
    finance: list[dict[str, JsonValue]]
    storage: list[dict[str, JsonValue]]
    cards: list[dict[str, JsonValue]]
    tariffs: dict[str, JsonValue]
    commissions: dict[str, JsonValue]
    sources: list[str]


class ProductDetails(CalculationInputs):
    purchase_cost: float | None = None
    buyout_percent: float | None = None
    fulfillment_cost: float | None = None
    vat_value: float | None = None
    usn_value: float | None = None
    osno_value: float | None = None
    commission_value: float | None = None
    commission_percent: float | None = None
    delivery_with_returns: float | None = None
    logistics: float | None = None
    storage_sum: float | None = None
    storage_days: float | None = None
    tax_system: str = "usn"


class ProductPrice(ReportModel):
    current: float | None = None
    with_spp: float | None = None
    snapshot_date: str | None = None


class ProductAdvertising(ReportModel):
    spend_per_order: float | None = None
    coverage: Coverage | None = None


class ProductSettings(ReportModel):
    storage_wb_rub: float | None = None


class ProductEconomics(ReportModel):
    turnover: float | None = None
    turnover_coverage: Coverage | None = None


class CalculatorProduct(ReportModel):
    article: str = ""
    manager: str | None = None
    image_url: str | None = None
    details: ProductDetails = Field(default_factory=ProductDetails)
    price: ProductPrice = Field(default_factory=ProductPrice)
    advertising: ProductAdvertising = Field(default_factory=ProductAdvertising)
    product_settings: ProductSettings = Field(default_factory=ProductSettings)
    economics_7d: ProductEconomics = Field(default_factory=ProductEconomics)
    data_errors: tuple[str, ...] = Field(default_factory=tuple)


class SyncResult(ReportModel):
    ok: bool
    date_from: str | None = None
    date_to: str | None = None
    rows: int | None = None
    skipped: bool = False
    reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class StoreSyncResult:
    store: str
    result: SyncResult


class CalculatorValues(ReportModel):
    retail: float | None = None
    client: float | None = None
    purchase: float | None = None
    fulfillment: float | None = None
    advertisingRub: float | None = None
    acquiringRub: float | None = None
    teamRub: float | None = None
    vatRub: float | None = None
    secondaryTaxRub: float | None = None
    commissionRub: float | None = None
    logistics: float | None = None
    storageTotal: float | None = None
    acquiringPercent: float | None = None
    commission: float | None = None
    team: float | None = None
    vat: float | None = None
    osno: float | None = None
    usn: float | None = None
    storage: float | None = None


@dataclass(frozen=True)
class Comparison:
    standard: Scenario
    current: Scenario
    delta: MarginRoi
    logistics_returns: LogisticsReturns
    storage_calculation: StorageCalculation


class PublishSnapshot(ReportModel):
    store_slug: str = Field(min_length=1)
    snapshot: Snapshot
    raw: SourceBundle | None = None
