import argparse
import asyncio
from datetime import UTC, datetime
from time import monotonic

from app.config import settings
from app.core.constants import JOBS, TIME, BackupIssue
from app.core.logging_config import configure_logging
from app.infrastructure.async_resources import AsyncResources
from app.integrations.database_backup_drive import DatabaseBackupDrive
from app.jobs import async_locks, database_backup


async def execute(check: bool) -> int:
    async with AsyncResources() as resources:
        if check:
            await DatabaseBackupDrive(resources.client, settings).check_folder()
            print("backup_drive_access_ok")
            return 0
        async with async_locks.hold(JOBS.database_backup.name, resources.database):
            run_id = await resources.jobs.start_manual(JOBS.database_backup.name, datetime.now(UTC))
            started = monotonic()
            error: str | None = BackupIssue.SESSION.value
            try:
                result = await database_backup.run(resources)
                error = result.error
            finally:
                await resources.jobs.finish(
                    JOBS.database_backup.name,
                    run_id,
                    finished_at=datetime.now(UTC),
                    duration_ms=round((monotonic() - started) * TIME.milliseconds_per_second),
                    error=error,
                )
        return 1 if result.error else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    configure_logging()
    return asyncio.run(execute(args.check))


if __name__ == "__main__":
    raise SystemExit(main())
