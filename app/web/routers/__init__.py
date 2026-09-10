from app.web.routers import (
    activity,
    admin,
    agent_analytics,
    agent_full,
    agent_management,
    auth,
    google_export,
    integrations,
    profile,
    sales,
    stock,
    stock_overview,
    system,
)

agent_analytics.router.include_router(agent_full.router)

ROUTERS = (
    agent_analytics.router,
    agent_management.router,
    system.router,
    auth.router,
    profile.router,
    activity.router,
    sales.router,
    stock_overview.router,
    stock.router,
    admin.router,
    google_export.router,
    integrations.router,
)

__all__ = ("ROUTERS",)
