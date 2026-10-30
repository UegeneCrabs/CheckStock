from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, field_validator, model_validator

from app.core.constants import (
    OZON_CALENDAR,
    OZON_SCENARIO,
    TIME,
    OzonCalendarEventKind,
    OzonCalendarField,
    WebErrorCode,
)
from app.dto.common import DtoModel
from app.dto.ozon_advertising import AdvertisingSourceStatus
from app.dto.ozon_economics import OzonTurnoverData
from app.dto.ozon_margin import OzonMarginDay, OzonMarginSnapshot
from app.dto.ui import UiStore

CalendarNumber = Annotated[
    Decimal,
    Field(
        ge=-OZON_SCENARIO.max_money,
        le=OZON_SCENARIO.max_money,
        allow_inf_nan=False,
        max_digits=OZON_SCENARIO.number_max_digits,
        decimal_places=OZON_SCENARIO.number_decimal_places,
    ),
]


class OzonCalendarQuery(DtoModel):
    store: str = Field(min_length=1)
    day: date
    q: str = ""
    page: int = Field(default=1, ge=1)
    page_size: int = OZON_CALENDAR.default_page_size

    @field_validator("day")
    @classmethod
    def past_or_today(cls, value: date) -> date:
        if value > datetime.now(TIME.moscow_timezone).date():
            raise ValueError(WebErrorCode.INVALID_PERIOD)
        return value

    @field_validator("page_size")
    @classmethod
    def supported_size(cls, value: int) -> int:
        if value not in OZON_CALENDAR.page_sizes:
            raise ValueError(WebErrorCode.INVALID_TARGET)
        return value


class OzonCalendarKey(DtoModel):
    store: str = Field(min_length=1)
    day: date
    article: str = Field(min_length=1, max_length=OZON_CALENDAR.article_max_length)

    @field_validator("day")
    @classmethod
    def past_or_today(cls, value: date) -> date:
        return OzonCalendarQuery.past_or_today(value)


class OzonCalendarCorrection(OzonCalendarKey):
    field: OzonCalendarField
    value: CalendarNumber | None = None
    undo: bool = False
    token: str = Field(min_length=1)
    reason: str = Field(
        min_length=OZON_CALENDAR.reason_min_length, max_length=OZON_CALENDAR.reason_max_length
    )
    preview_token: str = ""

    @field_validator("value", mode="before")
    @classmethod
    def numeric_value(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError(WebErrorCode.INVALID_TARGET)
        return value

    @model_validator(mode="after")
    def valid_value(self) -> Self:
        if not self.undo and self.value is None:
            raise ValueError(WebErrorCode.INVALID_TARGET)
        if self.value is not None and self.value < 0 and self.field not in OZON_CALENDAR.signed_fields:
            raise ValueError(WebErrorCode.INVALID_TARGET)
        if self.field == OzonCalendarField.BUYOUT and self.value is not None:
            if self.value > OZON_SCENARIO.percent_factor:
                raise ValueError(WebErrorCode.INVALID_TARGET)
        return self


class OzonCalendarOverride(DtoModel):
    field: OzonCalendarField
    value: CalendarNumber


class OzonCalendarJournal(DtoModel):
    revision: int = 0
    original: OzonMarginSnapshot
    source: OzonMarginSnapshot
    overrides: tuple[OzonCalendarOverride, ...] = ()


class OzonCalendarProduct(DtoModel):
    article: str
    name: str
    barcode: str
    barcodes: tuple[str, ...] = ()
    image_url: str | None = None


class OzonCalendarValues(DtoModel):
    seller_price: float | None = None
    purchase: float | None = None
    fulfillment: float | None = None
    commission_amount: float | None = None
    logistics_amount: float | None = None
    storage_amount: float | None = None
    advertising_amount: float | None = None
    acquiring_amount: float | None = None
    team_amount: float | None = None
    unallocated_amount: float | None = None
    vat_amount: float | None = None
    usn_amount: float | None = None
    buyout_percent: float | None = None
    orders_count: int | None = None
    advertising_spend: float | None = None


class OzonCalendarResult(DtoModel):
    margin: float | None = None
    roi: float | None = None
    day_profit: float | None = None
    complete: bool = False
    daily_complete: bool = False
    no_expected_buyouts: bool = False
    missing: tuple[str, ...] = ()
    daily_missing: tuple[str, ...] = ()


class OzonCalendarRow(DtoModel):
    product: OzonCalendarProduct
    snapshot: bool
    values: OzonCalendarValues
    result: OzonCalendarResult
    corrected: tuple[OzonCalendarField, ...] = ()


class OzonCalendarMissing(DtoModel):
    article: str
    parameters: tuple[str, ...]
    unavailable: bool


class OzonCalendarListing(DtoModel):
    rows: tuple[OzonCalendarRow, ...]
    total: int
    complete: int
    day_profit: float | None
    profit_complete: bool
    profit_missing: tuple[OzonCalendarMissing, ...]
    first_day: date | None
    day: date
    preliminary: bool
    can_edit: bool = True


class OzonCalendarObservation(DtoModel):
    values: OzonCalendarValues
    captured_at: datetime
    snapshot: OzonMarginSnapshot


class OzonCalendarEvent(DtoModel):
    at: datetime
    actor: str
    kind: OzonCalendarEventKind
    field: OzonCalendarField | None = None
    before: Decimal | OzonCalendarValues | None = None
    after: Decimal | OzonCalendarValues | None = None
    reason: str = ""


class OzonCalendarCell(DtoModel):
    token: str
    values: OzonCalendarValues
    result: OzonCalendarResult
    original: OzonCalendarObservation
    source: OzonCalendarObservation
    overrides: dict[OzonCalendarField, Decimal]
    events: tuple[OzonCalendarEvent, ...]
    version: int


class OzonCalendarPreview(DtoModel):
    before: float | None
    after: float | None
    before_result: OzonCalendarResult
    after_result: OzonCalendarResult
    preview_token: str


class OzonCalendarEvidence(DtoModel):
    key: OzonCalendarKey
    journal: OzonCalendarJournal
    calculation: OzonMarginDay
    events: tuple[OzonCalendarEvent, ...] = ()


class OzonCalendarDay(DtoModel):
    product: OzonCalendarProduct
    journal: OzonCalendarJournal | None
    calculation: OzonMarginDay


class OzonCalendarProductSource(DtoModel):
    product: OzonCalendarProduct
    journal: OzonCalendarJournal | None


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonCalendarSources:
    products: tuple[OzonCalendarProductSource, ...]
    first_day: date | None
    turnover: OzonTurnoverData
    advertising: tuple[AdvertisingSourceStatus, ...]
    credentials_present: bool


class OzonCalendarSave(DtoModel):
    key: OzonCalendarKey
    expected: OzonCalendarJournal
    journal: OzonCalendarJournal
    event: OzonCalendarEvent


class OzonCalendarConflict(Exception):
    pass


class OzonCalendarContext(DtoModel):
    stores: tuple[UiStore, ...]
    today: date
