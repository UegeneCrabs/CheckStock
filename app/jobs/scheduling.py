import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic

from fastapi.concurrency import run_in_threadpool

from app.core.constants import (
    SCHEDULER,
    TIME,
    JobStatus,
)
from app.dto.jobs import AsyncJobResult
from app.infrastructure.async_resources import AsyncResources
from app.jobs import locks
from app.jobs.tracking import exception_error, result_error
from app.repositories import job_schedules

ERROR_RETRY_SECONDS = SCHEDULER.error_retry_seconds
POLL_SECONDS = SCHEDULER.poll_seconds

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class JobTiming:
    startup_delay_seconds: float = 0
    ready_event: asyncio.Event | None = None
    on_attempt: Callable[[], None] | None = None
    interval_from_start: bool = False
    retry_delays: tuple[float, ...] = ()
    next_delay_after_success: Callable[[], float] | None = None
    align_scheduled_run: Callable[[datetime, datetime], datetime] | None = None


@dataclass(frozen=True, slots=True)
class BackgroundJob(JobTiming):
    name: str
    callback: Callable[[], object]
    next_delay: Callable[[], float]
    is_enabled: Callable[[], bool] | None = None
    run_callback: Callable[[], object] | None = None


@dataclass(frozen=True, slots=True)
class AsyncBackgroundJob(JobTiming):
    name: str
    callback: Callable[[AsyncResources], Awaitable[AsyncJobResult]]
    next_delay: Callable[[], float]
    is_enabled: Callable[[AsyncResources], Awaitable[bool]] | None = None


ScheduledJob = BackgroundJob | AsyncBackgroundJob


def _now() -> datetime:
    return datetime.now(UTC)


def _regular_next(job: ScheduledJob, now: datetime, elapsed: float = 0, *, success: bool = False) -> datetime:
    callback = job.next_delay_after_success if success and job.next_delay_after_success else job.next_delay
    delay = callback()
    if job.interval_from_start:
        delay -= elapsed
    return now + timedelta(seconds=max(SCHEDULER.min_delay_seconds, delay))


def _next_attempt(
    job: ScheduledJob, now: datetime, attempt: int, error: str | None, elapsed: float
) -> tuple[datetime, int]:
    if error and attempt <= len(job.retry_delays):
        return now + timedelta(seconds=job.retry_delays[attempt - 1]), attempt
    return _regular_next(job, now, elapsed, success=not error), 0


def run_scheduled_step(job: BackgroundJob) -> datetime:
    with locks.hold(job.name):
        now = _now()
        schedule = job_schedules.ensure(job.name, now + timedelta(seconds=job.startup_delay_seconds), now)
        planned = datetime.fromisoformat(schedule["next_run_at"])
        attempt = int(schedule["attempt_number"])
        if job.align_scheduled_run and not schedule["active_run_id"] and not attempt:
            aligned = job.align_scheduled_run(planned, now)
            if aligned != planned:
                job_schedules.defer(job.name, aligned, now)
                return aligned
        if not schedule["active_run_id"] and not attempt and planned > now:
            return planned

        enabled = job.is_enabled is None or job.is_enabled()
        if schedule["active_run_id"]:
            error: str | None = "Предыдущая автоматическая попытка прервана до сохранения результата"
            if not enabled:
                next_run, retry = _regular_next(job, now), 0
            elif job.retry_delays:
                next_run, retry = _next_attempt(job, now, attempt, error, 0)
            else:
                next_run, retry = now, 0
            job_schedules.finish(
                job.name,
                schedule["active_run_id"],
                finished_at=now,
                duration_ms=0,
                error=error,
                next_run_at=next_run,
                attempt_number=retry,
            )
            logger.warning("scheduled_run_interrupted job=%s attempt=%s", job.name, attempt)
            return next_run

        if not enabled:
            next_run = _regular_next(job, now)
            job_schedules.defer(job.name, next_run, now)
            return next_run
        if planned > now:
            return planned

        run_id = job_schedules.begin(schedule, now)
        if run_id is None:
            return now + timedelta(seconds=POLL_SECONDS)
        attempt += 1
        started = monotonic()
        try:
            result = (job.run_callback or job.callback)()
            error = result_error(result)
        except Exception as exc:
            error = exception_error(exc)
            logger.exception("background_job_failed job=%s attempt=%s", job.name, attempt)
        elapsed = monotonic() - started
        finished = _now()
        next_run, retry = _next_attempt(job, finished, attempt, error, elapsed)
        job_schedules.finish(
            job.name,
            run_id,
            finished_at=finished,
            duration_ms=max(0, round(elapsed * TIME.milliseconds_per_second)),
            error=error,
            next_run_at=next_run,
            attempt_number=retry,
        )
        logger.log(
            logging.WARNING if error else logging.INFO,
            "background_job_finished job=%s attempt=%s status=%s next_run=%s error=%s",
            job.name,
            attempt,
            JobStatus.ERROR.value if error else JobStatus.SUCCESS.value,
            next_run.isoformat(),
            error,
        )
        return next_run


async def wait_for_stop(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0, seconds))
        return True
    except TimeoutError:
        return stop.is_set()


async def _wait_ready(ready: asyncio.Event, stop: asyncio.Event) -> None:
    tasks = [asyncio.create_task(event.wait()) for event in (ready, stop)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_background_job(
    job: ScheduledJob,
    stop: asyncio.Event | None = None,
    resources: AsyncResources | None = None,
) -> None:
    if isinstance(job, AsyncBackgroundJob) and resources is None:
        raise ValueError("Async jobs require process-scoped resources")
    stop = stop if stop is not None else asyncio.Event()
    if job.ready_event is not None:
        await _wait_ready(job.ready_event, stop)
    while not stop.is_set():
        try:
            if isinstance(job, AsyncBackgroundJob):
                assert resources is not None
                from app.jobs.async_scheduling import run_scheduled_step as run_async_step

                planned = await run_async_step(job, resources)
            else:
                planned = await run_in_threadpool(run_scheduled_step, job)
            if job.on_attempt is not None:
                job.on_attempt()
            delay = min(POLL_SECONDS, max(SCHEDULER.min_delay_seconds, (planned - _now()).total_seconds()))
        except locks.SyncJobBusyError:
            logger.info("background_job_already_running job=%s", job.name)
            delay = POLL_SECONDS
        except Exception:
            logger.exception("background_job_scheduler_error job=%s", job.name)
            delay = ERROR_RETRY_SECONDS
        await wait_for_stop(stop, delay)
