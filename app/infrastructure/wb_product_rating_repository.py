from collections.abc import Callable
from datetime import UTC
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.domain import MOSCOW_TIMEZONE
from app.dto.wb_product_rating import WbRatingObservation
from app.infrastructure.database import database_for_path
from app.infrastructure.orm import UnitEconomics1CProductReputationRecord, WbProductRatingDailyRecord


class SqlAlchemyWbRatingWriter:
    def __init__(self, database_path: Callable[[], Path]) -> None:
        self._database_path = database_path

    def upsert(
        self,
        observations: tuple[WbRatingObservation, ...],
        valid_ratings: tuple[WbRatingObservation, ...],
    ) -> int:
        database = database_for_path(self._database_path())
        current: dict[tuple[str, str], WbRatingObservation] = {}
        for item in observations:
            key = (item.store_slug, item.nm_id)
            previous = current.get(key)
            if previous is None or item.observed_at >= previous.observed_at:
                current[key] = item
        latest: dict[tuple[str, str, str], WbRatingObservation] = {}
        for item in valid_ratings:
            day = item.observed_at.astimezone(MOSCOW_TIMEZONE).date().isoformat()
            key = (item.store_slug, item.nm_id, day)
            previous = latest.get(key)
            if previous is None or item.observed_at >= previous.observed_at:
                latest[key] = item
        with database.unit_of_work() as uow:
            session = uow.session
            assert session is not None
            insert = sqlite_insert if database.dialect_name == "sqlite" else postgres_insert
            for item in current.values():
                statement = insert(UnitEconomics1CProductReputationRecord).values(
                    store_slug=item.store_slug,
                    nm_id=item.nm_id,
                    rating=item.rating,
                    reviews_count=item.reviews_count,
                    synced_at=item.observed_at.isoformat(),
                )
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=["store_slug", "nm_id"],
                        set_={
                            "rating": statement.excluded.rating,
                            "reviews_count": statement.excluded.reviews_count,
                            "synced_at": statement.excluded.synced_at,
                        },
                        where=(
                            UnitEconomics1CProductReputationRecord.synced_at < statement.excluded.synced_at
                        ),
                    )
                )
            for key, item in latest.items():
                observed_at = item.observed_at.astimezone(UTC).isoformat()
                statement = insert(WbProductRatingDailyRecord).values(
                    store_slug=item.store_slug,
                    nm_id=item.nm_id,
                    day=key[2],
                    rating=item.rating,
                    reviews_count=item.reviews_count,
                    observed_at=observed_at,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=["store_slug", "nm_id", "day"],
                    set_={
                        "rating": statement.excluded.rating,
                        "reviews_count": statement.excluded.reviews_count,
                        "observed_at": statement.excluded.observed_at,
                    },
                    where=WbProductRatingDailyRecord.observed_at < statement.excluded.observed_at,
                )
                session.execute(statement)
            uow.commit()
        return len(observations)
