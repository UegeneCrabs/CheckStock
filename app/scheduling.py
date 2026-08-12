import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BackgroundJob:
    name: str
    callback: Callable[[], object]
    next_delay: Callable[[], float]
    startup_delay_seconds: float = 0
    ready_event: asyncio.Event | None = None
    on_attempt: Callable[[], None] | None = None


async def run_background_job(job: BackgroundJob) -> None:
    if job.ready_event is not None:
        await job.ready_event.wait()
    if job.startup_delay_seconds:
        await asyncio.sleep(job.startup_delay_seconds)

    while True:
        started_at = asyncio.get_running_loop().time()
        try:
            await run_in_threadpool(job.callback)
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
        finally:
            if job.on_attempt is not None:
                job.on_attempt()

        await asyncio.sleep(max(0, job.next_delay()))
