import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool

from app.sync_tracking import run_tracked, set_next_run

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackgroundJob:
    name: str
    callback: Callable[[], object]
    next_delay: Callable[[], float]
    startup_delay_seconds: float = 0
    ready_event: asyncio.Event | None = None
    on_attempt: Callable[[], None] | None = None
    interval_from_start: bool = False
    is_enabled: Callable[[], bool] | None = None
    run_callback: Callable[[], object] | None = None


async def run_background_job(job: BackgroundJob) -> None:
    if job.ready_event is not None:
        await job.ready_event.wait()
    if job.startup_delay_seconds:
        await run_in_threadpool(set_next_run, job.name, job.startup_delay_seconds)
        await asyncio.sleep(job.startup_delay_seconds)

    while True:
        started_at = asyncio.get_running_loop().time()
        enabled = job.is_enabled is None or await run_in_threadpool(job.is_enabled)
        if enabled:
            try:
                await run_in_threadpool(
                    run_tracked,
                    job.name,
                    "scheduled",
                    job.run_callback or job.callback,
                )
            except Exception:
                elapsed_seconds = asyncio.get_running_loop().time() - started_at
                logger.exception(
                    "background_job_failed job=%s duration_seconds=%.3f",
                    job.name,
                    elapsed_seconds,
                )
            else:
                elapsed_seconds = asyncio.get_running_loop().time() - started_at
                logger.info(
                    "background_job_completed job=%s duration_seconds=%.3f",
                    job.name,
                    elapsed_seconds,
                )
        else:
            elapsed_seconds = asyncio.get_running_loop().time() - started_at
        if job.on_attempt is not None:
            job.on_attempt()

        configured_delay = job.next_delay()
        delay = max(
            0,
            configured_delay - elapsed_seconds if job.interval_from_start else configured_delay,
        )
        await run_in_threadpool(set_next_run, job.name, delay)
        await asyncio.sleep(delay)
