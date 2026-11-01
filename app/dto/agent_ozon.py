from pydantic import Field, JsonValue

from app.dto.common import DtoModel


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
    data_status: str


class AgentOzonSummaryStore(DtoModel):
    store: str
    name: str
    current: AgentOzonSummaryMetrics
    previous: AgentOzonSummaryMetrics
    turnover_change_rub: float | None
    turnover_change_percent: float | None
