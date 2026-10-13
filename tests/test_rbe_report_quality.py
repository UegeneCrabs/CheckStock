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
from report_details import build_details, SPECS

spec = importlib.util.spec_from_file_location('rbe_dashboard_quality_test', SITE / 'main.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


def summary(days=(), deliveries=(), payments=(), commissions=()):
    return {'days': set(days), 'deliveries': set(deliveries), 'payments': set(payments),
            'payment_items': {d:{('1','sku'):1} for d in payments},
            'commission_items': {d:{('1','sku'):1} for d in commissions}}


def test_trailing_day_is_not_a_confirmed_zero_even_without_orders():
    ready, reason = quality.financial_evidence('2026-10-12', summary(), summary(['2026-10-11']))
    assert not ready
    assert '12.10.2026' in reason and '11.10.2026' in reason


def test_all_metrics_have_plain_language_explanations():
    assert set(SPECS) == set(dashboard.METRIC_SOURCES)
    for description, _ in SPECS.values():
        assert len(description) > 40
        assert not any(word in description.lower() for word in ('api', 'united-', 'payment_', 'sku', 'бэкенд'))


def test_missing_detail_is_not_reported_as_zero():
    detail = build_details({'buyout_count': 16})['buyout_count']
    assert not detail['available']
    assert all(row['value'] is None for row in detail['rows'])
    assert detail['note']


def test_breakdown_arithmetic_for_every_calculated_metric():
    from app.yandex.financial_reports import _pnl_values, _apply_return_commission
    values = _pnl_values({
        'payment_transfer': [{'merchantPrice': 1000, 'quantity': 3, 'servicePrice': 10}],
        'export_from_warehouse': [{'serviceName':'Доставка грузоместа до склада назначения','servicePrice':20}, {'serviceName':'Возврат товара заказчику','servicePrice':15}],
        'loyalty_and_reviews': [{'customerBonusAmount':5}],
        'placement': [{'lateOrderExecutionFeeTariff':7, 'qualityIndexAmount':11}],
        'shelf': [{'payment':2}],
        'boost': [{'postpaid':3}],
    }, {'returns':[{'count':1,'sumBillingPriceOfItems':100,'refundBuyerPaymentAmount':-50}]})
    _apply_return_commission(values, 4)
    values.update(detail_orders_remaining=100, detail_orders_cancelled=20, orders_turnover=120,
                  sold_cost_of_goods=60, returned_cost_of_goods=20)
    values['marginal_profit'] = values['bank_receipt'] - 60
    values['marginal_profit_percent'] = round(values['marginal_profit']/values['buyout_buyer_turnover']*100,2)
    details = build_details(values)
    for key, detail in details.items():
        if key == 'storage':
            assert not detail['rows']
            continue
        assert detail['available'], key
        rows = detail['rows']
        if key == 'marginal_profit_percent':
            calculated = rows[0]['value']/rows[1]['value']*rows[2]['value']
        else:
            calculated = sum((-1 if row['sign']=='−' else 1)*row['value'] for row in rows)
        assert round(calculated,2) == round(values[key],2), key


def test_interior_zero_day_is_valid():
    assert quality.financial_evidence('2026-10-06', summary(), summary(['2026-10-05', '2026-10-11']))[0]


def test_orders_without_payment_operations_stay_incomplete():
    assert not quality.financial_evidence('2026-10-12', summary(deliveries=['2026-10-12']), summary(['2026-10-12']))[0]


def test_services_arrive_and_day_becomes_complete():
    assert quality.financial_evidence('2026-10-12', summary(deliveries=['2026-10-12']), summary(['2026-10-12'], payments=['2026-10-12'], commissions=['2026-10-12']))[0]


def test_missing_or_partial_commissions_do_not_turn_finances_green():
    day = '2026-10-12'
    services = summary([day], payments=[day])
    assert not quality.financial_evidence(day, summary(), services)[0]
    services['commission_items'] = {day:{('1','sku'):1}}
    services['payment_items'][day][('2','other')] = 1
    assert not quality.financial_evidence(day, summary(), services)[0]
    services['commission_items'][day][('2','other')] = 1
    assert quality.financial_evidence(day, summary(), services)[0]


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
    add('united-services', '2026-10-13T09:00:00+00:00', 'payment_transfer', {'orderId':1,'shopSku':'sku','serviceDateTime':'2026-10-12 10:00:00'})
    add('united-services', '2026-10-13T09:00:00+00:00', 'placement', {'orderId':1,'shopSku':'sku','serviceDateTime':'2026-10-12 10:00:00','amountWithoutBonuses':0})
    add('united-netting', '2026-10-13T09:00:00+00:00', 'transaction_date', {'transactionDate':'12.10.2026 10:00:00'})
    row = {'store_slug':'tris','business_id':1,'report_date':'2026-10-12','calculated_at':'2026-10-13T07:00:00+00:00'}
    assert quality.build_quality(db, [row])[0][5] == 0
    row['calculated_at'] = '2026-10-13T10:00:00+00:00'
    assert quality.build_quality(db, [row])[0][5] == 1
    db.execute("DELETE FROM yandex_financial_report_rows WHERE sheet='placement'")
    assert quality.build_quality(db, [row])[0][5] == 0
    add('united-services', '2026-10-13T09:00:00+00:00', 'placement', {'orderId':1,'shopSku':'sku','serviceDateTime':'2026-10-12 10:00:00','amountWithoutBonuses':None})
    assert quality.build_quality(db, [row])[0][5] == 0
    db.close()


@pytest.mark.parametrize('money_rows', [[], [{'transactionDate':'10.10.2026'}], [{'transactionDate':'12.10.2026','transactionSource':'Оплата'}]])
def test_received_netting_without_refunds_is_not_incomplete(money_rows):
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE yandex_financial_report_rows(store_slug,business_id,report_kind,period_from,period_to,imported_at,sheet,payload_json)')
    def add(kind, sheet, value, start='2026-10-05', end='2026-10-12', imported='2026-10-13T06:00:00+00:00'):
        db.execute('INSERT INTO yandex_financial_report_rows VALUES(?,?,?,?,?,?,?,?)',
                   ('tris',1,kind,start,end,imported,sheet,json.dumps(value)))
    add('united-orders', 'orders_and_offers_transactions', {'deliveryDate':'12.10.2026'})
    add('united-services', 'payment_transfer', {'serviceDateTime':'12.10.2026','orderId':1,'shopSku':'sku','count':1})
    add('united-services', 'placement', {'serviceDateTime':'12.10.2026','orderId':1,'shopSku':'sku','count':1,'amountWithoutBonuses':10})
    row = {'store_slug':'tris','business_id':1,'report_date':'2026-10-12','calculated_at':'2026-10-13T07:00:00+00:00'}
    # Missing is not the same as a successfully downloaded empty report.
    assert quality.build_quality(db, [row])[0][5] == 0
    add('united-netting', '__coverage__', {})
    for operation in money_rows:
        add('united-netting', 'transaction_date', operation)
    assert quality.build_quality(db, [row])[0][5] == 1
    # Coverage must include the day and predate the financial calculation.
    db.execute("UPDATE yandex_financial_report_rows SET period_to='2026-10-11' WHERE report_kind='united-netting'")
    assert quality.build_quality(db, [row])[0][5] == 0
    db.execute("UPDATE yandex_financial_report_rows SET period_to='2026-10-12',imported_at='2026-10-13T08:00:00+00:00' WHERE report_kind='united-netting'")
    assert quality.build_quality(db, [row])[0][5] == 0
    db.execute("UPDATE yandex_financial_report_rows SET imported_at='2026-10-13T06:00:00+00:00' WHERE report_kind='united-netting'")
    db.execute("DELETE FROM yandex_financial_report_rows WHERE sheet='placement'")
    assert quality.build_quality(db, [row])[0][5] == 0
    db.close()


def test_auto_update_help_is_inside_toolbar_not_above_table():
    from html.parser import HTMLParser
    class Structure(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.parents = [], {}
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if values.get('id'):
                self.parents[values['id']] = list(self.stack)
            if tag not in {'meta','link','input','br','hr','img'}:
                self.stack.append((tag, values.get('id'), values.get('class')))
        def handle_endtag(self, tag):
            assert self.stack[-1][0] == tag
            self.stack.pop()
    parser = Structure()
    parser.feed((SITE / 'templates' / 'index.html').read_text(encoding='utf-8'))
    assert not parser.stack
    assert ('div',None,'filters') in parser.parents['sync-tools']
    for key in ('sync-info','sync-guide','sync-queue'):
        assert ('details','sync-tools','sync-tools') in parser.parents[key]
    assert ('details','sync-tools','sync-tools') not in parser.parents['error']


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


def test_detail_totals_do_not_hide_missing_days_or_stores(site):
    with site.connection() as db:
        for store, day, values in [
            ('tris', '2026-10-10', {'detail_redeemed_count':20, 'detail_returned_count':4, 'buyout_count':16}),
            ('tris', '2026-10-11', {'buyout_count':2}),
            ('rimili', '2026-10-10', {'buyout_count':3}),
        ]:
            db.execute('INSERT INTO daily_financial VALUES(?,?,?,?,?)', (store,1,day,json.dumps(values),'v1'))
    one = site.sum_daily('2026-10-10','2026-10-10','tris')
    assert build_details(one)['buyout_count']['available']
    assert one['detail_redeemed_count'] == 20
    for end, store in [('2026-10-11','tris'), ('2026-10-10',None)]:
        combined = site.sum_daily('2026-10-10',end,store)
        assert combined['detail_redeemed_count'] is None
        assert not build_details(combined)['buyout_count']['available']


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
    monkeypatch.setattr(costs, '_source_reports', lambda source, stores=None: {
        ('tris', 1): {costs.ORDER_KIND: {}, costs.SERVICE_KIND: {}}
    })
    monkeypatch.setattr(costs.yandex_financial, 'buyer_refund_events', lambda *args, **kwargs: [])
    fact(site, complete=complete)
    before = site.report_completeness(date(2026,10,12), date(2026,10,12), None)
    costs.recalculate(tmp_path / 'source.db')
    after = site.report_completeness(date(2026,10,12), date(2026,10,12), None)
    assert after == before
    with site.connection() as db:
        assert db.execute('SELECT calculated_at FROM daily_financial').fetchone()[0] == 'v1'


def test_cost_archive_streams_only_requested_stores():
    spec = importlib.util.spec_from_file_location('rbe_cost_stream_test', SITE / 'scripts' / 'recalculate_cost_of_goods.py')
    costs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(costs)
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE yandex_financial_report_rows (store_slug,business_id,report_kind,sheet,payload_json,period_to,imported_at)')
    for store in ('tris', 'rimili'):
        for version in ('old', 'new'):
            db.execute('INSERT INTO yandex_financial_report_rows VALUES (?,?,?,?,?,?,?)',
                       (store, 1, 'united-services', 'payment_transfer', '{"count":1}', '2026-10-12', version))

    class StreamingOnly:
        def execute(self, *args):
            # A generator has no fetchall: regressions must not buffer raw rows.
            return iter(row for row in db.execute(*args))

    reports = costs._source_reports(StreamingOnly(), ('tris',))
    assert set(reports) == {('tris', 1)}
    assert reports[('tris', 1)]['united-services']['payment_transfer'] == [{'count': 1}]
    db.close()
