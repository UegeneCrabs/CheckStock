from datetime import datetime
from enum import StrEnum

from pydantic import Field, PositiveInt

from app.dto.common import DtoModel


class DecisionStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class DecisionSyncRequest(DtoModel):
    store: str = Field(default="", max_length=100)


class DecisionStatusRequest(DtoModel):
    fingerprint: str = Field(min_length=3, max_length=500)
    status: DecisionStatus


class SetDecisionStatusCommand(DtoModel):
    request: DecisionStatusRequest
    user_id: PositiveInt
    user_name: str
    updated_at: datetime


class DecisionAction(DtoModel):
    fingerprint: str
    status: DecisionStatus
    user_id: PositiveInt | None = None
    user_name: str | None = None
    updated_at: datetime
