from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReportQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    days: Literal[7, 14, 30] = 14
    week: date | None = None
    include_history: bool = False
    details: bool = False
    store: str | None = Field(default=None, min_length=1, max_length=100)
    marketplace: Literal["WB", "OZON", "YANDEX MARKET"] = "WB"
    date_from: date | None = None
    date_to: date | None = None
    article: str | None = Field(default=None, min_length=1, max_length=100)
    search: str | None = Field(default=None, min_length=1, max_length=100)
    warehouse: str | None = Field(default=None, min_length=1, max_length=150)
    scheme: str | None = Field(
        default=None, pattern=r"^(fbo|fbs|rfbs|ff|transit|fbs_[a-zA-Z0-9_-]+)$", max_length=100
    )
    view: Literal["details", "summary"] = "details"
    fulfillment: str | None = Field(default=None, min_length=1, max_length=150)
    manager: str | None = Field(default=None, min_length=1, max_length=150)
    kind: str | None = Field(default=None, min_length=1, max_length=50)
    group_by: Literal["article", "day"] = "article"
    sort_by: str | None = Field(default=None, max_length=60)
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)
    order_id: str | None = Field(default=None, min_length=1, max_length=100)
    supply_id: str | None = Field(default=None, min_length=1, max_length=100)
    status: str | None = Field(default=None, min_length=1, max_length=100)
    cost_view: Literal[
        "summary", "deliveries", "transfers", "shipments", "fbs_transfers", "fbs_sales", "fbs_actual_sales"
    ] = "summary"
    is_new: bool | None = None
    active_only: bool = True
    ctr_below: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    tag_column: Literal["goal_week", "goal_day", "status", "ends", "code", "fact", "plan"] | None = None
    tag_value: str | None = Field(default=None, min_length=1, max_length=100)
    tag_operator: Literal["eq", "gte", "lte"] = "eq"

    @field_validator("days", mode="before")
    @classmethod
    def parse_days(cls, value: object) -> object:
        return int(value) if isinstance(value, str) and value.isdecimal() else value

    @field_validator("store")
    @classmethod
    def normalize_store(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None
