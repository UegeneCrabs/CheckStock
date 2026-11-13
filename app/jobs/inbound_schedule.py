from datetime import datetime, timedelta

from app.config import settings
from app.core.constants import INBOUND_SCHEDULE


def delay() -> float:
    return settings.inbound_sync_interval_seconds


def align(planned: datetime, now: datetime) -> datetime:
    maximum = now + timedelta(seconds=delay() + INBOUND_SCHEDULE.alignment_tolerance_seconds)
    return now if planned > maximum else planned
