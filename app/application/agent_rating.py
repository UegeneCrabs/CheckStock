from datetime import timedelta

from app.application.ports import AgentRatingReader
from app.core.constants import AGENT_WB_RATING, WbRatingStatus
from app.dto.agent_rating import (
    RatingCard,
    RatingComparison,
    RatingComparisonRow,
    RatingHistory,
    RatingHistoryRow,
    RatingRequest,
    RatingSnapshot,
    RatingSources,
    RatingTotals,
)


def selected_cards(query: RatingRequest, sources: RatingSources) -> tuple[RatingCard, ...]:
    cards: dict[str, RatingCard] = {}
    for card in sources.cards:
        if any(
            expected and (actual or "").casefold() != expected.casefold()
            for actual, expected in (
                (card.brand, query.brand),
                (card.manager, query.manager),
                (card.subject, query.subject),
            )
        ):
            continue
        if query.article and query.article not in {card.article, card.nm_id}:
            continue
        if query.search and query.search.casefold() not in f"{card.article} {card.name}".casefold():
            continue
        cards.setdefault(card.nm_id, card)
    return tuple(cards.values())


class AgentRatingService:
    def __init__(self, reader: AgentRatingReader) -> None:
        self.reader = reader

    async def comparison(self, query: RatingRequest) -> RatingComparison:
        sources = await self.reader.read(query)
        days = (query.date_to - query.date_from).days + 1
        previous_from = query.date_from - timedelta(days=days)
        previous_to = query.date_from - timedelta(days=1)
        current: dict[str, RatingSnapshot] = {}
        previous: dict[str, RatingSnapshot] = {}
        for snapshot in sorted(sources.history, key=lambda item: (item.day, item.observed_at)):
            if previous_from <= snapshot.day <= query.date_to:
                (current if snapshot.day >= query.date_from else previous)[snapshot.nm_id] = snapshot
        latest = {item.nm_id: item for item in sources.latest}
        rows = []
        for card in selected_cards(query, sources):
            now, old, fresh = current.get(card.nm_id), previous.get(card.nm_id), latest.get(card.nm_id)
            change = (
                round(now.rating - old.rating, AGENT_WB_RATING.precision)
                if now is not None and old is not None
                else None
            )
            status = (
                WbRatingStatus.NO_DATA
                if change is None
                else WbRatingStatus.PROBLEM
                if change < -query.tolerance
                else WbRatingStatus.HEALTHY
            )
            if query.status and status != query.status:
                continue
            if query.rating_below is not None and (now is None or now.rating >= query.rating_below):
                continue
            if query.drop_at_least is not None and (change is None or change > -query.drop_at_least):
                continue
            rows.append(
                RatingComparisonRow(
                    **card.model_dump(),
                    latest_rating=fresh.rating
                    if fresh
                    and fresh.rating is not None
                    and 0 < fresh.rating <= AGENT_WB_RATING.maximum_rating
                    else None,
                    reviews_count=fresh.reviews_count if fresh else None,
                    latest_observed_at=fresh.observed_at if fresh else None,
                    current_rating=now.rating if now else None,
                    current_observed_day=now.day if now else None,
                    current_observed_at=now.observed_at if now else None,
                    previous_rating=old.rating if old else None,
                    previous_observed_day=old.day if old else None,
                    previous_observed_at=old.observed_at if old else None,
                    change=change,
                    status=status,
                )
            )
        return RatingComparison(
            rows=tuple(rows),
            totals=RatingTotals(
                checked=len(rows),
                problem=sum(item.status == WbRatingStatus.PROBLEM for item in rows),
                healthy=sum(item.status == WbRatingStatus.HEALTHY for item in rows),
                no_data=sum(item.status == WbRatingStatus.NO_DATA for item in rows),
            ),
            previous_from=previous_from,
            previous_to=previous_to,
        )

    async def history(self, query: RatingRequest) -> RatingHistory:
        sources = await self.reader.read(query)
        cards = selected_cards(query, sources)
        if len(cards) != 1:
            raise LookupError("rating_card_not_found")
        card = cards[0]
        observations = {item.day: item for item in sources.history if item.nm_id == card.nm_id}
        rows = []
        for offset in range((query.date_to - query.date_from).days + 1):
            day = query.date_from + timedelta(days=offset)
            snapshot = observations.get(day)
            rows.append(
                RatingHistoryRow(
                    **card.model_dump(),
                    day=day,
                    rating=snapshot.rating if snapshot else None,
                    reviews_count=snapshot.reviews_count if snapshot else None,
                    observed_at=snapshot.observed_at if snapshot else None,
                )
            )
        observed_days = sum(item.rating is not None for item in rows)
        return RatingHistory(
            rows=tuple(rows), observed_days=observed_days, missing_days=len(rows) - observed_days
        )
