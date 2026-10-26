import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic

from app.core.constants import (
    TIME,
)
from app.infrastructure.async_resources import AsyncResources
from app.jobs import async_locks, settings
from app.jobs.scheduling import AsyncBackgroundJob
from app.jobs.tracking import exception_error

logger = logging.getLogger(__name__)


async def queue_tracked(job: AsyncBackgroundJob, resources: AsyncResources) -> str:
    handle = await async_locks.acquire(job.name, resources.database)
    try:
        run_id = await resources.jobs.start_manual(job.name, datetime.now(UTC))
    except BaseException:
        await handle.close()
        raise
    started = monotonic()
    error: str | None = "Ручная загрузка прервана"

    async def work() -> None:
        nonlocal error
        try:
            with settings.manual_run(job.name):
                result = await job.callback(resources)
                error = result.error
        except asyncio.CancelledError:
            error = "Ручная загрузка прервана"
            raise
        except Exception as exc:
            error = exception_error(exc)
            logger.exception("manual_sync_failed job=%s", job.name)

    async def finish(task: asyncio.Task[None]) -> None:
        try:
            await resources.jobs.finish(
                job.name,
                run_id,
                finished_at=datetime.now(UTC),
                duration_ms=max(0, round((monotonic() - started) * TIME.milliseconds_per_second)),
                error=error,
            )
        finally:
            await handle.close()

    try:
        resources.spawn(work(), name=f"manual-sync:{job.name}", finalize=finish)
    except BaseException as exc:
        try:
            await resources.jobs.finish(
                job.name,
                run_id,
                finished_at=datetime.now(UTC),
                duration_ms=0,
                error=f"Не удалось запустить загрузку: {type(exc).__name__}",
            )
        finally:
            await handle.close()
        raise
    return run_id
