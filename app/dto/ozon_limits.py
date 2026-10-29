from typing import Annotated

from pydantic import AwareDatetime, Field

from app.core.constants import AdvertisingError, OzonReservationState
from app.dto.common import DtoModel

Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class OzonRateScope(DtoModel):
    key: str
    minimum_spacing: Seconds


class OzonRateState(DtoModel):
    next_at: AwareDatetime
    spacing: Seconds
    successes: int = 0


class OzonRateFeedback(DtoModel):
    status: int
    retry_seconds: Seconds | None = None
    remaining: int | None = None


class PerformanceAccount(DtoModel):
    store_slug: str
    account_scope: str
    organization_scope: str


class PerformanceIdentity(PerformanceAccount):
    accounts: tuple[PerformanceAccount, ...] = ()


class PerformanceQuotaRequest(DtoModel):
    identity: PerformanceIdentity
    export_units: int = Field(default=0, ge=0)
    account_export_limit: int = Field(ge=0)
    request_key: str | None = None
    generation: bool = False
    http_units: int = Field(default=1, ge=0, le=1)


class PerformanceReservation(DtoModel):
    id: str
    export_units: int


class PerformanceAdmission(DtoModel):
    reservation: PerformanceReservation | None = None
    error: AdvertisingError | None = None


class PerformanceCompletion(DtoModel):
    reservation: PerformanceReservation
    status: int | None = None
    state: OzonReservationState


class PerformanceLease(DtoModel):
    token: str


class PendingPerformanceReport(DtoModel):
    store_slug: str
    request_key: str
    uuid: str
