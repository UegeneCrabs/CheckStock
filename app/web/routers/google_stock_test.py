from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from app.access.authentication import is_active_user
from app.application.google_stock_test import next_run
from app.config import settings
from app.container import google_stock_test_service
from app.core.constants import GOOGLE_STOCK_TEST, JOBS, WEB, JobStatus, WebErrorCode
from app.dto.google_stock_report import StockTestExportView, StockTestSchedule
from app.dto.integrations import JobLaunch
from app.ff_import.google_service_account import get_service_account_email
from app.infrastructure.async_resources import AsyncResources
from app.infrastructure.integration_repository import IntegrationRepository
from app.jobs import async_locks, google_stock_test
from app.jobs.async_tracking import queue_tracked
from app.jobs.locks import SyncJobBusyError
from app.web.dependencies import get_async_resources

router = APIRouter(prefix="/api/admin/integrations/google-stock-test")


def resources_for(request: Request) -> AsyncResources:
    if not settings.google_stock_test_enabled:
        raise HTTPException(status_code=404, detail=WebErrorCode.NOT_FOUND)
    if not is_active_user(request.state.user):
        raise HTTPException(status_code=403, detail=WebErrorCode.FORBIDDEN)
    return get_async_resources(request)


async def view(resources: AsyncResources) -> StockTestExportView:
    name = JOBS.google_stock_test.name
    config = await google_stock_test_service(resources).schedules.get()
    state = await IntegrationRepository(resources.database).state(name)
    if state.status == JobStatus.RUNNING:
        running = await async_locks.is_running(name, resources.database)
        state = state.model_copy(
            update={"running": running, "status": JobStatus.RUNNING if running else WEB.interrupted_status}
        )
    return StockTestExportView(
        schedule=config.schedule,
        spreadsheet_url=GOOGLE_STOCK_TEST.spreadsheet_url,
        sheets=tuple(
            target.sheet_name for target in (*GOOGLE_STOCK_TEST.targets, *GOOGLE_STOCK_TEST.order_targets)
        ),
        service_account_email=get_service_account_email(),
        next_run_at=next_run(config, datetime.now(UTC)),
        state=state,
    )


@router.get("", response_model=StockTestExportView)
async def status(request: Request) -> StockTestExportView:
    return await view(resources_for(request))


@router.put("", response_model=StockTestExportView)
async def save(request: Request, schedule: StockTestSchedule) -> StockTestExportView:
    resources = resources_for(request)
    try:
        async with async_locks.hold(JOBS.google_stock_test.name, resources.database):
            await google_stock_test_service(resources).schedules.save(schedule, datetime.now(UTC))
    except SyncJobBusyError as error:
        raise HTTPException(status_code=409, detail=WebErrorCode.JOB_BUSY) from error
    return await view(resources)


@router.post("/run", status_code=202, response_model=JobLaunch)
async def run(request: Request) -> JobLaunch:
    resources = resources_for(request)
    try:
        run_id = await queue_tracked(google_stock_test.job(), resources)
    except SyncJobBusyError as error:
        raise HTTPException(status_code=409, detail=WebErrorCode.JOB_BUSY) from error
    user = request.state.user
    await resources.jobs.log_action(user.id, user.full_name, JOBS.google_stock_test.title, datetime.now(UTC))
    return JobLaunch(run_id=run_id)
