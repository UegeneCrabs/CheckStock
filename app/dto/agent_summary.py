from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    marketplace: Literal["WB", "OZON"] = "WB"
    date_from: date
    date_to: date
    compare_from: date | None = None
    compare_to: date | None = None
    store: str | None = Field(default=None, min_length=1, max_length=100)
    manager: str | None = Field(default=None, min_length=1, max_length=100)
