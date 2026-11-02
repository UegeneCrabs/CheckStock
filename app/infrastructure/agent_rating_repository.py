from datetime import timedelta

from sqlalchemy import text

from app.core.constants import AGENT_WB_RATING
from app.dto.agent_rating import RatingCard, RatingLatest, RatingRequest, RatingSnapshot, RatingSources
from app.infrastructure.async_database import AsyncDatabase


class AgentRatingRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self.database = database

    async def read(self, query: RatingRequest) -> RatingSources:
        period_days = (query.date_to - query.date_from).days + 1
        first_day = query.date_from - timedelta(days=period_days)
        card_filter = " AND nm_id=:nm_id" if query.article else ""
        card_params = {"nm_id": query.article.partition(" / ")[0].strip()} if query.article else {}
        async with self.database.transaction() as connection:
            catalog = await connection.execute(
                text(
                    "SELECT items.store_slug, items.article, items.name, categories.brand, "
                    "categories.category AS subject, source.manager FROM stock_items items "
                    "LEFT JOIN unit_economics_1c_product_categories categories "
                    "ON categories.stock_item_id=items.id "
                    "LEFT JOIN unit_economics_1c_source_values source ON source.stock_item_id=items.id "
                    "WHERE items.store_slug=:store AND items.marketplace='WB' AND items.is_service=0 "
                    "ORDER BY items.article"
                ),
                {"store": query.store},
            )
            cards = tuple(
                RatingCard.model_validate({**item, "nm_id": str(item["article"]).partition(" / ")[0].strip()})
                for item in catalog.mappings()
            )
            history = await connection.execute(
                text(
                    "SELECT nm_id, day, rating, reviews_count, observed_at FROM wb_product_rating_daily "
                    "WHERE store_slug=:store AND day>=:start AND day<=:end "
                    "AND rating>0 AND rating<=:maximum" + card_filter + " ORDER BY day, observed_at"
                ),
                {
                    "store": query.store,
                    "start": first_day.isoformat(),
                    "end": query.date_to.isoformat(),
                    "maximum": AGENT_WB_RATING.maximum_rating,
                    **card_params,
                },
            )
            snapshots = tuple(RatingSnapshot.model_validate(item) for item in history.mappings())
            latest = await connection.execute(
                text(
                    "SELECT nm_id, rating, reviews_count, synced_at AS observed_at "
                    "FROM unit_economics_1c_product_reputation WHERE store_slug=:store" + card_filter
                ),
                {"store": query.store, **card_params},
            )
            return RatingSources(
                cards=cards,
                history=snapshots,
                latest=tuple(RatingLatest.model_validate(item) for item in latest.mappings()),
            )
