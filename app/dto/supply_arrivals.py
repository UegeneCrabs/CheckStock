from datetime import date, datetime
from typing import Annotated

from pydantic import Field, NonNegativeInt, StrictBool

from app.dto.common import DtoModel

SupplyArrivalId = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class SupplyStockState(DtoModel):
    supply_id: SupplyArrivalId
    checked: bool = False
    version: NonNegativeInt = 0
    marked_at: datetime | None = None
    marked_by: str | None = None


class SupplyStockChange(DtoModel):
    checked: StrictBool
    expected_version: NonNegativeInt = Field(strict=True)


class SupplyStockCommand(DtoModel):
    source_key: str
    supply_id: SupplyArrivalId
    change: SupplyStockChange
    user_id: int
    user_name: str
    occurred_at: datetime


class SupplyArrival(DtoModel):
    row: int = Field(ge=1)
    store_slug: str = ""
    project: str = ""
    order: str = Field(min_length=1)
    group: str = ""
    file: str = ""
    category: str = ""
    warehouse: str = ""
    status: str = ""
    arrival: date | None = None
    volume: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    weight: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    boxes: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    shipping: str = ""
    warnings: tuple[str, ...] = ()


class SupplyArrivalRow(SupplyArrival):
    supply_id: SupplyArrivalId
    stock: SupplyStockState
    stock_editable: bool


class SupplyArrivalsReport(DtoModel):
    rows: tuple[SupplyArrival, ...]
    last_attempt: datetime | None
    last_success: datetime | None
    error: str
    sheet_title: str
    running: bool
    stale: bool
    today: date
    source_url: str


class SupplyArrivalsStockReport(SupplyArrivalsReport):
    rows: tuple[SupplyArrivalRow, ...]
    can_edit_stock: bool


class SupplyArrivalMissingError(Exception):
    pass


class SupplyStockConflictError(Exception):
    pass
