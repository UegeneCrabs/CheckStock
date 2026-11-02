from datetime import datetime, timedelta

from app.core.constants import OZON_STORAGE, TIME


def next_run(now: datetime) -> datetime:
    local = now.astimezone(TIME.moscow_timezone)
    target = local.replace(hour=OZON_STORAGE.run_hour_msk, minute=0, second=0, microsecond=0)
    target += timedelta(days=(OZON_STORAGE.run_weekday_msk - local.weekday()) % OZON_STORAGE.days_per_week)
    if target <= local:
        target += timedelta(days=OZON_STORAGE.days_per_week)
    return target


def delay() -> float:
    now = datetime.now(TIME.moscow_timezone)
    return (next_run(now) - now).total_seconds()


def align(planned: datetime, now: datetime) -> datetime:
    local = planned.astimezone(TIME.moscow_timezone)
    rounded = (local + timedelta(seconds=OZON_STORAGE.alignment_tolerance_seconds)).replace(
        minute=0, second=0, microsecond=0
    )
    if (
        rounded.weekday() == OZON_STORAGE.run_weekday_msk
        and rounded.hour == OZON_STORAGE.run_hour_msk
        and abs((rounded - local).total_seconds()) <= OZON_STORAGE.alignment_tolerance_seconds
    ):
        return rounded
    return next_run(now)
