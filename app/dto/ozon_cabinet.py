from datetime import date, datetime
from typing import Annotated, Self

from pydantic import Field, model_validator

from app.core.constants import OZON_CABINET, OzonBuyoutState, OzonRoiCode
from app.dto.common import DtoModel

Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
RoiPercent = Annotated[float, Field(ge=0, le=OZON_CABINET.max_roi_percent, allow_inf_nan=False)]


class OzonCabinetGoals(DtoModel):
    target_drr_percent: Percent = 0
    target_roi_percent: RoiPercent = OZON_CABINET.default_roi_percent
    target_roi_by_code: dict[OzonRoiCode, RoiPercent | None] = Field(
        default_factory=lambda: dict[OzonRoiCode, float | None](OZON_CABINET.roi_by_code)
    )

    @model_validator(mode="after")
    def complete_codes(self) -> Self:
        if set(self.target_roi_by_code) != set(OzonRoiCode):
            raise ValueError("All ROI codes are required")
        return self


class OzonBuyoutSettings(DtoModel):
    period_days: int = Field(default=OZON_CABINET.default_period_days, ge=1, le=OZON_CABINET.max_period_days)
    default_percent: Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)] | None = None


class OzonCabinetExpenses(DtoModel):
    acquiring_percent: Percent | None = None
    vat_percent: Percent | None = None
    usn_percent: Percent | None = None


class OzonCabinetValues(DtoModel):
    goals: OzonCabinetGoals = OzonCabinetGoals()
    buyout: OzonBuyoutSettings = OzonBuyoutSettings()
    expenses: OzonCabinetExpenses = OzonCabinetExpenses()


class OzonCabinetRecord(DtoModel):
    store_slug: str
    revision: int = 0
    values: OzonCabinetValues = OzonCabinetValues()
    updated_at: datetime | None = None
    updated_by: str | None = None


class OzonTeamCommission(DtoModel):
    percent: float | None = None
    values: tuple[float, ...] = ()
    products: int = 0
    missing_products: int = 0
    updated_at: datetime | None = None


class OzonCabinetView(OzonCabinetRecord):
    store_name: str
    team_commission: OzonTeamCommission = OzonTeamCommission()


class OzonCabinets(DtoModel):
    items: tuple[OzonCabinetView, ...]


class OzonCabinetUpdate(DtoModel):
    revision: int = Field(ge=0)
    goals: OzonCabinetGoals | None = None
    buyout: OzonBuyoutSettings | None = None
    expenses: OzonCabinetExpenses | None = None

    @model_validator(mode="after")
    def one_section(self) -> Self:
        if sum(value is not None for value in (self.goals, self.buyout, self.expenses)) != 1:
            raise ValueError("Exactly one settings section is required")
        return self


class OzonCabinetSave(DtoModel):
    store_slug: str
    change: OzonCabinetUpdate
    user_name: str
    saved_at: datetime


class OzonBuyoutProduct(DtoModel):
    article: str
    received: int
    cancelled: int
    returned: int = 0
    percent: float | None


class OzonBuyoutSnapshot(DtoModel):
    formula_version: int = OZON_CABINET.legacy_buyout_formula_version
    store_slug: str
    day: date
    period_from: date
    period_to: date
    observed_at: datetime
    products: tuple[OzonBuyoutProduct, ...]
    state: OzonBuyoutState
    missing_dates: tuple[date, ...] = ()
    error: str | None = None


class OzonEffectiveBuyout(DtoModel):
    percent: float | None = None
    actual_percent: float | None = None
    default_applied: bool = False
    state: OzonBuyoutState = OzonBuyoutState.MISSING
    stale: bool = False
    error: str | None = None
    period_from: date | None = None
    period_to: date | None = None
    observed_at: datetime | None = None
    received: int | None = None
    cancelled: int | None = None
    returned: int | None = None
    retained: int | None = None


class OzonProductGoals(DtoModel):
    target_drr_percent: Percent | None = None
    target_roi_percent: RoiPercent | None = None


class OzonEffectiveParameters(DtoModel):
    target_drr_percent: float
    target_roi_percent: float
    expenses: OzonCabinetExpenses
    buyout: OzonEffectiveBuyout


class OzonBuyoutSyncResult(DtoModel):
    stores: int = 0
    products: int = 0
    error: str | None = None
