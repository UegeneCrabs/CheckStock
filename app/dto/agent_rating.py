from datetime import date, datetime

from pydantic import Field, model_validator

from app.core.constants import AGENT_WB_RATING, WbRatingStatus
from app.dto.common import DtoModel


class RatingRequest(DtoModel):
    store: str
    date_from: date
    date_to: date
    article: str | None = None
    search: str | None = None
    brand: str | None = None
    manager: str | None = None
    subject: str | None = None
    rating_below: float | None = Field(
        default=None, ge=0, le=AGENT_WB_RATING.maximum_rating, allow_inf_nan=False
    )
    drop_at_least: float | None = Field(
        default=None, ge=0, le=AGENT_WB_RATING.maximum_rating, allow_inf_nan=False
    )
    tolerance: float = Field(default=0, ge=0, le=AGENT_WB_RATING.maximum_rating, allow_inf_nan=False)
    status: WbRatingStatus | None = None

    @model_validator(mode="after")
    def validate_period(self) -> "RatingRequest":
        days = (self.date_to - self.date_from).days + 1
        if not 1 <= days <= AGENT_WB_RATING.maximum_period_days or self.date_from.toordinal() <= days:
            raise ValueError("Некорректные границы периода рейтинга")
        return self


class RatingCard(DtoModel):
    store_slug: str
    nm_id: str
    article: str
    name: str
    brand: str | None
    manager: str | None
    subject: str | None


class RatingSnapshot(DtoModel):
    nm_id: str
    day: date
    rating: float
    reviews_count: int | None
    observed_at: datetime


class RatingLatest(DtoModel):
    nm_id: str
    rating: float | None
    reviews_count: int | None
    observed_at: datetime


class RatingSources(DtoModel):
    cards: tuple[RatingCard, ...]
    history: tuple[RatingSnapshot, ...]
    latest: tuple[RatingLatest, ...]


class RatingComparisonRow(RatingCard):
    latest_rating: float | None
    reviews_count: int | None
    latest_observed_at: datetime | None
    current_rating: float | None
    current_observed_day: date | None
    current_observed_at: datetime | None
    previous_rating: float | None
    previous_observed_day: date | None
    previous_observed_at: datetime | None
    change: float | None
    status: WbRatingStatus


class RatingHistoryRow(RatingCard):
    day: date
    rating: float | None
    reviews_count: int | None
    observed_at: datetime | None


class RatingTotals(DtoModel):
    checked: int
    problem: int
    healthy: int
    no_data: int


class RatingComparison(DtoModel):
    rows: tuple[RatingComparisonRow, ...]
    totals: RatingTotals
    previous_from: date
    previous_to: date


class RatingHistory(DtoModel):
    rows: tuple[RatingHistoryRow, ...]
    observed_days: int
    missing_days: int
