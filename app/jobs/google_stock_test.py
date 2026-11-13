from datetime import UTC, datetime

from app.application.google_stock_test import next_run
from app.container import google_stock_test_service
from app.core.constants import GOOGLE_WEEK, JOBS
from app.dto.google_stock_report import StockTestJobResult
from app.infrastructure.async_resources import AsyncResources
from app.jobs.scheduling import AsyncBackgroundJob


async def due(resources: AsyncResources) -> bool:
    now = datetime.now(UTC)
    slot = next_run(await google_stock_test_service(resources).schedules.get(), now)
    return slot is not None and slot <= now


async def run(resources: AsyncResources) -> StockTestJobResult:
    return await google_stock_test_service(resources).run(datetime.now(UTC))


def delay() -> float:
    return GOOGLE_WEEK.poll_seconds


def job() -> AsyncBackgroundJob:
    return AsyncBackgroundJob(JOBS.google_stock_test.name, run, delay, is_enabled=due)
