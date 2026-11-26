from datetime import date
from typing import Annotated, Final

from pydantic import Field

from app.dto.common import DtoModel

WEEKLY_GOAL_MAX: Final = 2_147_483_647
GoalQuantity = Annotated[int, Field(strict=True, ge=0, le=WEEKLY_GOAL_MAX)]


class WeeklyGoal(DtoModel):
    quantity: GoalQuantity | None
    version: Annotated[int, Field(strict=True, ge=0, le=WEEKLY_GOAL_MAX)]


class WeeklyGoalState(WeeklyGoal):
    applied_quantity: GoalQuantity | None


class WeeklyGoalChange(DtoModel):
    store: str = Field(min_length=1, max_length=100)
    article: str = Field(min_length=1, max_length=200)
    week: date
    quantity: GoalQuantity | None
    version: Annotated[int, Field(strict=True, ge=0, lt=WEEKLY_GOAL_MAX)]


class WeeklyGoalResponse(DtoModel):
    ok: bool = True
    goal: WeeklyGoal


class WeeklyGoalsApply(DtoModel):
    week: date


class WeeklyGoalsApplied(DtoModel):
    ok: bool = True
    changed: int
