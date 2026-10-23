from datetime import datetime, timedelta

from app.core.constants import INBOUND_SCHEDULE
from app.core.domain import MOSCOW_TIMEZONE


def next_run(now: datetime) -> datetime:
    local = now.astimezone(MOSCOW_TIMEZONE)
    daily = local.replace(hour=INBOUND_SCHEDULE.daily_hour, minute=0, second=0, microsecond=0)
    if daily <= local:
        daily += timedelta(days=1)
    extra = local.replace(hour=INBOUND_SCHEDULE.extra_hour, minute=0, second=0, microsecond=0)
    extra += timedelta(
        days=(INBOUND_SCHEDULE.extra_weekday - local.weekday()) % INBOUND_SCHEDULE.days_per_week
    )
    if extra <= local:
        extra += timedelta(days=INBOUND_SCHEDULE.days_per_week)
    return min(daily, extra)


def delay() -> float:
    now = datetime.now(MOSCOW_TIMEZONE)
    return (next_run(now) - now).total_seconds()


def align(planned: datetime, now: datetime) -> datetime:
    local = planned.astimezone(MOSCOW_TIMEZONE)
    rounded = (local + timedelta(seconds=INBOUND_SCHEDULE.alignment_tolerance_seconds)).replace(
        minute=0, second=0, microsecond=0
    )
    allowed_hour = rounded.hour == INBOUND_SCHEDULE.daily_hour or (
        rounded.weekday() == INBOUND_SCHEDULE.extra_weekday and rounded.hour == INBOUND_SCHEDULE.extra_hour
    )
    if (
        allowed_hour
        and abs((rounded - local).total_seconds()) <= INBOUND_SCHEDULE.alignment_tolerance_seconds
    ):
        return rounded
    return next_run(now)
