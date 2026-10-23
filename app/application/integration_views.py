from collections.abc import Awaitable, Callable
from typing import Protocol

from app.config import settings
from app.core.constants import JOB_SCHEDULE_RULES, JOBS, WEB, JobScope, JobStatus, ScheduleKind, WebErrorCode
from app.dto.integrations import (
    JobCatalog,
    JobConfiguration,
    JobHistory,
    JobIdentity,
    JobRun,
    JobScheduleView,
    JobState,
    JobStates,
    JobView,
    SyncSettingUpdate,
)
from app.dto.jobs import SyncJobSetting
from app.jobs.catalog import SyncJobDefinition
from app.jobs.settings import configuration_from_rows
from app.yandex.product_novelty import SYNC_INTERVAL_SECONDS as NOVELTY_INTERVAL
from app.yandex.unit_economics_sync import SYNC_INTERVAL_SECONDS as YANDEX_INTERVALS


class JobSettingsReader(Protocol):
    async def settings(self, name: str) -> tuple[SyncJobSetting, ...]: ...


class IntegrationReader(Protocol):
    async def state(self, name: str) -> JobState: ...
    async def runs(self, name: str, limit: int) -> tuple[JobRun, ...]: ...
    async def save_setting(self, name: str, value: SyncSettingUpdate) -> None: ...


def schedule_for(name: str) -> JobScheduleView:
    rule = next(item for item in JOB_SCHEDULE_RULES if item.name == name)
    seconds = getattr(settings, rule.interval_setting) if rule.interval_setting else rule.seconds
    if rule.yandex_source is not None:
        seconds = YANDEX_INTERVALS[rule.yandex_source]
    if name == JOBS.wb_standard_costs.name:
        seconds = settings.wb_standard_costs.interval_seconds
    if name == JOBS.yandex_product_novelty.name:
        seconds = NOVELTY_INTERVAL
    hours = (getattr(settings, rule.hour_setting),) if rule.hour_setting else rule.hours
    if rule.kind == ScheduleKind.WINDOW:
        return JobScheduleView(
            kind=rule.kind,
            timezone=WEB.timezone,
            hours=(settings.ftp_export_start_hour,),
            minute=settings.ftp_export_start_minute,
            deadline_hour=settings.ftp_export_deadline_hour,
            retry_seconds=settings.ftp_export_retry_interval_seconds,
        )
    return JobScheduleView(
        kind=rule.kind,
        timezone=WEB.timezone,
        interval_seconds=seconds,
        hours=hours,
        minute=rule.minute,
        weekdays=rule.weekdays,
    )


class IntegrationViews:
    def __init__(
        self,
        repository: IntegrationReader,
        job_settings: JobSettingsReader,
        running: Callable[[str], Awaitable[bool]],
    ):
        self.repository = repository
        self.job_settings = job_settings
        self.running = running

    async def configuration(self, name: str) -> JobConfiguration:
        return configuration_from_rows(name, await self.job_settings.settings(name))

    async def catalog(self, definitions: tuple[SyncJobDefinition, ...]) -> JobCatalog:
        jobs = []
        for definition in definitions:
            jobs.append(
                JobView(
                    name=definition.name,
                    title=definition.title,
                    description=definition.description,
                    scope=JobScope(definition.scope),
                    marketplaces=definition.marketplaces,
                    manual_run=definition.manual_run,
                    schedule=schedule_for(definition.name),
                    configuration=await self.configuration(definition.name),
                )
            )
        return JobCatalog(jobs=tuple(jobs))

    async def state(self, name: str) -> JobState:
        active = await self.running(name)
        state = await self.repository.state(name)
        config = await self.configuration(name)
        interrupted = state.status == JobStatus.RUNNING and not active
        return state.model_copy(
            update={
                "running": active,
                "status": JobStatus.RUNNING
                if active
                else WEB.interrupted_status
                if interrupted
                else state.status,
                "error_code": WebErrorCode.INTERRUPTED if interrupted else None,
                "effective_enabled": config.effective_enabled
                and (not config.targets or bool(config.enabled_target_count)),
            }
        )

    async def states(self, definitions: tuple[SyncJobDefinition, ...]) -> JobStates:
        return JobStates(states=tuple([await self.state(definition.name) for definition in definitions]))

    async def history(self, definition: SyncJobDefinition, limit: int) -> JobHistory:
        state = await self.state(definition.name)
        runs = list(await self.repository.runs(definition.name, limit))
        if state.last_started_at and not any(run.started_at == state.last_started_at for run in runs):
            runs.insert(
                0,
                JobRun(
                    id=f"state:{definition.name}",
                    name=definition.name,
                    trigger=state.last_trigger,
                    status=state.status,
                    started_at=state.last_started_at,
                    finished_at=state.last_finished_at,
                    duration_ms=state.duration_ms,
                    error=state.error,
                    error_code=state.error_code,
                ),
            )
        result = []
        for run in runs[:limit]:
            if run.status == JobStatus.RUNNING:
                latest = run.started_at == state.last_started_at
                if latest and state.status in (JobStatus.SUCCESS, JobStatus.ERROR):
                    run = run.model_copy(
                        update={
                            "status": state.status,
                            "finished_at": state.last_finished_at,
                            "duration_ms": state.duration_ms,
                            "error": state.error,
                        }
                    )
                elif not (latest and state.running):
                    run = run.model_copy(
                        update={"status": WEB.interrupted_status, "error_code": WebErrorCode.INTERRUPTED}
                    )
            result.append(run)
        return JobHistory(job=JobIdentity(name=definition.name, title=definition.title), runs=tuple(result))

    async def save_setting(self, definition: SyncJobDefinition, value: SyncSettingUpdate) -> JobConfiguration:
        config = await self.configuration(definition.name)
        if value.store_slug or value.marketplace:
            valid = any(
                target.store_slug == value.store_slug and target.marketplace == value.marketplace
                for target in config.targets
            )
            valid = valid or (not value.store_slug and value.marketplace in definition.marketplaces)
            if not valid:
                raise ValueError(WebErrorCode.INVALID_TARGET)
        await self.repository.save_setting(definition.name, value)
        return await self.configuration(definition.name)
