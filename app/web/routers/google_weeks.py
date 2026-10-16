from datetime import datetime
from time import monotonic

from fastapi import APIRouter, HTTPException, Request

from app.access.authentication import is_active_user
from app.application.google_weeks import GoogleWeeks, job_name
from app.core.constants import TIME, Marketplace, WeekAction, WeekIssue
from app.dto.google_week import WeekError, WeekSettings, WeekView
from app.jobs import async_locks
from app.jobs.locks import SyncJobBusyError

router = APIRouter(prefix="/api/admin/google-weeks")


def service(request: Request) -> GoogleWeeks:
    if not is_active_user(request.state.user):
        raise HTTPException(status_code=403, detail="forbidden")
    return GoogleWeeks(request.app.state.async_resources)


@router.get("", response_model=list[WeekView])
async def index(request: Request) -> list[WeekView]:
    application = service(request)
    return [await application.view(marketplace) for marketplace in Marketplace]


@router.get("/{marketplace}", response_model=WeekView)
async def status(request: Request, marketplace: Marketplace) -> WeekView:
    return await service(request).view(marketplace)


@router.put("/{marketplace}", response_model=WeekView)
async def save(request: Request, marketplace: Marketplace, settings: WeekSettings) -> WeekView:
    application = service(request)
    try:
        async with async_locks.hold(job_name(marketplace), application.resources.database):
            await application.save(marketplace, settings)
    except SyncJobBusyError as error:
        raise HTTPException(status_code=409, detail={"code": WeekIssue.BUSY}) from error
    except WeekError as error:
        raise HTTPException(status_code=400, detail=error.problem.model_dump()) from error
    return await application.view(marketplace)


@router.post("/{marketplace}/catalog", response_model=WeekView)
async def catalog(request: Request, marketplace: Marketplace) -> WeekView:
    application = service(request)
    try:
        async with async_locks.hold(job_name(marketplace), application.resources.database):
            await application.refresh_catalog(
                await application.repository.get(marketplace), datetime.now(TIME.moscow_timezone)
            )
    except SyncJobBusyError as error:
        raise HTTPException(status_code=409, detail={"code": WeekIssue.BUSY}) from error
    except WeekError as error:
        raise HTTPException(status_code=400, detail=error.problem.model_dump()) from error
    return await application.view(marketplace)


@router.post("/{marketplace}/{action}", response_model=WeekView)
async def run(
    request: Request, marketplace: Marketplace, action: WeekAction, preview: bool = False
) -> WeekView:
    application = service(request)
    if preview and action != WeekAction.FREEZE:
        raise HTTPException(status_code=400, detail={"code": WeekIssue.INVALID_SETTINGS})
    try:
        async with async_locks.hold(job_name(marketplace), application.resources.database):
            resources = application.resources
            name = job_name(marketplace)
            run_id = await resources.jobs.start_manual(name, datetime.now(TIME.moscow_timezone))
            started = monotonic()
            failure: str | None = WeekIssue.INTERRUPTED.value
            try:
                result = await application.run(
                    await application.repository.get(marketplace),
                    action,
                    datetime.now(TIME.moscow_timezone),
                    preview=preview,
                )
                failure = None if result.complete else WeekIssue.MISSING_DATA.value
            except WeekError as error:
                failure = error.problem.code.value
                raise
            finally:
                await resources.jobs.finish(
                    name,
                    run_id,
                    finished_at=datetime.now(TIME.moscow_timezone),
                    duration_ms=round((monotonic() - started) * TIME.milliseconds_per_second),
                    error=failure,
                )
    except SyncJobBusyError as error:
        raise HTTPException(status_code=409, detail={"code": WeekIssue.BUSY}) from error
    except WeekError as error:
        raise HTTPException(status_code=400, detail=error.problem.model_dump()) from error
    return await application.view(marketplace, preview=result if preview else None)
