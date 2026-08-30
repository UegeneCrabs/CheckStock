from app.web.routers import (
    activity,
    admin,
    auth,
    google_export,
    integrations,
    profile,
    sales,
    stock,
    stock_overview,
    system,
)

ROUTERS = (
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
