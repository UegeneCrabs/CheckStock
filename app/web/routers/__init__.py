from app.web.routers import (
    admin,
    agent_analytics,
    agent_full,
    agent_management,
    agent_mcp,
    auth,
    google_export,
    integrations,
    profile,
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
    unit_economics.router,
    target_prices.router,
    admin.router,
    google_export.router,
    integrations.router,
    yandex_economics.router,
    yandex_prices.router,
    yandex_reports.router,
)

__all__ = ("ROUTERS",)
