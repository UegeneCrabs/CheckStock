from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WbRatingObservation:
    store_slug: str
    nm_id: str
    rating: float | None
    reviews_count: int | None
    observed_at: datetime
