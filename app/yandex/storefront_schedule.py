"""Daily storefront checks at 01:00 and hourly 08:00–19:00, Yekaterinburg time."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Asia/Yekaterinburg")
RUN_HOURS = (1, *range(8, 20))


def next_run_at(after: datetime | None = None) -> datetime:
    local = (after or datetime.now(UTC)).astimezone(TIMEZONE)
    for offset in (0, 1):
        day = local + timedelta(days=offset)
        for hour in RUN_HOURS:
            candidate = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if candidate > local:
                return candidate.astimezone(UTC)
    raise AssertionError("The next day always contains a scheduled run")
