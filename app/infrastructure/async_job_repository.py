from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import (
    JOBS,
    SCHEDULER,
    JobStatus,
    JobTrigger,
)
from app.dto.jobs import JobSchedule, SyncJobSetting
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.orm import (
    ActivityLogRecord,
    SyncJobRunRecord,
    SyncJobScheduleRecord,
    SyncJobSettingRecord,
    SyncJobStateRecord,
)

STATES = table_for(SyncJobStateRecord)
RUNS = table_for(SyncJobRunRecord)
SCHEDULES = table_for(SyncJobScheduleRecord)
SETTINGS = table_for(SyncJobSettingRecord)


class AsyncJobRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def settings(self, name: str) -> tuple[SyncJobSetting, ...]:
        names = (
            (JOBS.ozon_advertising.name, name) if name == JOBS.ozon_advertising_breakdown.name else (name,)
        )
        async with self.database.transaction() as connection:
            rows = await connection.execute(select(SETTINGS).where(SETTINGS.c.name.in_(names)))
            return tuple(
                SyncJobSetting(
                    store_slug=row["store_slug"], marketplace=row["marketplace"], enabled=bool(row["enabled"])
                )
                for row in sorted(rows.mappings(), key=lambda row: row["name"] == name)
            )

    async def log_action(self, user_id: int, user_name: str, title: str, now: datetime) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                table_for(ActivityLogRecord)
                .insert()
                .values(
                    user_id=user_id,
                    user_name=user_name,
                    action="Запущена выгрузка вручную",
                    details=title,
                    created_at=now.isoformat(),
                )
            )

    async def _next_run(self, connection: AsyncConnection, name: str, planned: str, now: str) -> None:
        await connection.execute(
            upsert(
                connection,
                STATES,
                {
                    "name": name,
                    "next_run_at": planned,
                    "updated_at": now,
                },
                ("name",),
            )
        )

    async def ensure(self, name: str, initial: datetime, now: datetime) -> JobSchedule:
        async with self.database.transaction(write=True) as connection:
            row = (
                (await connection.execute(select(SCHEDULES).where(SCHEDULES.c.name == name)))
                .mappings()
                .first()
            )
            if row:
                return JobSchedule(
                    name=row["name"],
                    next_run_at=row["next_run_at"],
                    attempt_number=row["attempt_number"],
                    active_run_id=row["active_run_id"],
                    updated_at=row["updated_at"],
                )
            previous = (
                await connection.execute(select(STATES.c.next_run_at).where(STATES.c.name == name))
            ).scalar()
            try:
                planned = datetime.fromisoformat(previous) if previous else initial
                if planned.tzinfo is None:
                    planned = planned.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                planned = initial
            schedule = JobSchedule(
                name=name,
                next_run_at=planned.astimezone(UTC).isoformat(),
                attempt_number=0,
                active_run_id=None,
                updated_at=now.isoformat(),
            )
            await connection.execute(SCHEDULES.insert().values(**asdict(schedule)))
            await self._next_run(connection, name, schedule.next_run_at, now.isoformat())
            return schedule

    async def _started(
        self, connection: AsyncConnection, name: str, trigger: JobTrigger, now: datetime, run_id: str
    ) -> None:
        stamp = now.isoformat()
        await connection.execute(
            upsert(
                connection,
                STATES,
                {
                    "name": name,
                    "last_trigger": trigger.value,
                    "status": JobStatus.RUNNING.value,
                    "last_started_at": stamp,
                    "last_finished_at": None,
                    "duration_ms": None,
                    "error": None,
                    "updated_at": stamp,
                },
                ("name",),
            )
        )
        await connection.execute(
            RUNS.insert().values(
                id=run_id,
                name=name,
                trigger=trigger.value,
                status=JobStatus.RUNNING.value,
                started_at=stamp,
            )
        )
        await connection.execute(
            delete(RUNS).where(
                RUNS.c.started_at < (now - timedelta(days=SCHEDULER.job_run_retention_days)).isoformat(),
                RUNS.c.id.not_in(
                    select(SCHEDULES.c.active_run_id).where(SCHEDULES.c.active_run_id.is_not(None))
                ),
            )
        )

    async def begin(self, schedule: JobSchedule, now: datetime) -> str | None:
        run_id = uuid4().hex
        async with self.database.transaction(write=True) as connection:
            result = await connection.execute(
                update(SCHEDULES)
                .where(
                    SCHEDULES.c.name == schedule.name,
                    SCHEDULES.c.active_run_id.is_(None),
                    SCHEDULES.c.next_run_at == schedule.next_run_at,
                    SCHEDULES.c.attempt_number == schedule.attempt_number,
                )
                .values(
                    active_run_id=run_id,
                    attempt_number=SCHEDULES.c.attempt_number + 1,
                    updated_at=now.isoformat(),
                )
            )
            if result.rowcount != 1:
                return None
            await self._started(connection, schedule.name, JobTrigger.SCHEDULED, now, run_id)
        return run_id

    async def start_manual(self, name: str, now: datetime) -> str:
        run_id = uuid4().hex
        async with self.database.transaction(write=True) as connection:
            await self._started(connection, name, JobTrigger.MANUAL, now, run_id)
        return run_id

    async def _finished(
        self,
        connection: AsyncConnection,
        name: str,
        run_id: str,
        now: datetime,
        duration_ms: int,
        error: str | None,
    ) -> None:
        stamp = now.isoformat()
        status = (JobStatus.SUCCESS if error is None else JobStatus.ERROR).value
        await connection.execute(
            update(STATES)
            .where(
                STATES.c.name == name,
                STATES.c.last_started_at
                == select(RUNS.c.started_at).where(RUNS.c.id == run_id).scalar_subquery(),
            )
            .values(
                status=status,
                last_finished_at=stamp,
                duration_ms=duration_ms,
                error=error,
                updated_at=stamp,
                last_success_at=stamp if error is None else STATES.c.last_success_at,
            )
        )
        await connection.execute(
            update(RUNS)
            .where(RUNS.c.id == run_id)
            .values(
                status=status,
                finished_at=stamp,
                duration_ms=duration_ms,
                error=error,
            )
        )

    async def finish(
        self,
        name: str,
        run_id: str,
        *,
        finished_at: datetime,
        duration_ms: int,
        error: str | None,
        next_run_at: datetime | None = None,
        attempt_number: int = 0,
    ) -> None:
        async with self.database.transaction(write=True) as connection:
            if next_run_at is not None:
                updated = await connection.execute(
                    update(SCHEDULES)
                    .where(
                        SCHEDULES.c.name == name,
                        SCHEDULES.c.active_run_id == run_id,
                    )
                    .values(
                        active_run_id=None,
                        next_run_at=next_run_at.isoformat(),
                        attempt_number=attempt_number,
                        updated_at=finished_at.isoformat(),
                    )
                )
                if updated.rowcount != 1:
                    raise RuntimeError(f"Scheduled run ownership changed: {name}/{run_id}")
                await self._next_run(connection, name, next_run_at.isoformat(), finished_at.isoformat())
            await self._finished(connection, name, run_id, finished_at, duration_ms, error)

    async def defer(self, name: str, planned: datetime, now: datetime) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                update(SCHEDULES)
                .where(
                    SCHEDULES.c.name == name,
                    SCHEDULES.c.active_run_id.is_(None),
                )
                .values(next_run_at=planned.isoformat(), attempt_number=0, updated_at=now.isoformat())
            )
            await self._next_run(connection, name, planned.isoformat(), now.isoformat())
