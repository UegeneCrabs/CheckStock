"""No real API calls: exercise retry persistence, scope, and partial failures."""
import importlib.util
import json
import sys
import threading
from datetime import date, datetime, UTC
from pathlib import Path
from types import SimpleNamespace

import pytest

SITE = Path(__file__).resolve().parents[1] / 'rbe-yandex-site'
sys.path.insert(0, str(SITE))
spec = importlib.util.spec_from_file_location('rbe_sync_test', SITE / 'main.py')
site = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = site
spec.loader.exec_module(site)
DAY = date(2026, 10, 12)


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(site, 'DATABASE_PATH', tmp_path / 'site.db')
    monkeypatch.setattr(site, 'load_connections', lambda: [{'store_slug': 'tris', 'business_id': 1}])
    monkeypatch.setattr(site, '_require_admin', lambda request: {'role': 'admin'})
    monkeypatch.setattr(site, '_require_user', lambda request: {'role': 'admin'})
    monkeypatch.setattr(site, 'SYNC_LOCK', threading.Lock())
    monkeypatch.setattr(site, 'SYNC_STOP_EVENT', threading.Event())
    monkeypatch.setattr(site, 'SYNC_COOLDOWN_SECONDS', 0)
    monkeypatch.setattr(site, '_sync_period', lambda: (date(2026, 9, 29), DAY))
    site.init_database()
    return site


def fact(dashboard, day=DAY, ready=True, cost=0):
    with dashboard.connection() as db:
        db.execute('INSERT OR REPLACE INTO daily_financial VALUES(?,?,?,?,?)', ('tris', 1, day.isoformat(), json.dumps({'sold_cost_of_goods': cost}), 'v1'))
        db.execute('INSERT OR REPLACE INTO daily_source_quality VALUES(?,?,?,?,?,?,?)', ('tris', 1, day.isoformat(), 'v1', 1, int(ready), 'test'))


def queue(dashboard):
    with dashboard.connection() as db:
        return [dict(row) for row in db.execute('SELECT * FROM sync_retry_days ORDER BY store_slug,report_date')]


def test_gap_survives_restart_and_is_removed_only_when_complete(dashboard):
    fact(dashboard, ready=False)
    dashboard._refresh_retry_queue(DAY, DAY, ('tris',))
    assert len(queue(dashboard)) == 1
    dashboard.init_database()
    assert len(queue(dashboard)) == 1
    fact(dashboard)
    dashboard._refresh_retry_queue(DAY, DAY, ('tris',))
    assert queue(dashboard) == []


def test_purchase_price_gap_does_not_hammer_market_api(dashboard):
    fact(dashboard, cost=None)
    dashboard._refresh_retry_queue(DAY, DAY, ('tris',))
    assert queue(dashboard) == []


@pytest.mark.parametrize('error', ['420 rate limit', 'NO_DATA', 'HTTP 500'])
def test_failed_refresh_of_old_complete_day_is_persisted(dashboard, monkeypatch, error):
    old = date(2026, 8, 1)
    fact(dashboard, old)
    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr=error))
    for _ in range(4):
        assert dashboard._run_official_sync(old, old, ('tris',))['status'] == 'retrying'
    dashboard._refresh_retry_queue(old, old, ('tris',))
    assert queue(dashboard)[0]['force_retry'] == 1
    assert dashboard._older_retry_ranges(date(2026, 9, 29)) == [('tris', old, old)]
    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='ok', stderr=''))
    dashboard._run_official_sync(old, old, ('tris',))
    assert queue(dashboard) == []


def test_successful_but_incomplete_response_keeps_retrying(dashboard, monkeypatch):
    fact(dashboard, ready=False)
    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='ok', stderr=''))
    dashboard._run_official_sync(DAY, DAY, ('tris',))
    assert len(queue(dashboard)) == 1 and queue(dashboard)[0]['force_retry'] == 0


def test_old_queue_fairness_and_bounded_batches(dashboard):
    with dashboard.connection() as db:
        for day in range(1, 31):
            db.execute('INSERT INTO sync_retry_days VALUES(?,?,0,NULL)', ('tris', f'2026-08-{day:02}'))
    assert dashboard._older_retry_ranges(DAY) == [('tris', date(2026,8,1), date(2026,8,14))]
    with dashboard.connection() as db:
        db.execute("UPDATE sync_retry_days SET last_attempt='2026-10-13' WHERE report_date<='2026-08-14'")
    assert dashboard._older_retry_ranges(DAY) == [('tris', date(2026,8,15), date(2026,8,28))]


def test_discovery_recovers_old_gaps_and_holes(dashboard):
    fact(dashboard, date(2026,9,1), ready=False)
    fact(dashboard, date(2026,9,3))
    dashboard._discover_retry_days()
    days = {row['report_date'] for row in queue(dashboard)}
    assert {'2026-09-01','2026-09-02','2026-10-12'} <= days
    assert '2026-09-03' not in days


def test_manual_request_reserves_lock_tracks_job_and_respects_store(dashboard, monkeypatch):
    calls = []
    class Thread:
        def __init__(self, **kwargs): calls.append(kwargs)
        def start(self): pass
    monkeypatch.setattr(dashboard.threading, 'Thread', Thread)
    result = dashboard.start_sync(None, dashboard.SyncRequest(date_from=DAY, date_to=DAY, store_slug='tris'))
    assert result['run_id'] > 0
    assert calls[0]['kwargs']['stores'] == ('tris',)
    assert calls[0]['kwargs']['lock_already_held']
    assert dashboard.SYNC_LOCK.locked() and len(queue(dashboard)) == 1
    with pytest.raises(dashboard.HTTPException) as exc:
        dashboard.start_sync(None, dashboard.SyncRequest(date_from=DAY, date_to=DAY))
    assert exc.value.status_code == 409


def test_manual_refresh_all_stores(dashboard, monkeypatch):
    monkeypatch.setattr(dashboard, 'load_connections', lambda: [{'store_slug':'tris'}, {'store_slug':'rimili'}])
    calls = []
    monkeypatch.setattr(dashboard.threading, 'Thread', lambda **kwargs: SimpleNamespace(start=lambda: calls.append(kwargs)))
    dashboard.start_sync(None, dashboard.SyncRequest(date_from=DAY, date_to=DAY))
    assert calls[0]['kwargs']['stores'] == ('rimili', 'tris')


def test_invalid_store_does_not_start_import(dashboard):
    with pytest.raises(dashboard.HTTPException) as exc:
        dashboard.start_sync(None, dashboard.SyncRequest(date_from=DAY, date_to=DAY, store_slug='other'))
    assert exc.value.status_code == 422 and not dashboard.SYNC_LOCK.locked()


def test_manual_job_waiting_does_not_claim_success_on_no_data(dashboard, monkeypatch):
    fact(dashboard, ready=False)
    with dashboard.connection() as db:
        run_id = db.execute("INSERT INTO targeted_sync_runs(metric,started_at,status) VALUES('period','now','queued')").lastrowid
    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout='NO_DATA', stderr=''))
    dashboard.SYNC_LOCK.acquire()
    dashboard._run_period_sync(run_id, DAY, DAY, ('tris',), True)
    result = dashboard.targeted_sync_status(None, run_id)['run']
    assert result['status'] == 'waiting' and not dashboard.SYNC_LOCK.locked()
    assert len(queue(dashboard)) == 1


def test_cooldown_and_shutdown_preserve_queued_dates(dashboard, monkeypatch):
    monkeypatch.setattr(dashboard, 'SYNC_COOLDOWN_SECONDS', 125)
    with dashboard.connection() as db:
        db.execute('INSERT INTO sync_store_attempts VALUES(?,?)', ('tris', datetime.now(UTC).isoformat()))
    dashboard.SYNC_STOP_EVENT.set()
    monkeypatch.setattr(dashboard.subprocess, 'run', lambda *a, **k: pytest.fail('Do not send requests during cooldown/shutdown'))
    assert dashboard._run_official_sync(DAY, DAY, ('tris',))['status'] == 'retrying'
    assert len(queue(dashboard)) == 1 and not dashboard.SYNC_LOCK.locked()


def test_automatic_loop_keeps_recent_refresh_and_old_retries(dashboard, monkeypatch):
    calls = []
    old = date(2026, 8, 1)
    monkeypatch.setattr(dashboard, '_discover_retry_days', lambda: calls.append('discover'))
    monkeypatch.setattr(dashboard, '_older_retry_ranges', lambda before: [('tris', old, old)])
    monkeypatch.setattr(dashboard, '_run_official_sync', lambda *args: calls.append(args))
    class Stop:
        stopped = False
        def is_set(self): return self.stopped
        def wait(self, seconds): self.stopped = True
    monkeypatch.setattr(dashboard, 'SYNC_STOP_EVENT', Stop())
    dashboard._sync_loop()
    assert calls == ['discover', (), (old, old, ('tris',))]


def test_worker_exception_does_not_kill_auto_loop(dashboard, monkeypatch):
    calls = []
    def discover():
        calls.append('attempt')
        raise RuntimeError('temporary DB error')
    monkeypatch.setattr(dashboard, '_discover_retry_days', discover)
    class Stop:
        count = 0
        def is_set(self): return self.count == 2
        def wait(self, seconds): self.count += 1
    monkeypatch.setattr(dashboard, 'SYNC_STOP_EVENT', Stop())
    dashboard._sync_loop()
    assert calls == ['attempt', 'attempt']


@pytest.mark.parametrize('successful', [(), ('tris',)])
def test_importer_never_copies_failed_store_as_fresh(monkeypatch, successful):
    sys.path.insert(0, str(SITE / 'scripts'))
    spec = importlib.util.spec_from_file_location('rbe_refresh_test', SITE / 'scripts' / 'refresh_official_period.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, '_import_store', lambda store, *args: {'store': store, 'ok': store in successful})
    copied = []
    monkeypatch.setattr(runner, 'copy_into_rbe', lambda *args, **kwargs: copied.append(kwargs['stores']))
    assert runner.run(DAY, DAY, ('tris','rimili')) == 1
    assert copied == ([successful] if successful else [])


def test_runner_copies_old_refund_dates_to_site(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('rbe_refund_history_runner_test', SITE / 'scripts' / 'refresh_official_period.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, '_import_store', lambda store, *args:
        {'store':store, 'ok':True, 'report':{'reports':[{'reconciled_dates':['2026-09-01','2026-10-12']}]}})
    copied = []
    monkeypatch.setattr(runner, 'copy_into_rbe', lambda *a, **kw: copied.append(kw))
    assert runner.run(DAY, DAY, ('tris',)) == 0
    assert copied == [{'stores':('tris',), 'date_from':date(2026,9,1), 'date_to':date(2026,10,12)}]
