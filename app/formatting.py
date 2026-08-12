from datetime import datetime

from app.domain import MOSCOW_TIMEZONE


def format_dt(iso_str: str | None) -> str:

    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return str(iso_str)
    if dt.tzinfo is not None:
        dt = dt.astimezone(MOSCOW_TIMEZONE)
    return dt.strftime("%d.%m.%Y %H:%M") + " МСК"
