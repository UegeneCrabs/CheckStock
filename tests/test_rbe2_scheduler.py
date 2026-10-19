import sqlite3
import sys
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SITE = Path(__file__).resolve().parents[1] / 'rbe-yandex-site'
sys.path.insert(0, str(SITE))

from rbe2_scheduler import auto_cycle, plan, record_attempt
from rbe2_service import SCHEMA as SNAPSHOT_SCHEMA, save_snapshot
from rbe2_scheduler import SCHEMA as ATTEMPT_SCHEMA
from rbe2_calculation import canonical


def dashboard(tmp_path, monkeypatch):
    path = tmp_path / 'site.db'
    def connection():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    with connection() as conn:
        conn.executescript(SNAPSHOT_SCHEMA + ATTEMPT_SCHEMA + '''
            CREATE TABLE sync_store_attempts(store_slug TEXT PRIMARY KEY,attempted_at TEXT);
            CREATE TABLE daily_financial(store_slug TEXT,business_id INTEGER,report_date TEXT,
                values_json TEXT,calculated_at TEXT);''')
    return {'connection': connection, 'SYNC_LOCK': threading.Lock(),
            'SYNC_STOP_EVENT': threading.Event(), 'SYNC_COOLDOWN_SECONDS': 0,
            'RBE2_SYNC_INTERVAL_MINUTES': 30, 'RBE2_CYCLE_MAX_JOBS': 1,
            'RBE2_SYNC_LOOKBACK_DAYS': 14,
            'MOSCOW': ZoneInfo('Europe/Moscow'), '_api_cutoff': lambda: date(2026, 7, 21)}


def test_newest_first_backfill_stays_inside_cutoff(tmp_path, monkeypatch):
    site = dashboard(tmp_path, monkeypatch)
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    start, end = date(2026, 7, 21), date(2026, 10, 18)
    with site['connection']() as conn:
        jobs = plan(conn, ['tris'], start, end, now, limit=100)
    assert jobs[0] == ('tris', date(2026, 10, 12), end)
    assert jobs[-1][1] == start
    assert sum((finish - first).days + 1 for _, first, finish in jobs) == 90


def test_complete_snapshot_refreshes_daily_and_partial_retries(tmp_path, monkeypatch):
    site = dashboard(tmp_path, monkeypatch)
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    start, end = date(2026, 10, 12), date(2026, 10, 18)
    account = {'store_slug': 'tris', 'business_id': 1}
    with site['connection']() as conn:
        save_snapshot(conn, account, start, end, {'complete': True, 'expenses': {}},
                      (now - timedelta(hours=1)).isoformat())
        assert not plan(conn, ['tris'], start, end, now)
        assert plan(conn, ['tris'], start, end, now + timedelta(hours=25)) == [('tris', start, end)]
        # A newer incomplete snapshot must not be hidden by an older complete one.
        save_snapshot(conn, account, end, end, {'complete': False, 'expenses': {}}, now.isoformat())
        record_attempt(conn, 'tris', end, end, 'partial', now=now)
        assert not plan(conn, ['tris'], start, end, now + timedelta(minutes=10))
        assert plan(conn, ['tris'], start, end, now + timedelta(minutes=31)) == [('tris', end, end)]


def test_backfilled_older_days_are_not_requeried_daily(tmp_path, monkeypatch):
    site = dashboard(tmp_path, monkeypatch)
    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    old = date(2026, 8, 1)
    with site['connection']() as conn:
        save_snapshot(conn, {'store_slug': 'tris', 'business_id': 1}, old, old,
                      {'complete': True, 'expenses': {}}, (now-timedelta(days=10)).isoformat())
        jobs = plan(conn, ['tris'], old, date(2026, 10, 18), now, lookback_days=14, limit=100)
        assert not any(first <= old <= finish for _, first, finish in jobs)


def test_latest_incomplete_check_invalidates_old_coverage():
    common = {'store_slug': 'tris', 'business_id': 1, 'date_from': '2026-10-12',
              'date_to': '2026-10-12'}
    data = {'orders': [], 'margins': [], 'stats': [], 'returns_by_day': {}}
    old = {**common, 'fetched_at': '2026-10-12T10:00:00+00:00',
           'data': {**data, 'complete': True}}
    latest = {**common, 'fetched_at': '2026-10-12T11:00:00+00:00',
              'data': {**data, 'complete': False}}
    assert canonical([old])[4] == {('tris', 1, '2026-10-12')}
    assert canonical([old, latest])[4] == set()


def test_auto_cycle_only_writes_rbe2_snapshot_and_respects_lock(tmp_path, monkeypatch):
    site = dashboard(tmp_path, monkeypatch)
    site['_api_cutoff'] = lambda: datetime.now(site['MOSCOW']).date() - timedelta(days=3)
    accounts = [{'store_slug': 'tris', 'business_id': 1, 'api_key': 'secret'}]
    monkeypatch.setattr('rbe2_scheduler.fetch_packet', lambda *args: {'complete': True, 'expenses': {}, 'orders': [], 'margins': [], 'stats': [], 'returns_by_day': {}})
    site['SYNC_LOCK'].acquire()
    try:
        assert auto_cycle(site, accounts) == 0
    finally:
        site['SYNC_LOCK'].release()
    assert auto_cycle(site, accounts) == 1
    with site['connection']() as conn:
        assert conn.execute('SELECT COUNT(*) FROM rbe2_snapshots').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM daily_financial').fetchone()[0] == 0
        assert conn.execute('SELECT status FROM rbe2_runs').fetchone()[0] == 'completed'
