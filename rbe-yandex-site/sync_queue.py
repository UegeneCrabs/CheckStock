"""Bounded, newest-first refresh planning and user-facing queue explanations."""
from datetime import date, datetime, timedelta

MAX_BATCH_DAYS = 7

SCHEMA = '''CREATE TABLE IF NOT EXISTS sync_day_checks (
    store_slug TEXT NOT NULL, report_date TEXT NOT NULL,
    checked_at TEXT, reason TEXT NOT NULL DEFAULT 'requested',
    PRIMARY KEY(store_slug,report_date));'''

REASONS = {
    'requested': ('Ожидает загрузки', 'Обновление запланировано, но ещё не завершено.'),
    'unknown': ('Незавершённая проверка', 'Задание осталось от прежней загрузки. Точная причина не сохранена.'),
    'rate_limit': ('Пауза по ограничению Яндекса', 'Яндекс ограничил частоту запросов. Повторим после паузы.'),
    'unavailable': ('Доступ ограничен', 'Яндекс отказал в доступе к отчёту. Нужно проверить подключение или доступ к этому периоду.'),
    'no_data': ('Отчёт ещё не готов', 'Яндекс пока не подготовил данные за эти дни.'),
    'error': ('Ошибка загрузки', 'Загрузка не завершилась. Сохранённые данные не удалены; нужна повторная попытка.'),
    'incomplete': ('Не хватает данных', 'Отчёты получены, но подтверждены не все необходимые показатели.'),
    'complete': ('Плановая сверка', 'Данные получены. Проверяем, не внёс ли Яндекс исправления.'),
}


def error_reason(detail):
    if '420' in detail or '429' in detail or 'rate limit' in detail.lower():
        return 'rate_limit'
    if '90 days' in detail or '401' in detail or '403' in detail:
        return 'unavailable'
    if 'NO_DATA' in detail or 'не смог сформировать' in detail:
        return 'no_data'
    return 'error'


def newest_ranges(missing, max_days=MAX_BATCH_DAYS):
    """Non-overlapping consecutive chunks, ordered by their newest date globally."""
    result = []
    for store, values in missing.items():
        days = sorted({date.fromisoformat(d) for d in values}, reverse=True)
        if not days:
            continue
        end = start = days[0]
        for day in days[1:]:
            if day == start - timedelta(days=1) and (end - day).days < max_days:
                start = day
            else:
                result.append((store, start, end))
                end = start = day
        result.append((store, start, end))
    return sorted(result, key=lambda r: (-r[2].toordinal(), r[0]))


def automatic_plan(stores, pending, checked, start, end, cutoff, now):
    """Yesterday first. Complete earlier days need at most one check per 24h."""
    wanted = {s: set() for s in stores}
    for row in pending:
        if row['store_slug'] in wanted and cutoff.isoformat() <= row['report_date'] <= end.isoformat():
            wanted[row['store_slug']].add(row['report_date'])
    for store in stores:
        day = max(start, cutoff)
        while day <= end:
            stamp = checked.get((store, day.isoformat()))
            if day == end or not stamp or now - datetime.fromisoformat(stamp) >= timedelta(hours=24):
                wanted[store].add(day.isoformat())
            day += timedelta(days=1)
    latest = [(s, end, end) for s in sorted(stores) if end.isoformat() in wanted[s]]
    for values in wanted.values():
        values.discard(end.isoformat())
    return latest + newest_ranges(wanted)
