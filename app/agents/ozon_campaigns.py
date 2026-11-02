from datetime import datetime

from pydantic import JsonValue

from app.agents.reports import filtered
from app.core.constants import OZON_ADVERTISING, OZON_API_LIMITS
from app.dto.agent_ozon import AgentOzonRows
from app.dto.agent_query import ReportQuery
from app.dto.ozon_advertising import (
    AdvertisingFact,
    AdvertisingPeriod,
    AdvertisingSourceStatus,
    PerformanceCampaign,
)
from app.ozon.advertising_normalizer import kind_source, source_days


def campaign_rows(query: ReportQuery, states: tuple[AdvertisingSourceStatus, ...]) -> AgentOzonRows:
    if query.date_from is None or query.date_to is None:
        raise ValueError("period_required")
    period = AdvertisingPeriod(start=query.date_from, end=query.date_to)
    selected = source_days(period, states)
    grouped: dict[tuple[str, str, str], list[AdvertisingFact]] = {}
    inventory: dict[str, PerformanceCampaign] = {}
    updated: dict[str, datetime] = {}
    for state in sorted(states, key=lambda item: item.attempted_at):
        if state.snapshot:
            for saved_campaign in state.snapshot.campaigns:
                inventory[saved_campaign.id] = saved_campaign
                updated[saved_campaign.id] = state.snapshot.synced_at
    for source in selected:
        if source.snapshot is None:
            continue
        for fact in source.snapshot.facts:
            if fact.day != source.day or kind_source(fact.kind) != source.source:
                continue
            if fact.article is None or fact.campaign_id is None:
                continue
            grouped.setdefault((fact.campaign_id, fact.article, fact.kind.value), []).append(fact)
    rows: list[dict[str, JsonValue]] = []
    for (campaign_id, article, kind), facts in sorted(grouped.items()):
        campaign = inventory.get(campaign_id)
        active = (
            campaign.state == OZON_API_LIMITS.running_campaign_state if campaign and campaign.state else None
        )
        impressions = (
            sum(item.impressions or 0 for item in facts)
            if all(item.impressions is not None for item in facts)
            else None
        )
        clicks = (
            sum(item.clicks or 0 for item in facts)
            if all(item.clicks is not None for item in facts)
            else None
        )
        ctr = (
            clicks / impressions * OZON_ADVERTISING.percent_factor
            if clicks is not None and impressions
            else None
        )
        if query.active_only and active is not True:
            continue
        if query.ctr_below is not None and (ctr is None or ctr >= query.ctr_below):
            continue
        rows.append(
            {
                "article": article,
                "campaign_id": campaign_id,
                "kind": kind,
                "is_active": active,
                "status": campaign.state if campaign else None,
                "spend": float(sum(item.amount for item in facts)),
                "impressions": impressions,
                "clicks": clicks,
                "ctr_percent": ctr,
                "updated_at": updated[campaign_id].isoformat() if campaign_id in updated else None,
            }
        )
    return AgentOzonRows(
        rows=filtered(rows, query),
        sources=[
            {
                "source": state.source.value,
                "state": state.state.value,
                "attempted_at": state.attempted_at.isoformat(),
            }
            for state in states
        ],
        context={
            "available": bool(states)
            and all(item.snapshot is not None and not item.stale for item in selected)
            and all(
                inventory.get(key[0]) is not None and inventory[key[0]].state is not None for key in grouped
            ),
            "active_status_basis": "saved_campaign_inventory",
            "empty_result_proves_no_campaigns": False,
            "missing_source_days": sum(item.snapshot is None for item in selected),
            "stale_source_days": sum(item.stale for item in selected),
        },
    )
