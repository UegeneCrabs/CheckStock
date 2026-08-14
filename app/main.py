from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.background import lifespan
from app.config import settings
from app.container import ApplicationContainer
from app.logging_config import configure_logging
from app.web.middleware import authentication_middleware, request_logging_middleware
from app.web.routers import activity, admin, auth, profile, sales, stock, stock_overview, system


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    configure_logging()
    application = FastAPI(lifespan=lifespan)
    application.state.container = container or ApplicationContainer()
    application.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    application.middleware("http")(authentication_middleware)
    application.middleware("http")(request_logging_middleware)
    application.include_router(system.router)
    application.include_router(auth.router)
    application.include_router(profile.router)
    application.include_router(activity.router)
    application.include_router(sales.router)
    application.include_router(stock_overview.router)
    application.include_router(stock.router)
    application.include_router(admin.router)
    return application


app = create_app()
