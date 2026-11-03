from datetime import date

from pydantic import Field, field_validator

from app.core.constants import OZON_CALENDAR
from app.dto.common import DtoModel
from app.dto.ozon_cabinet import OzonCabinetRecord, OzonCabinetSave
from app.dto.ozon_calculator import ScenarioPercent
from app.dto.ozon_calendar import OzonCalendarEvent, OzonCalendarJournal, OzonCalendarQuery
from app.dto.ozon_margin import OzonMarginSnapshot
from app.dto.ozon_prices import PriceValue


class OzonTaxHistoryCommand(DtoModel):
    store: str = Field(min_length=1)
    through: date
    vat_percent: ScenarioPercent
    usn_percent: ScenarioPercent
    operation: str = Field(min_length=1)
    reason: str = Field(
        min_length=OZON_CALENDAR.reason_min_length, max_length=OZON_CALENDAR.reason_max_length
    )

    @field_validator("through")
    @classmethod
    def past_or_today(cls, value: date) -> date:
        return OzonCalendarQuery.past_or_today(value)


class OzonTaxHistoryRow(DtoModel):
    snapshot: OzonMarginSnapshot
    journal: OzonCalendarJournal | None = None
    prices: tuple[PriceValue, ...] = ()
    repaired: bool = False


class OzonTaxHistoryData(DtoModel):
    cabinet: OzonCabinetRecord
    rows: tuple[OzonTaxHistoryRow, ...]


class OzonTaxHistoryChange(DtoModel):
    before: OzonTaxHistoryRow
    snapshot: OzonMarginSnapshot
    journal: OzonCalendarJournal
    event: OzonCalendarEvent


class OzonTaxHistoryReport(DtoModel):
    store: str
    snapshots: int
    days: int
    first_day: date | None
    last_day: date | None
    changes: int
    already_applied: int
    unresolved_usn: int
    cabinet_changed: bool


class OzonTaxHistoryPlan(DtoModel):
    command: OzonTaxHistoryCommand
    cabinet: OzonCabinetRecord
    cabinet_change: OzonCabinetSave | None = None
    changes: tuple[OzonTaxHistoryChange, ...]
    report: OzonTaxHistoryReport
