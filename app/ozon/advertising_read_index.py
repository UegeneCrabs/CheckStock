from collections.abc import Mapping
from dataclasses import dataclass

from app.dto.ozon_advertising import AdvertisingFact, AdvertisingSourceDay, PerformanceCampaign


@dataclass(frozen=True, slots=True, kw_only=True)
class AdvertisingDayIndex:
    source: AdvertisingSourceDay
    facts: Mapping[str | None, tuple[AdvertisingFact, ...]]


class AdvertisingReadIndex:
    def __init__(self, sources: tuple[AdvertisingSourceDay, ...]):
        days: list[AdvertisingDayIndex] = []
        for source in sources:
            facts: dict[str | None, list[AdvertisingFact]] = {}
            snapshot = source.snapshot
            if snapshot is not None:
                for fact in snapshot.facts:
                    facts.setdefault(fact.article, []).append(fact)
                campaigns: dict[tuple[str, str], PerformanceCampaign] = {}
                for campaign in snapshot.campaigns:
                    campaigns.setdefault((campaign.adv_object_type, campaign.payment_type), campaign)
                source = source.model_copy(
                    update={"snapshot": snapshot.model_copy(update={"campaigns": tuple(campaigns.values())})}
                )
            days.append(
                AdvertisingDayIndex(source=source, facts={key: tuple(rows) for key, rows in facts.items()})
            )
        self.days = tuple(days)

    def article(self, article: str) -> tuple[AdvertisingSourceDay, ...]:
        return tuple(
            day.source.model_copy(
                update={
                    "snapshot": snapshot.model_copy(
                        update={"facts": day.facts.get(article, ()) + day.facts.get(None, ())}
                    )
                }
            )
            if (snapshot := day.source.snapshot) is not None
            else day.source
            for day in self.days
        )
