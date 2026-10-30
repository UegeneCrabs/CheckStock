from app.core.constants import OZON_ADVERTISING, OZON_API_LIMITS
from app.dto.ozon_advertising import PerformanceCampaign, PerformanceReportRequest


def export_units(request: PerformanceReportRequest) -> int:
    if request.path == OZON_ADVERTISING.statistics_path:
        return len(request.campaigns)
    return int(request.path in OZON_ADVERTISING.aggregate_report_paths)


def account_export_limit(campaigns: tuple[PerformanceCampaign, ...]) -> int:
    active = sum(item.state == OZON_API_LIMITS.running_campaign_state for item in campaigns)
    return min(
        OZON_API_LIMITS.performance_exports_per_day, active * OZON_API_LIMITS.exports_per_active_campaign
    )
