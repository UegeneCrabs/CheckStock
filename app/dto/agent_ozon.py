from datetime import date
from http import HTTPStatus
from typing import Literal, NamedTuple

from pydantic import Field, JsonValue

from app.core.constants import AgentOzonAnalysisStatus, AgentOzonDataStatus
from app.dto.common import DtoModel
from app.dto.identity import SectionName


class AgentOzonReportSpec(NamedTuple):
    section: SectionName
    marketplace_specific: bool
    filters: str
    description: str


class AgentOzonRows(DtoModel):
    rows: list[dict[str, JsonValue]]
    context: dict[str, JsonValue] = Field(default_factory=dict)
    sources: list[dict[str, JsonValue]] = Field(default_factory=list)


class AgentOzonSummaryMetrics(DtoModel):
    available: bool = True
    products_count: int
    orders_complete: bool
    orders_amount: float | None
    ordered_products_report_margin: float | None
    margin_complete: bool
    margin_included_products: int
    margin_excluded_products: int
    data_status: AgentOzonDataStatus


class AgentOzonSummaryStore(DtoModel):
    store: str
    name: str
    current: AgentOzonSummaryMetrics
    previous: AgentOzonSummaryMetrics
    turnover_change_rub: float | None
    turnover_change_percent: float | None


class AgentOzonStoreFailure(DtoModel):
    store: str
    status_code: int = HTTPStatus.SERVICE_UNAVAILABLE


class AgentOzonSummaryResponse(DtoModel):
    marketplace: Literal["OZON"] = "OZON"
    currency: Literal["RUB"] = "RUB"
    date_from: date
    date_to: date
    compare_from: date
    compare_to: date
    rows: list[AgentOzonSummaryStore]
    failed_stores: list[AgentOzonStoreFailure]
    next_offset: None = None
    warnings: tuple[str, ...]


class AgentOzonAnalysisTotals(DtoModel):
    checked: int
    matched: int
    unknown: int
    no_match: int


class AgentOzonAnalysisRow(DtoModel):
    store: str
    marketplace: Literal["OZON"] = "OZON"
    article: str
    name: str
    manager: str | None
    orders_amount: float | None
    status: AgentOzonAnalysisStatus
    missing_data: list[str]
    available_stock: float | None = None
    stock_presence_confirmed: bool | None = None
    stock_observed_at: str | None = None
    orders_count: float | None = None
    previous_orders_amount: float | None = None
    turnover_change_rub: float | None = None
    turnover_change_percent: float | None = None
    drr: float | None = None
    roi: float | None = None
    impressions: float | None = None
    previous_impressions: float | None = None
    impressions_change: float | None = None
    campaigns: list[dict[str, JsonValue]] = Field(default_factory=list)
    goal_week: float | None = None
    goal_for_period: float | None = None
    shortfall: float | None = None


class AgentOzonAnalysisResponse(DtoModel):
    scenario: str
    marketplace: Literal["OZON"] = "OZON"
    currency: Literal["RUB"] = "RUB"
    date_from: date
    date_to: date
    compare_from: date | None
    compare_to: date | None
    rows: list[AgentOzonAnalysisRow]
    total_rows: int
    next_offset: int | None
    totals: AgentOzonAnalysisTotals
    unassessed_sample: list[AgentOzonAnalysisRow]
    failed_stores: list[AgentOzonStoreFailure]
    complete: bool
    warnings: tuple[str, ...]
