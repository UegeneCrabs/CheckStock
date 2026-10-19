"""Durable, newest-first backfill and refresh for operational RBE 2."""

import json
import logging
import threading
from datetime import UTC, date, datetime, timedelta

from sync_queue import newest_ranges
from rbe2_service import fetch_packet, save_snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS rbe2_day_attempts (
    store_slug TEXT NOT NULL,
    report_date TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (store_slug, report_date)
);
"""


def record_attempt(conn, store, start, end, status, detail='', now=None):
    stamp = (now or datetime.now(UTC)).isoformat()
    rows = [(store, (start + timedelta(days=offset)).isoformat(), stamp, status, detail[:500])
            for offset in range((end - start).days + 1)]
    conn.executemany('''INSERT INTO rbe2_day_attempts VALUES(?,?,?,?,?)
        ON CONFLICT(store_slug,report_date) DO UPDATE SET
        attempted_at=excluded.attempted_at,status=excluded.status,detail=excluded.detail''', rows)


def plan(conn, stores, cutoff, end, now, *, retry_minutes=30, refresh_hours=24,
         lookback_days=14, limit=6):
    """Return bounded jobs. Missing days outrank stale ones at the same date."""
    if cutoff > end or not stores:
        return []
    stores = sorted(set(stores))
    wanted = {store: set() for store in stores}
    refresh_start = max(cutoff, end - timedelta(days=lookback_days - 1))
    latest = {}
    rows = conn.execute('''SELECT store_slug,date_from,date_to,fetched_at,payload_json
        FROM rbe2_snapshots WHERE date_to>=? AND date_from<=?
        ORDER BY fetched_at''', (cutoff.isoformat(), end.isoformat()))
    for row in rows:
        store = row['store_slug']
        if store not in wanted:
            continue
        packet = json.loads(row['payload_json'])
        complete = packet.get('complete', True) and not packet.get('expenses', {}).get('load_error')
        first = max(cutoff, date.fromisoformat(row['date_from']))
        last = min(end, date.fromisoformat(row['date_to']))
        cursor = first
        while cursor <= last:
            key = (store, cursor.isoformat())
            if key not in latest or row['fetched_at'] >= latest[key][0]:
                latest[key] = (row['fetched_at'], complete)
            cursor += timedelta(days=1)
    attempts = {(r['store_slug'], r['report_date']): r for r in conn.execute(
        'SELECT * FROM rbe2_day_attempts WHERE report_date>=? AND report_date<=?',
        (cutoff.isoformat(), end.isoformat()))}
    for store in stores:
        cursor = cutoff
        while cursor <= end:
            day = cursor.isoformat()
            recorded = latest.get((store, day))
            success = recorded[0] if recorded and recorded[1] else None
            attempted = attempts.get((store, day))
            if attempted and now - datetime.fromisoformat(attempted['attempted_at']) < timedelta(minutes=retry_minutes):
                cursor += timedelta(days=1)
                continue
            if not success or (cursor >= refresh_start and
                               now - datetime.fromisoformat(success) >= timedelta(hours=refresh_hours)):
                wanted[store].add(day)
            cursor += timedelta(days=1)
    return newest_ranges(wanted)[:limit]


def load_account(site, account, start, end):
    """One cabinet transaction; manual and automatic runs use the same path."""
    store = account['store_slug']
    with site['connection']() as conn:
        row = conn.execute('SELECT attempted_at FROM sync_store_attempts WHERE store_slug=?',
                           (store,)).fetchone()
    wait = max(0, site['SYNC_COOLDOWN_SECONDS'] -
               (datetime.now(UTC) - datetime.fromisoformat(row[0])).total_seconds()) if row else 0
    if wait and site['SYNC_STOP_EVENT'].wait(wait):
        raise RuntimeError('Сервер остановлен; незавершённые даты остаются в очереди')
    status, detail = 'failed', ''
    try:
        if start < site['_api_cutoff']():
            raise ValueError('Дата вышла за предел 90 дней до начала запроса')
        packet = fetch_packet(account, start, end)
        with site['connection']() as conn:
            save_snapshot(conn, account, start, end, packet)
        issue = packet.get('expenses', {}).get('load_error')
        if not packet.get('complete', True):
            issue = issue or '; '.join(packet.get('notes', [])) or 'Ответ API неполный'
        status = 'completed' if not issue else 'partial'
        detail = str(issue or '')
    except Exception as error:
        detail = str(error)[:500]
    finally:
        with site['connection']() as conn:
            stamp = datetime.now(UTC)
            conn.execute('INSERT OR REPLACE INTO sync_store_attempts VALUES(?,?)',
                         (store, stamp.isoformat()))
            record_attempt(conn, store, start, end, status, detail, stamp)
    return status, detail


def auto_cycle(site, accounts, *, now=None):
    """Attempt a few newest-first jobs without monopolising the RBE 1 lock."""
    now = now or datetime.now(UTC)
    end = datetime.now(site['MOSCOW']).date() - timedelta(days=1)
    cutoff = site['_api_cutoff']()
    by_store = {a['store_slug']: a for a in accounts}
    with site['connection']() as conn:
        jobs = plan(conn, by_store, cutoff, end, now,
                    retry_minutes=site['RBE2_SYNC_INTERVAL_MINUTES'],
                    lookback_days=site['RBE2_SYNC_LOOKBACK_DAYS'],
                    limit=site['RBE2_CYCLE_MAX_JOBS'])
    attempted = 0
    for store, start, finish in jobs:
        if site['SYNC_STOP_EVENT'].is_set():
            break
        with site['connection']() as conn:
            row = conn.execute('SELECT attempted_at FROM sync_store_attempts WHERE store_slug=?',
                               (store,)).fetchone()
        if row and (datetime.now(UTC)-datetime.fromisoformat(row[0])).total_seconds() < site['SYNC_COOLDOWN_SECONDS']:
            continue
        if not site['SYNC_LOCK'].acquire(blocking=False):
            break
        try:
            with site['connection']() as conn:
                run_id = conn.execute('INSERT INTO rbe2_runs(started_at,status,detail) VALUES(?,?,?)',
                    (datetime.now(UTC).isoformat(), 'running',
                     f'Автозагрузка {store.upper()}: {start} — {finish}')).lastrowid
            status, detail = load_account(site, by_store[store], start, finish)
            with site['connection']() as conn:
                conn.execute('UPDATE rbe2_runs SET status=?,finished_at=?,detail=? WHERE id=?',
                    (status, datetime.now(UTC).isoformat(), detail or 'Автозагрузка завершена', run_id))
            attempted += 1
        finally:
            site['SYNC_LOCK'].release()
    return attempted


def start_background(site):
    def loop():
        while not site['SYNC_STOP_EVENT'].is_set():
            try:
                count = auto_cycle(site, site['load_connections']())
            except Exception:
                logging.exception('RBE 2 automatic refresh failed; next cycle will retry')
                count = 0
            # Continue backfill promptly, but allow RBE 1 to get the shared lock.
            pause = 10 if count else site['RBE2_SYNC_INTERVAL_MINUTES'] * 60
            site['SYNC_STOP_EVENT'].wait(pause)
    thread = threading.Thread(target=loop, name='rbe2-operational-sync', daemon=True)
    thread.start()
    return thread
