from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.dto.system import HealthStatus, ReadinessStatus
from app.web.dependencies import ContainerDependency

router = APIRouter()


@router.get("/healthz", response_model=HealthStatus)
async def healthcheck() -> HealthStatus:
    return HealthStatus()


@router.get("/readyz", response_model=ReadinessStatus)
async def readinesscheck(container: ContainerDependency) -> ReadinessStatus | JSONResponse:
    status = await run_in_threadpool(container.health.readiness)
    if status.status == "unavailable":
        return JSONResponse(status.model_dump(mode="json"), status_code=503)
    return status
