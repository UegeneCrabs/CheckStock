import logging
from datetime import datetime, timedelta

from app.container import database_backup_service
from app.core.constants import DATABASE_BACKUP, JOBS, TIME
from app.dto.database_backup import BackupError, BackupJobResult
from app.infrastructure.async_resources import AsyncResources
from app.jobs.scheduling import AsyncBackgroundJob
from app.jobs.settings import configuration_from_rows

logger = logging.getLogger(__name__)


def delay() -> float:
    now = datetime.now(TIME.moscow_timezone)
    target = now.replace(hour=DATABASE_BACKUP.hour_msk, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def enabled(resources: AsyncResources) -> bool:
    config = configuration_from_rows(
        JOBS.database_backup.name, await resources.jobs.settings(JOBS.database_backup.name)
    )
    return config.effective_enabled


async def run(resources: AsyncResources) -> BackupJobResult:
    try:
        result = await database_backup_service(resources).run(datetime.now(TIME.moscow_timezone).date())
        logger.info("database_backup_uploaded file_id=%s skipped=%s", result.file_id, result.skipped)
        return result
    except BackupError as error:
        logger.warning("database_backup_failed code=%s status=%s", error.code, error.status)
        return BackupJobResult(error=str(error))


def job() -> AsyncBackgroundJob:
    return AsyncBackgroundJob(
        JOBS.database_backup.name,
        run,
        delay,
        startup_delay_seconds=delay(),
        is_enabled=enabled,
        retry_delays=DATABASE_BACKUP.job_retry_seconds,
    )
