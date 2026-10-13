import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1] / 'rbe-yandex-site'
sys.path.insert(0, str(SITE))
import report_quality as quality

spec = importlib.util.spec_from_file_location('rbe_dashboard_quality_test', SITE / 'main.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


def summary(days=(), deliveries=(), payments=()):
    return {'days': set(days), 'deliveries': set(deliveries), 'payments': set(payments)}


def test_trailing_day_is_not_a_confirmed_zero_even_without_orders():
    ready, reason = quality.financial_evidence('2026-10-12', summary(), summary(['2026-10-11']))
    assert not ready
    assert '12.10.2026' in reason and '11.10.2026' in reason


def test_interior_zero_day_is_valid():
    assert quality.financial_evidence('2026-10-06', summary(), summary(['2026-10-05', '2026-10-11']))[0]


def test_orders_without_payment_operations_stay_incomplete():
    assert not quality.financial_evidence('2026-10-12', summary(deliveries=['2026-10-12']), summary(['2026-10-12']))[0]


def test_services_arrive_and_day_becomes_complete():
    assert quality.financial_evidence('2026-10-12', summary(deliveries=['2026-10-12']), summary(['2026-10-12'], payments=['2026-10-12']))[0]


@pytest.mark.parametrize('orders,services', [(None, summary()), (summary(), None), (summary(), summary())])
def test_missing_or_empty_sources_cannot_prove_completeness(orders, services):
    assert not quality.financial_evidence('2026-10-12', orders, services)[0]


def test_archive_evidence_uses_latest_snapshot_available_at_calculation():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE yandex_financial_report_rows(store_slug,business_id,report_kind,period_from,period_to,imported_at,sheet,payload_json)')
    def add(kind, imported, sheet, payload):
        db.execute('INSERT INTO yandex_financial_report_rows VALUES(?,?,?,?,?,?,?,?)',
                   ('tris', 1, kind, '2026-10-01', '2026-10-12', imported, sheet, json.dumps(payload)))
    add('united-orders', '2026-10-13T06:00:00+00:00', 'orders_and_offers_transactions', {'deliveryDate':'12.10.2026'})
    add('united-services', '2026-10-13T06:00:00+00:00', 'payment_transfer', {'serviceDateTime':'11.10.2026 10:00:00'})
    add('united-services', '2026-10-13T09:00:00+00:00', 'payment_transfer', {'serviceDateTime':'2026-10-12 10:00:00'})
    row = {'store_slug':'tris','business_id':1,'report_date':'2026-10-12','calculated_at':'2026-10-13T07:00:00+00:00'}
    assert quality.build_quality(db, [row])[0][5] == 0
    row['calculated_at'] = '2026-10-13T10:00:00+00:00'
    assert quality.build_quality(db, [row])[0][5] == 1
    db.close()


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'DATABASE_PATH', tmp_path / 'site.db')
    monkeypatch.setattr(dashboard, 'load_connections', lambda: [{'store_slug':'tris','business_id':1}])
    dashboard.init_database()
    return dashboard


def fact(site, business=1, complete=False, proof=True, cost=0):
    with site.connection() as db:
        db.execute('INSERT OR REPLACE INTO daily_financial VALUES(?,?,?,?,?)',
                   ('tris',business,'2026-10-12',json.dumps({'sold_cost_of_goods':cost}), 'v1'))
        if proof:
            quality.save_quality(db, [('tris',business,'2026-10-12','v1',1,int(complete),'Нет финансовых операций за 12.10.2026')])


def test_placeholder_is_not_green_and_cost_inherits_financial_gaps(site):
    fact(site)
    states = site.report_completeness(date(2026,10,12),date(2026,10,12),None)
    assert states['orders']['complete']
    for source in ('financial','cost'):
        assert not states[source]['complete']
        assert states[source]['missing'] == {'tris':['2026-10-12']}
        assert '12.10.2026' in states[source]['detail']
    assert site.repair_source('marginal_profit', states) == 'financial'
    assert site.repair_source('sold_cost_of_goods', states) == 'financial'


def test_legacy_and_stale_proof_cannot_turn_finances_green(site):
    fact(site, proof=False)
    assert not site.report_completeness(date(2026,10,12),date(2026,10,12),None)['financial']['complete']
    fact(site, complete=True)
    with site.connection() as db:
        db.execute("UPDATE daily_financial SET calculated_at='v2'")
    assert not site.report_completeness(date(2026,10,12),date(2026,10,12),None)['financial']['complete']


def test_green_requires_every_cabinet_and_day(site, monkeypatch):
    fact(site, complete=True)
    monkeypatch.setattr(site, 'load_connections', lambda: [{'store_slug':'tris','business_id':1},{'store_slug':'tris','business_id':2}])
    assert not site.report_completeness(date(2026,10,12),date(2026,10,12),None)['financial']['complete']
    fact(site, business=2, complete=True)
    assert site.report_completeness(date(2026,10,12),date(2026,10,12),None)['financial']['complete']
    assert not site.report_completeness(date(2026,10,11),date(2026,10,12),None)['financial']['complete']


def test_cost_only_gaps_still_require_cost_entry(site):
    fact(site, complete=True, cost=None)
    states = site.report_completeness(date(2026,10,12),date(2026,10,12),None)
    assert states['financial']['complete'] and not states['cost']['complete']
    assert site.repair_source('sold_cost_of_goods', states) == 'cost'


def test_backfill_only_changes_metadata(tmp_path):
    source_path, target_path = tmp_path/'source.db', tmp_path/'target.db'
    with sqlite3.connect(source_path) as db:
        db.execute('CREATE TABLE yandex_financial_report_rows(store_slug,business_id,report_kind,period_from,period_to,imported_at,sheet,payload_json)')
    with sqlite3.connect(target_path) as db:
        db.execute('CREATE TABLE daily_financial(store_slug,business_id,report_date,values_json,calculated_at)')
        db.execute('INSERT INTO daily_financial VALUES(?,?,?,?,?)', ('tris',1,'2026-10-12','{"sales":-1}','v1'))
        before = db.execute('SELECT * FROM daily_financial').fetchall()
    for _ in range(2):
        assert quality.backfill(source_path.as_posix(), target_path.as_posix()) == {'checked':1,'incomplete':1}
    with sqlite3.connect(target_path) as db:
        assert db.execute('SELECT * FROM daily_financial').fetchall() == before
        assert db.execute('SELECT COUNT(*) FROM daily_source_quality').fetchone()[0] == 1


def test_api_response_marks_all_dependent_metrics_and_keeps_amounts(site, monkeypatch):
    fact(site)
    monkeypatch.setattr(site, '_require_user', lambda request: {'role':'admin'})
    result = site.report(None, date(2026,10,12), date(2026,10,12))
    for metric, state in result['quality'].items():
        assert state['complete'] == (metric == 'orders_turnover')
        if metric != 'orders_turnover':
            assert state['can_repair']
    assert result['values']['sold_cost_of_goods'] == 0


def test_no_data_response_is_explicit_and_not_downloaded(monkeypatch):
    from app.yandex import financial_reports
    responses = iter([{'reportId':'test'}, {'status':'DONE','subStatus':'NO_DATA'}])
    monkeypatch.setattr(financial_reports.api, 'request', lambda *a, **k: next(responses))
    monkeypatch.setattr(financial_reports, '_download_archive', lambda *a: pytest.fail('No download for NO_DATA'))
    with pytest.raises(RuntimeError, match='NO_DATA'):
        financial_reports._load_report('test-key','test-endpoint',{})


@pytest.mark.parametrize('complete', [False, True])
def test_cost_recalculation_preserves_source_proof(site, tmp_path, monkeypatch, complete):
    spec = importlib.util.spec_from_file_location('rbe_cost_quality_test', SITE / 'scripts' / 'recalculate_cost_of_goods.py')
    costs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(costs)
    monkeypatch.setattr(costs, 'connection', site.connection)
    monkeypatch.setattr(costs, '_source_reports', lambda source: {
        ('tris', 1): {costs.ORDER_KIND: {}, costs.SERVICE_KIND: {}}
    })
    fact(site, complete=complete)
    before = site.report_completeness(date(2026,10,12), date(2026,10,12), None)
    costs.recalculate(tmp_path / 'source.db')
    after = site.report_completeness(date(2026,10,12), date(2026,10,12), None)
    assert after == before
    with site.connection() as db:
        assert db.execute('SELECT calculated_at FROM daily_financial').fetchone()[0] == 'v1'
