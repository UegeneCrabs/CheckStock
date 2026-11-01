from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scenario: Literal[
        "stock_without_orders",
        "turnover_drop",
        "drr_negative_roi",
        "stock_impressions_drop",
        "active_low_ctr",
        "goal_gap",
    ]
    marketplace: Literal["WB", "OZON"] = "WB"
    date_from: date
    date_to: date
    compare_from: date | None = None
    compare_to: date | None = None
    store: str | None = Field(default=None, min_length=1, max_length=100)
    manager: str | None = Field(default=None, min_length=1, max_length=100)
    code: str | None = Field(default=None, min_length=1, max_length=100)
    is_new: bool | None = None
    fulfillment: str | None = Field(default=None, min_length=1, max_length=100)
    drr_max: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)
    ctr_below: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    include_unknown: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)
