from datetime import date

from app.core.constants import OZON_ADVERTISING, OZON_API_LIMITS
from app.dto.ozon_advertising import PerformanceCampaign, PerformanceReportRequest


def campaign_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.split("T", 1)[0])
    except ValueError:
        return None


def export_units(request: PerformanceReportRequest, campaigns: tuple[PerformanceCampaign, ...]) -> int:
    if request.campaigns:
        return len(request.campaigns)
    selected = tuple(
        item
        for item in campaigns
        if (start := campaign_date(item.from_date)) is None or start <= request.period.end
    )
    selected = tuple(
        item
        for item in selected
        if (end := campaign_date(item.to_date)) is None or end >= request.period.start
    )
    if request.path == OZON_ADVERTISING.selected_path:
        selected = tuple(
            item
            for item in selected
            if item.adv_object_type in (OZON_ADVERTISING.selected_type, OZON_ADVERTISING.sku_type)
        )
    elif request.path == OZON_ADVERTISING.all_products_path:
        selected = tuple(item for item in selected if item.adv_object_type == OZON_ADVERTISING.all_type)
    return max(len(selected), 1)


def account_export_limit(campaigns: tuple[PerformanceCampaign, ...]) -> int:
    active = sum(item.state == OZON_API_LIMITS.running_campaign_state for item in campaigns)
    return min(
        OZON_API_LIMITS.performance_exports_per_day, active * OZON_API_LIMITS.exports_per_active_campaign
    )
