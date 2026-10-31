import logging
from datetime import datetime, timedelta
from time import monotonic

from app.core.constants import (
    JOBS,
    SCHEDULER,
    TIME,
)
from app.core.logging_config import log_tags, tagged_logger
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks, scheduling
from app.jobs.scheduling import AsyncBackgroundJob
from app.jobs.tracking import exception_error

logger = tagged_logger(__name__, "jobs", "async_scheduling")


async def run_scheduled_step(job: AsyncBackgroundJob, resources: AsyncResources) -> datetime:
    async with async_locks.hold(job.name, resources.database):
        now = scheduling._now()
        repository = resources.jobs
        schedule = await repository.ensure(job.name, now + timedelta(seconds=job.startup_delay_seconds), now)
        planned = datetime.fromisoformat(schedule.next_run_at)
        attempt = schedule.attempt_number
        if (
            job.name in (JOBS.ozon_orders.name, JOBS.ozon_advertising_breakdown.name)
            and not schedule.active_run_id
            and not attempt
            and planned > now + timedelta(seconds=job.next_delay())
        ):
            await repository.defer(job.name, now, now)
            schedule = await repository.ensure(job.name, now, now)
            planned = now
        if not schedule.active_run_id and not attempt and planned > now:
            return planned
        enabled = job.is_enabled is None or await job.is_enabled(resources)
        if schedule.active_run_id:
            error: str | None = "Предыдущая автоматическая попытка прервана до сохранения результата"
            if not enabled:
                next_run, retry = scheduling._regular_next(job, now), 0
            elif job.retry_delays:
                next_run, retry = scheduling._next_attempt(job, now, attempt, error, 0)
            else:
                next_run, retry = now, 0
            await repository.finish(
                job.name,
                schedule.active_run_id,
                finished_at=now,
                duration_ms=0,
                error=error,
                next_run_at=next_run,
                attempt_number=retry,
            )
            logger.warning(
                "scheduled_run_interrupted job=%s attempt=%s", job.name, attempt, extra=log_tags(job.name)
            )
            return next_run
        if not enabled:
            next_run = scheduling._regular_next(job, now)
            await repository.defer(job.name, next_run, now)
            return next_run
        if planned > now:
            return planned
        run_id = await repository.begin(schedule, now)
        if run_id is None:
            return now + timedelta(seconds=SCHEDULER.poll_seconds)
        started = monotonic()
        attempt += 1
        try:
            result = await job.callback(resources)
            error = result.error
        except Exception as exc:
            error = exception_error(exc)
            logger.exception(
                "background_job_failed job=%s attempt=%s", job.name, attempt, extra=log_tags(job.name)
            )
        elapsed = monotonic() - started
        finished = scheduling._now()
        next_run, retry = scheduling._next_attempt(job, finished, attempt, error, elapsed)
        await repository.finish(
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
            "background_job_finished job=%s attempt=%s next_run=%s error=%s",
            job.name,
            attempt,
            next_run.isoformat(),
            error,
            extra=log_tags(job.name),
        )
        return next_run
