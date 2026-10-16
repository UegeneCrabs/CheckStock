import json
import sqlite3
from datetime import date

import pytest

from app.repositories import yandex_financial as financial
from app.repositories import yandex_refund_recognitions as ledger
from app.yandex import api
from app.yandex.buyer_refunds import build_refunds


def order(**kw):
    return {'orderId': 1, 'shopSku': 'sku', 'offerStatus': 'Возврат принят на складе',
            'deliveryDate': '01.10.2026', 'billingPrice': 1000, 'buyerPaymentAmount': 500,
            'deliveredOrReturned': 1, **kw}


def payment(**kw):
    return {'orderId': 1, 'shopSku': 'sku', 'transactionDate': '12.10.2026 10:00',
            'transactionType': 'Возврат', 'transactionSource': 'Возврат баллов за скидку Маркета',
            'transactionSum': -500, 'count': 1, **kw}


def events(payments=None, **kw):
    return build_refunds([order(**kw)], payments or [payment()], '0001-01-01', '9999-12-31')


def saved_units(events):
    return [{'order_id': key[0], 'shop_sku': key[1], 'unit_index': key[2],
             'payload_json': json.dumps(e), 'campaign_id': None, 'return_id': None,
             'cancelled': 0} for key, e in ledger.split_units(events).items()]


def application(id=10, created='2026-10-11T12:00:00+03:00', status='REFUND_IN_PROGRESS', count=1, **kw):
    return {'id': id, 'campaign_id': 100, 'orderId': 1, 'returnType': 'RETURN',
            'creationDate': created, 'refundStatus': status, 'shipmentStatus': 'IN_TRANSIT',
            'items': [{'shopSku': 'sku', 'count': count}], **kw}


def test_october_12_points_and_october_16_money_remain_on_october_12():
    first = events()
    saved = saved_units(first)
    later = events([payment(), payment(transactionSource='Возврат платежа покупателя',
                                      transactionDate='16.10.2026 10:00', transactionSum=-450)])
    result = ledger.visible(later, saved, '2026-10-12', '2026-10-12')
    assert len(result) == 1 and result[0]['count'] == 1
    assert result[0]['refund_day'] == '2026-10-12'
    assert result[0]['refund_money_confirmed']
    assert result[0]['refundBuyerPaymentAmount'] == -450
    assert ledger.visible(later, saved, '2026-10-16', '2026-10-16') == []


def test_saved_date_survives_corrected_date_and_empty_snapshot():
    saved = saved_units(events())
    corrected = events([payment(transactionDate='16.10.2026 10:00')])
    assert ledger.visible(corrected, saved, '2026-10-12', '2026-10-12')[0]['refund_day'] == '2026-10-12'
    assert ledger.visible([], saved, '2026-10-12', '2026-10-12')[0]['count'] == 1


def test_money_first_is_not_moved_to_later_points():
    ee = events([payment(transactionSource='Возврат платежа покупателя'),
                 payment(transactionDate='16.10.2026 10:00')])
    assert len(ee) == 1 and ee[0]['refund_day'] == '2026-10-12'


def test_cancellation_removes_only_its_units_with_same_order_and_sku():
    ee = events([payment(), payment(transactionDate='14.10.2026 10:00')],
                deliveredOrReturned=2, buyerPaymentAmount=1000)
    saved = saved_units(ee)
    units = ledger.merge(ee, saved)
    returns = [application(status='CANCELLED'), application(id=11, created='2026-10-13T12:00:00+03:00')]
    links = ledger.cancellation_links(units, saved, returns)
    for row in saved:
        key = (row['order_id'], row['shop_sku'], row['unit_index'])
        row['campaign_id'], row['return_id'], row['cancelled'] = links[key]
    assert ledger.visible(ee, saved, '2026-10-12', '2026-10-12') == []
    remaining = ledger.visible(ee, saved, '2026-10-14', '2026-10-14')
    assert len(remaining) == 1 and remaining[0]['count'] == 1


def test_known_return_id_is_not_reassigned_to_another_application():
    saved = saved_units(events())
    saved[0].update(campaign_id=100, return_id=10)
    links = ledger.cancellation_links(ledger.merge(events(), saved), saved, [
        application(status='CANCELLED'), application(id=11, created='2026-10-12T08:00:00+03:00')])
    assert links[('1', 'sku', 0)] == (100, 10, True)


def test_ambiguous_same_day_returns_are_not_silently_cancelled():
    with pytest.raises(ValueError, match='однозначно'):
        ledger.cancellation_links(ledger.split_units(events()), [], [
            application(status='CANCELLED'), application(id=11)])


def test_newer_cancelled_application_cannot_hide_an_older_valid_return():
    with pytest.raises(ValueError, match='однозначно'):
        ledger.cancellation_links(ledger.split_units(events()), [], [
            application(created='2026-10-10T12:00:00+03:00'),
            application(id=11, created='2026-10-11T12:00:00+03:00', status='CANCELLED')])


def test_application_created_after_first_financial_operation_is_not_its_cancellation():
    assert ledger.cancellation_links(ledger.split_units(events()), [], [
        application(created='2026-10-12T12:00:00+03:00', status='CANCELLED')]) == {}


def test_new_return_after_cancelled_operation_disappears_requires_matching():
    saved = saved_units(events())
    saved[0].update(campaign_id=100, return_id=10, cancelled=1)
    incoming = ledger.split_units(events([payment(transactionDate='16.10.2026 10:00')]))
    with pytest.raises(ValueError, match='новый возврат после отмены'):
        ledger.cancellation_links(ledger.merge(events(), saved), saved, [
            application(status='CANCELLED'), application(id=11, created='2026-10-15T12:00:00+03:00')],
            incoming=incoming)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    path = tmp_path / 'refunds.db'
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    with connect() as conn:
        conn.execute('CREATE TABLE yandex_financial_report_rows (store_slug,business_id,report_kind,sheet,payload_json,period_from,period_to,imported_at)')
        for kind, sheet, payload in [('united-orders', 'orders_and_offers_transactions', order()),
                                      ('united-netting', 'transaction_date', payment())]:
            conn.execute('INSERT INTO yandex_financial_report_rows VALUES (?,?,?,?,?,?,?,?)',
                         ('tris', 1, kind, sheet, json.dumps(payload), '2026-10-12', '2026-10-12', 'v1'))
        conn.commit()
    monkeypatch.setattr(ledger, 'get_connection', connect)
    monkeypatch.setattr(financial, 'get_connection', connect)
    return connect


def test_persistent_seed_survives_report_replacement_and_confirmed_cancellation(archive, monkeypatch):
    ledger.seed('tris', 1, 'v1')
    with archive() as conn:
        conn.execute("DELETE FROM yandex_financial_report_rows WHERE report_kind='united-netting'")
        conn.execute('INSERT INTO yandex_financial_report_rows VALUES (?,?,?,?,?,?,?,?)',
                     ('tris', 1, 'united-netting', '__coverage__', '{}', '2026-10-12', '2026-10-12', 'v2'))
        conn.commit()
    assert financial.buyer_refund_events('tris', 1, '2026-10-12', '2026-10-12')[0]['count'] == 1
    monkeypatch.setattr(api, 'get_campaigns', lambda key: [{'id': 100, 'business': {'id': 1}},
                                                          {'id': 999, 'business': {'id': 2}}])
    calls = []
    def returns(key, campaign, orders):
        calls.append((campaign, orders))
        return [application(status='CANCELLED')]
    monkeypatch.setattr(api, 'get_returns', returns)
    for _ in range(2):
        ledger.sync('tris', 1, 'secret', [], 'v3', current_day=date(2026, 10, 16))
        assert financial.buyer_refund_events('tris', 1, '2026-10-12', '2026-10-12') == []
    assert calls == [(100, [1]), (100, [1])]
    with archive() as conn:
        rows = ledger.read(conn, 'tris', 1)
        assert len(rows) == 1 and rows[0]['return_id'] == 10 and rows[0]['cancelled'] == 1
        assert json.loads(rows[0]['payload_json'])['refund_day'] == '2026-10-12'
        assert ledger.read(conn, 'other', 1) == ledger.read(conn, 'tris', 2) == []


def test_api_failure_keeps_existing_date_and_count(archive, monkeypatch):
    ledger.seed('tris', 1, 'v1')
    monkeypatch.setattr(api, 'get_campaigns', lambda key: [{'id': 100, 'business': {'id': 1}}])
    def fail(*args): raise api.YandexApiError(503, 'test')
    monkeypatch.setattr(api, 'get_returns', fail)
    with pytest.raises(api.YandexApiError):
        ledger.sync('tris', 1, 'secret', [], 'v2', current_day=date(2026, 10, 16))
    assert financial.buyer_refund_events('tris', 1, '2026-10-12', '2026-10-12')[0]['count'] == 1


def test_old_refunds_are_preserved_without_old_api_requests(archive, monkeypatch):
    ledger.seed('tris', 1, 'v1')
    def fail(*args): raise AssertionError('Old API data must not be requested')
    monkeypatch.setattr(api, 'get_campaigns', fail)
    ledger.sync('tris', 1, 'secret', [], 'v2', current_day=date(2027, 2, 1))
    assert financial.buyer_refund_events('tris', 1, '2026-10-12', '2026-10-12')[0]['count'] == 1


def test_returns_api_paginates_without_excluding_cancelled_applications(monkeypatch):
    calls = []
    def request(path, key, **kw):
        calls.append((path, dict(kw['params']), kw['method']))
        if len(calls) == 1:
            return {'returns': [application()], 'paging': {'nextPageToken': 'next'}}
        return {'returns': [application(id=11, status='CANCELLED')], 'paging': {}}
    monkeypatch.setattr(api, '_request', request)
    result = api.get_returns('secret', 100, [1])
    assert len(result) == 2
    assert calls[0] == ('/v2/campaigns/100/returns', {'orderIds': '1', 'type': 'RETURN', 'limit': 100}, 'GET')
    assert calls[1][1]['pageToken'] == 'next'


def test_returns_api_missing_or_repeated_page_fails_closed(monkeypatch):
    monkeypatch.setattr(api, '_request', lambda *a, **kw: {})
    with pytest.raises(api.YandexApiError, match='не передал'):
        api.get_returns('secret', 100, [1])
    monkeypatch.setattr(api, '_request', lambda *a, **kw:
                        {'returns': [application()], 'paging': {'nextPageToken': 'same'}})
    with pytest.raises(api.YandexApiError, match='повторил страницу'):
        api.get_returns('secret', 100, [1])


def test_full_import_preserves_first_day_and_rebuilds_it_after_cancellation(archive, monkeypatch):
    from app.yandex import financial_reports as reports
    with archive() as conn:
        conn.execute("ALTER TABLE yandex_financial_report_rows ADD COLUMN row_key TEXT DEFAULT ''")
        for table, dates in [('yandex_financial_pnl', 'period_from TEXT,period_to TEXT'),
                              ('yandex_financial_daily_pnl', 'report_date TEXT')]:
            keys = 'period_from,period_to' if table.endswith('_pnl') and 'daily' not in table else 'report_date'
            conn.execute(f'CREATE TABLE {table} (store_slug TEXT,business_id INTEGER,{dates},'
                         f'values_json TEXT,calculated_at TEXT,PRIMARY KEY(store_slug,business_id,{keys}))')
        conn.commit()
    monkeypatch.setattr(reports.tokens, 'get_accounts', lambda store:
                        [{'business_id': 1, 'api_key': 'secret', 'campaign_ids': [100]}])
    monkeypatch.setattr(reports.sales_sync, 'sync_store_period', lambda *args: {'ok': True})
    monkeypatch.setattr(reports, '_load_united_orders_report', lambda *args:
                        {'orders_and_offers_transactions': [order()]})
    monkeypatch.setattr(reports, '_load_united_services_report', lambda *args:
                        {'payment_transfer': [{'orderId': 1, 'shopSku': 'sku', 'count': 1,
                                               'merchantPrice': 1000, 'serviceDateTime': args[2]}]})
    monkeypatch.setattr(reports, '_load_united_netting_report', lambda *args:
                        {'transaction_date': [payment()] if args[2] == '2026-10-12' else [
                            payment(transactionDate='16.10.2026 10:00',
                                    transactionSource='Возврат платежа покупателя', transactionSum=-450)]})
    monkeypatch.setattr(reports.yandex_source_values, 'get_values', lambda *args:
                        {'sku': {'purchase_price': 10}})
    monkeypatch.setattr(financial, 'refresh_pnl_costs', lambda *args: {})
    monkeypatch.setattr(financial, 'buyer_refund_commission', lambda *args: 0)
    monkeypatch.setattr(reports, '_store_daily_netting', lambda *args: None)
    monkeypatch.setattr(api, 'get_campaigns', lambda *args: [{'id': 100, 'business': {'id': 1}}])
    status = ['REFUND_IN_PROGRESS']
    monkeypatch.setattr(api, 'get_returns', lambda *args: [application(status=status[0])])

    def fact(day):
        with archive() as conn:
            return json.loads(conn.execute('SELECT values_json FROM yandex_financial_daily_pnl '
                'WHERE store_slug=? AND business_id=? AND report_date=?', ('tris', 1, day)).fetchone()[0])
    reports.import_period('tris', date(2026, 10, 12), date(2026, 10, 12), orders_lookback_days=0)
    assert fact('2026-10-12')['returned_count'] == 1
    reports.import_period('tris', date(2026, 10, 16), date(2026, 10, 16), orders_lookback_days=0)
    assert fact('2026-10-12')['returned_count'] == 1
    assert fact('2026-10-12')['detail_buyer_returns'] == 450
    assert fact('2026-10-12')['returned_cost_of_goods'] == 10
    assert fact('2026-10-16')['returned_count'] == 0
    status[0] = 'CANCELLED'
    reports.import_period('tris', date(2026, 10, 16), date(2026, 10, 16), orders_lookback_days=0)
    assert fact('2026-10-12')['returned_count'] == 0
    assert fact('2026-10-12')['detail_seller_returns'] == 0
    assert fact('2026-10-12')['detail_buyer_returns'] == 0
    assert fact('2026-10-12')['returned_cost_of_goods'] == 0
