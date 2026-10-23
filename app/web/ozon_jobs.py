from datetime import UTC, datetime
from functools import partial

from fastapi import HTTPException, Request

from app.access.economics_preview import require_preview
from app.application.integration_views import IntegrationViews
from app.core.constants import WEB, WebErrorCode
from app.dto.integrations import JobConfigurationResult, JobHistory, JobLaunch, SyncSettingUpdate
from app.infrastructure.async_resources import AsyncResources
from app.infrastructure.integration_repository import IntegrationRepository
from app.jobs import async_locks, ozon
from app.jobs.async_tracking import queue_tracked
from app.jobs.locks import SyncJobBusyError
from app.jobs.settings import definition_for


def owns(name: str) -> bool:
    return any(job.name == name for job in ozon.jobs())


def views(resources: AsyncResources) -> IntegrationViews:
    return IntegrationViews(
        IntegrationRepository(resources.database),
        resources.jobs,
        partial(async_locks.is_running, database=resources.database),
    )


async def launch(request: Request, name: str, resources: AsyncResources) -> JobLaunch:
    require_preview(request.state.user)
    job = next(job for job in ozon.jobs() if job.name == name)
    config = await views(resources).configuration(name)
    if config.targets and not config.enabled_target_count:
        raise HTTPException(409, WebErrorCode.NO_TARGETS)
    try:
        run_id = await queue_tracked(job, resources)
    except SyncJobBusyError as error:
        raise HTTPException(409, WebErrorCode.JOB_BUSY) from error
    definition = definition_for(name)
    if definition:
        await resources.jobs.log_action(
            request.state.user.id, request.state.user.full_name, definition.title, datetime.now(UTC)
        )
    return JobLaunch(run_id=run_id)


async def history(name: str, limit: int, resources: AsyncResources) -> JobHistory:
    definition = definition_for(name)
    if definition is None:
        raise HTTPException(404, WebErrorCode.NOT_FOUND)
    return await views(resources).history(definition, min(max(limit, 1), WEB.history_max_limit))


async def save(
    request: Request, name: str, payload: SyncSettingUpdate, resources: AsyncResources
) -> JobConfigurationResult:
    definition = definition_for(name)
    if definition is None:
        raise HTTPException(404, WebErrorCode.NOT_FOUND)
    try:
        result = await views(resources).save_setting(definition, payload)
    except ValueError as error:
        raise HTTPException(422, WebErrorCode.INVALID_TARGET) from error
    await IntegrationRepository(resources.database).audit(
        request.state.user.id, request.state.user.full_name, "sync_job_setting", name
    )
    return JobConfigurationResult(configuration=result)
