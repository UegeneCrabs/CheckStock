from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from app.core.constants import GOOGLE_STOCK_TEST, GOOGLE_WEEK, ScheduleKind
from app.dto.common import DtoModel
from app.dto.integrations import JobState


class StockTable(DtoModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    headers: tuple[str, ...]
    rows: tuple[tuple[str | int, ...], ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class StockTestOrderPeriod:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class StockTestProductOrders:
    ordered: int
    cancelled: int
    barcode: str = ""
    name: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class StockTestAccountOrders:
    store: str
    orders: dict[str, StockTestProductOrders]
    from_customer: dict[str, int]


@dataclass(frozen=True, slots=True, kw_only=True)
class StockTestCatalogProduct:
    store: str
    article: str
    barcode: str
    name: str


class StockTestCatalog(DtoModel):
    products: tuple[StockTestCatalogProduct, ...]


class StockTestSchedule(DtoModel):
    enabled: bool = False
    schedule_kind: Literal[ScheduleKind.DAILY, ScheduleKind.WEEKLY] = ScheduleKind.WEEKLY
    weekday: int = Field(default=GOOGLE_STOCK_TEST.default_weekday, ge=0, lt=GOOGLE_WEEK.days_per_week)
    run_time: str = Field(
        default=GOOGLE_STOCK_TEST.default_time, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"
    )


class StockTestScheduleState(DtoModel):
    schedule: StockTestSchedule
    activated_at: datetime | None = None
    last_scheduled_at: datetime | None = None


class StockTestExportView(DtoModel):
    schedule: StockTestSchedule
    spreadsheet_url: str
    sheets: tuple[str, ...]
    service_account_email: str
    next_run_at: datetime | None
    state: JobState


class StockTestJobResult(DtoModel):
    error: str | None = None
