from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.background import lifespan
from app.config import settings
from app.container import ApplicationContainer
from app.logging_config import configure_logging
from app.web.middleware import authentication_middleware, request_logging_middleware
from app.web.routers import ROUTERS


def create_app(container: ApplicationContainer | None = None) -> FastAPI:
    configure_logging()
    application = FastAPI(lifespan=lifespan)
    application.state.container = container or ApplicationContainer()
    application.mount("/static", StaticFiles(directory=str(settings.static_dir)), name="static")
    application.middleware("http")(authentication_middleware)
    application.middleware("http")(request_logging_middleware)
    for router in ROUTERS:
        application.include_router(router)
    return application


app = create_app()
