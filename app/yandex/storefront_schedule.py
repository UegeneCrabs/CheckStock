from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.constants import JOB_SCHEDULE_RULES, JOBS

TIMEZONE = ZoneInfo("Europe/Moscow")

RUN_HOURS = next(rule.hours for rule in JOB_SCHEDULE_RULES if rule.name == JOBS.yandex_storefront_prices.name)


def next_run_at(after: datetime | None = None) -> datetime:
    local = (after or datetime.now(UTC)).astimezone(TIMEZONE)
    for offset in (0, 1):
        day = local + timedelta(days=offset)
        for hour in RUN_HOURS:
            candidate = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > local:
                return candidate.astimezone(UTC)
    raise AssertionError("The next day always contains a scheduled run")
