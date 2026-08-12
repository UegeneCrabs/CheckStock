from typing import Literal

from app.dto.common import DtoModel


class HealthStatus(DtoModel):
    status: Literal["ok"] = "ok"


class ReadinessStatus(DtoModel):
    status: Literal["ok", "unavailable"]
    database: Literal["ok", "unavailable"]


class SyncFailure(DtoModel):
    target: str
    error_type: str


class SyncGroupReport(DtoModel):
    group: str
    succeeded: tuple[str, ...]
    failed: tuple[SyncFailure, ...]


class TokenRefreshResult(DtoModel):
    refreshed: bool
