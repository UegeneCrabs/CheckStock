from datetime import datetime

from sqlalchemy import select

from app.core.constants import GOOGLE_STOCK_TEST, JOBS, JobTrigger
from app.dto.google_stock_report import StockTestSchedule, StockTestScheduleState
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.orm import GoogleStockTestScheduleRecord, SyncJobRunRecord

SCHEDULE = table_for(GoogleStockTestScheduleRecord)
RUNS = table_for(SyncJobRunRecord)


class GoogleStockTestRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def seed_server_schedule(self, now: datetime) -> None:
        schedule = StockTestSchedule(enabled=True, run_time=GOOGLE_STOCK_TEST.server_run_time)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    SCHEDULE,
                    {
                        "name": JOBS.google_stock_test.name,
                        "payload": schedule.model_dump_json(),
                        "activated_at": now.isoformat(),
                    },
                    ("name",),
                    updates={"name": JOBS.google_stock_test.name},
                )
            )

    async def get(self) -> StockTestScheduleState:
        name = JOBS.google_stock_test.name
        async with self.database.transaction() as connection:
            row = (
                (await connection.execute(select(SCHEDULE).where(SCHEDULE.c.name == name))).mappings().first()
            )
            if row is None:
                return StockTestScheduleState(schedule=StockTestSchedule())
            latest = (
                await connection.execute(
                    select(RUNS.c.started_at)
                    .where(RUNS.c.name == name, RUNS.c.trigger == JobTrigger.SCHEDULED.value)
                    .order_by(RUNS.c.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return StockTestScheduleState(
            schedule=StockTestSchedule.model_validate_json(row["payload"]),
            activated_at=datetime.fromisoformat(row["activated_at"]),
            last_scheduled_at=datetime.fromisoformat(latest) if latest else None,
        )

    async def save(self, schedule: StockTestSchedule, now: datetime) -> None:
        name = JOBS.google_stock_test.name
        payload = schedule.model_dump_json()
        async with self.database.transaction(write=True) as connection:
            saved = (
                await connection.execute(select(SCHEDULE.c.payload).where(SCHEDULE.c.name == name))
            ).scalar_one_or_none()
            if saved == payload:
                return
            await connection.execute(
                upsert(
                    connection,
                    SCHEDULE,
                    {"name": name, "payload": payload, "activated_at": now.isoformat()},
                    ("name",),
                )
            )
