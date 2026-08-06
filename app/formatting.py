"""
Общее форматирование значений для показа человеку.

В БД время хранится в UTC в ISO-формате (см. _now в модулях синхронизации и
импорта), а показываем мы его по Москве — по московскому времени работают и
WB, и сами продавцы.
"""

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def format_dt(iso_str: str | None) -> str:
    """ISO-строка из БД -> "04.08.2026 02:41 МСК". Пустое/битое значение
    превращается в прочерк, чтобы не показывать пользователю мусор."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return str(iso_str)
    if dt.tzinfo is not None:
        dt = dt.astimezone(MSK)
    return dt.strftime("%d.%m.%Y %H:%M") + " МСК"
