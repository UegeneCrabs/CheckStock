from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.dto.system import HealthStatus, ReadinessStatus
from app.section_access import landing_path
from app.web.dependencies import ContainerDependency
from app.web.templating import render_access_denied_page

router = APIRouter()


@router.get("/")
async def root(request: Request):
    return RedirectResponse(landing_path(request.state.user), status_code=303)


@router.get("/healthz", response_model=HealthStatus)
async def healthcheck() -> HealthStatus:
    return HealthStatus()


@router.get("/readyz", response_model=ReadinessStatus)
async def readinesscheck(container: ContainerDependency) -> ReadinessStatus | JSONResponse:
    status = await run_in_threadpool(container.health.readiness)
    if status.status == "unavailable":
        return JSONResponse(status.model_dump(mode="json"), status_code=503)
    return status


@router.get("/access-denied", response_class=HTMLResponse)
async def access_denied(request: Request):
    return render_access_denied_page(request.state.user)
