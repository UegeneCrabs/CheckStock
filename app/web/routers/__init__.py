from app.web.routers import (
    admin,
    agent_analytics,
    agent_full,
    agent_management,
    agent_mcp,
    analyzer,
    auth,
    economics_calendar,
    google_export,
    integrations,
    profile,
    sales_api,
    stock,
    system,
    target_prices,
    unit_economics,
    yandex_economics,
    yandex_prices,
    yandex_reports,
)

agent_analytics.router.include_router(agent_full.router)

ROUTERS = (
    agent_analytics.router,
    agent_management.router,
    agent_mcp.router,
    system.router,
    auth.router,
    profile.router,
    stock.router,
    analyzer.router,
    sales_api.router,
    unit_economics.router,
    economics_calendar.router,
    target_prices.router,
    admin.router,
    google_export.router,
    integrations.router,
    yandex_economics.router,
    yandex_prices.router,
    yandex_reports.router,
)

__all__ = ("ROUTERS",)
