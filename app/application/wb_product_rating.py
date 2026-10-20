from collections.abc import Iterable

from app.application.ports import WbRatingWriter
from app.dto.wb_product_rating import WbRatingObservation


class WbProductRatingService:
    def __init__(self, writer: WbRatingWriter) -> None:
        self._writer = writer

    def record(self, observations: Iterable[WbRatingObservation]) -> int:
        all_observations = tuple(observations)
        valid = tuple(
            item
            for item in all_observations
            if item.rating is not None
            and 0 < item.rating <= 5
            and item.observed_at.tzinfo is not None
            and (item.reviews_count is None or item.reviews_count >= 0)
        )
        return self._writer.upsert(all_observations, valid) if all_observations else 0
