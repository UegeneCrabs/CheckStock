from functools import partial

from app.application.google_weeks import GoogleWeeks
from app.config import settings
from app.core.constants import GOOGLE_WEEK, JOBS, Marketplace
from app.dto.google_week import WeekJobResult
from app.infrastructure.async_resources import AsyncResources
from app.jobs.scheduling import AsyncBackgroundJob


async def run(marketplace: Marketplace, resources: AsyncResources) -> WeekJobResult:
    return await GoogleWeeks(resources).scheduled(marketplace)


async def enabled(marketplace: Marketplace, resources: AsyncResources) -> bool:
    return settings.background_sync_enabled and await GoogleWeeks(resources).is_due(marketplace)


def delay() -> float:
    return GOOGLE_WEEK.poll_seconds


def jobs() -> tuple[AsyncBackgroundJob, ...]:
    return tuple(
        AsyncBackgroundJob(
            metadata.name,
            partial(run, metadata.marketplaces[0]),
            delay,
            startup_delay_seconds=GOOGLE_WEEK.startup_seconds,
            is_enabled=partial(enabled, metadata.marketplaces[0]),
        )
        for metadata in JOBS.google_weeks
    )
