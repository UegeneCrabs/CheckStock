import importlib.util
import json
import sqlite3
import sys
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

SITE=Path(__file__).resolve().parents[1]/'rbe-yandex-site'
sys.path.insert(0,str(SITE))
import rbe2_calculation as calculation
import rbe2_service as service

START, END = date(2026,10,12), date(2026,10,18)


def order(**kwargs):
    return {'orderId':1,'shopSku':'sku','campaign_id':100,'model':'FBY',
        'creationDate':'01.10.2026','deliveryDate':'12.10.2026','statusChanged':'2026-10-16 10:00:00',
        'offerStatus':'Доставлен покупателю','billingPrice':1000,'partnerPriceForDelivery':1000,
        'buyerPaymentAmount':500,'deliveredOrReturned':1,'transferredForDelivery':1,**kwargs}


def returned(**kwargs):
    return {'id':10,'orderId':1,'campaign_id':100,'returnType':'RETURN',
        'refundStatus':'REFUNDED','shipmentStatus':'FULFILMENT_RECEIVED',
        'creationDate':'2026-10-11T12:00:00+03:00','updateDate':'2026-10-16T12:00:00+03:00',
        'items':[{'shopSku':'sku','count':1}],**kwargs}


def snapshot(*,orders=None,returns=None,store='tris',business=1,stamp='v1',**data):
    return {'store_slug':store,'business_id':business,'date_from':'2026-10-12','date_to':'2026-10-18',
        'fetched_at':stamp,'data':{'orders':[order()] if orders is None else orders,
        'margins':[{'orderId':1,'summaryCommission':160,'saleCommission':100,'buyerDelivery':20,'boost':10}],
        'stats':[],'returns_by_day':returns or {},'campaigns':[{'id':100,'placementType':'FBY'}],**data}}


def report(snapshots,accounts=None,price=100,**kwargs):
    return calculation.calculate(snapshots,accounts or [{'store_slug':'tris','business_id':1}],
        kwargs.pop('start',START),kwargs.pop('end',END),lambda s,d:{} if price is None else {'sku':price},**kwargs)


def test_delivery_day_not_creation_update_or_bank_date():
    row=order(creationDate='12.10.2026',deliveryDate='16.10.2026',statusChanged='2026-10-18 12:00',buyerPaymentBankOrderDate='19.10.2026')
    assert report([snapshot(orders=[row])],start=START,end=START)['values']['detail_redeemed_count']==0
    assert report([snapshot(orders=[row])],start=date(2026,10,16),end=date(2026,10,16))['values']['detail_redeemed_count']==1


@pytest.mark.parametrize('status',['Отгружен','В пути','В пункте выдачи','Невыкуп отправлен'])
def test_positive_counter_does_not_make_a_dispatch_a_buyout(status):
    result=report([snapshot(orders=[order(offerStatus=status,deliveryDate=None)])])
    assert result['values']['detail_redeemed_count']==0


def test_returned_item_remains_in_original_gross_deliveries():
    result=report([snapshot(orders=[order(offerStatus='Возврат принят на складе')],
        returns={'2026-10-16':[returned()]})])
    assert result['values']['detail_redeemed_count']==1
    assert result['values']['detail_returned_count']==1
    assert result['values']['buyout_seller_turnover']==0


def test_return_uses_api_day_not_creation_update_or_first_financial_day():
    result=report([snapshot(returns={'2026-10-12':[returned()]})],start=START,end=START)
    assert result['events']['returns'][0]['date']=='2026-10-12'
    assert report([snapshot(returns={'2026-10-12':[returned()]})],start=date(2026,10,16),end=date(2026,10,16))['values']['detail_returned_count']==0


def test_duplicate_pages_and_days_count_one_application_once():
    result=report([snapshot(returns={'2026-10-12':[returned(),returned()],'2026-10-16':[returned()]})])
    assert result['values']['detail_returned_count']==1
    assert any('нескольких дней' in n for n in result['notes'])


def test_distinct_applications_over_original_quantity_are_unavailable_not_double_counted():
    result=report([snapshot(returns={'2026-10-12':[returned(),returned(id=11)]})])
    assert result['values']['detail_returned_count'] is None
    assert result['values']['buyout_count'] is None
    assert not result['quality']['detail_returned_count']['complete']


def test_new_cancellation_removes_older_membership_without_moving_financial_data():
    first=snapshot(returns={'2026-10-12':[returned()]})
    later=snapshot(stamp='v2',returns={'2026-10-16':[returned(refundStatus='CANCELLED')]})
    result=report([first,later])
    assert result['values']['detail_returned_count']==0


@pytest.mark.parametrize('row',[returned(returnType='UNREDEEMED'),returned(refundStatus='CANCELLED'),returned(shipmentStatus='EXPIRED')])
def test_non_purchase_and_cancelled_application_are_not_returns(row):
    assert report([snapshot(returns={'2026-10-12':[row]})])['values']['detail_returned_count']==0


def test_same_identifiers_in_other_store_or_business_do_not_collide():
    result=report([snapshot(),snapshot(store='rimili'),snapshot(business=2)],accounts=[
        {'store_slug':'tris','business_id':1},{'store_slug':'rimili','business_id':1},
        {'store_slug':'tris','business_id':2}])
    assert result['values']['detail_redeemed_count']==3
    assert result['values']['detail_seller_sales']==3000


def test_cost_uses_exact_store_and_recognition_day_no_general_price():
    calls=[]
    def price(store,day):
        calls.append((store,day))
        return {'sku':100 if day=='2026-10-12' else 70}
    result=calculation.calculate([snapshot(returns={'2026-10-16':[returned()]})],
        [{'store_slug':'tris','business_id':1}],START,END,price)
    assert result['values']['sold_cost_of_goods']==30
    assert calls==[('tris','2026-10-12'),('tris','2026-10-16')]
    assert report([snapshot()],price=None)['values']['sold_cost_of_goods'] is None


def test_paid_fees_are_not_subtracted_from_gross_turnover_to_fake_profit():
    result=report([snapshot()])
    assert result['order_report_expenses']['order_expenses']==160
    assert sum(result['order_report_expenses'][k] for k in ('commission','logistics','advertising','other_direct_expenses'))==160
    assert result['values']['order_expenses'] is None
    assert result['values']['bank_receipt'] is None
    assert result['values']['marginal_profit'] is None
    assert not result['quality']['marginal_profit']['complete']
    assert not result['quality']['commission']['complete']


def test_partial_cancellation_counts_original_creation_cohort_not_returns():
    row=order(creationDate='12.10.2026')
    stats=[{'id':1,'items':[{'shopSku':'sku','count':1,'initialCount':2}]}]
    values=report([snapshot(orders=[row],stats=stats)])['values']
    assert values['orders_turnover']==2000
    assert values['cancelled_count']==1
    assert values['cancelled_turnover']==1000
    assert values['detail_returned_count']==0


def test_empty_success_is_zero_missing_snapshot_is_not_zero():
    assert report([snapshot(orders=[],margins=[])])['values']['detail_redeemed_count']==0
    result=report([])
    assert result['values']['detail_redeemed_count'] is None
    assert len(result['missing_days'])==7


def test_disabled_campaign_never_makes_the_whole_cabinet_complete():
    result=report([snapshot(complete=False)])
    assert not result['quality']['detail_redeemed_count']['complete']


def test_one_order_expense_is_additive_across_separate_delivery_days():
    rows=[order(),order(shopSku='other',deliveryDate='13.10.2026')]
    data=snapshot(orders=rows)
    first=report([data],start=START,end=START)['order_report_expenses']['order_expenses']
    second=report([data],start=date(2026,10,13),end=date(2026,10,13))['order_report_expenses']['order_expenses']
    assert first==160 and second==0
    assert report([data])['order_report_expenses']['order_expenses']==first+second


def test_missing_delivered_quantity_is_not_a_confirmed_zero():
    result=report([snapshot(orders=[order(deliveredOrReturned=None)])])
    assert result['values']['detail_redeemed_count'] is None
    assert result['values']['detail_seller_sales'] is None
    assert not result['quality']['detail_redeemed_count']['complete']


def test_missing_buyer_price_does_not_make_partial_turnover_green():
    result=report([snapshot(orders=[order(buyerPaymentAmount=None)])])
    assert result['values']['detail_buyer_sales'] is None
    assert result['values']['buyout_buyer_turnover'] is None
    assert result['values']['detail_redeemed_count']==1


def test_unknown_campaign_cannot_make_a_filtered_zero_green():
    result=report([snapshot(orders=[order(campaign_id=None)])],campaign_id=100)
    assert not result['quality']['detail_redeemed_count']['complete']
    assert any('не определена кампания' in note for note in result['notes'])


def test_incompatible_daily_and_weekly_returns_are_not_saved():
    def request(path,key,**kwargs):
        if path.endswith('/stats/orders'):
            return {'orders':[]}
        params=kwargs['params']
        return {'returns':[returned()]} if params['fromDate']==params['toDate']=='2026-10-12' else {'returns':[]}
    api=SimpleNamespace(get_campaigns=lambda key:[{'id':100,'business':{'id':1},'placementType':'FBY'}],request=request)
    with pytest.raises(ValueError,match='разные списки'):
        service.fetch_packet({'business_id':1,'api_key':'secret'},START,END,api=api,
            report_loader=lambda *args:{'orders_and_offers_transactions':[]})


def test_fake_orders_are_not_added_from_the_operational_report():
    def request(path,key,**kwargs):
        return {'orders':[{'id':1,'fake':True}]} if path.endswith('/stats/orders') else {'returns':[]}
    api=SimpleNamespace(get_campaigns=lambda key:[{'id':100,'business':{'id':1},'placementType':'FBY'}],request=request)
    packet=service.fetch_packet({'business_id':1,'api_key':'secret'},START,END,api=api,
            report_loader=lambda *args:{'orders_and_offers_transactions':[order()]},expense_loader=lambda *args:{})
    assert packet['orders']==[] and packet['stats']==[]


def test_pages_reject_missing_array_and_repeated_token():
    fake=SimpleNamespace(request=lambda *a,**k:{})
    with pytest.raises(ValueError,match='не передал'):
        service.pages(fake,'/returns','secret','returns')
    fake.request=lambda *a,**k:{'returns':[],'paging':{'nextPageToken':'same'}}
    with pytest.raises(ValueError,match='повторил'):
        service.pages(fake,'/returns','secret','returns')


def test_operational_basket_loader_never_requests_netting_or_legacy_financial_facts():
    calls=[]
    def request(path,key,**kwargs):
        calls.append((path,kwargs))
        if path.endswith('/stats/orders'):
            assert 'updateFrom' in kwargs['payload']
            return {'orders':[]}
        assert path.endswith('/returns')
        return {'returns':[]}
    api=SimpleNamespace(get_campaigns=lambda key:[{'id':100,'business':{'id':1},'placementType':'FBY'}],request=request)
    account={'business_id':1,'api_key':'secret'}
    packet=service.fetch_packet(account,START,END,api=api,
        report_loader=lambda *args:{'orders_and_offers_transactions':[],'services_and_orders_margin':[]},expense_loader=lambda *args:{})
    assert packet['complete']
    assert len(calls)==9
    assert [kw['params']['fromDate'] for path,kw in calls if path.endswith('/returns')][:-1]==list(reversed(calculation.date_range(START,END)))
    assert not any('united-services' in path or 'netting' in path for path,_ in calls)


@pytest.fixture
def dashboard(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('rbe2_dashboard_test',SITE/'main.py')
    site=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(site)
    monkeypatch.setattr(site,'DATABASE_PATH',tmp_path/'site.db')
    monkeypatch.setattr(site,'load_connections',lambda:[{'store_slug':'tris','business_id':1,'api_key':'secret'}])
    monkeypatch.setattr(site,'_require_user',lambda r:{'role':'viewer'})
    monkeypatch.setattr(site,'_require_admin',lambda r:{'role':'admin'})
    monkeypatch.setattr(site,'SYNC_LOCK',threading.Lock())
    site.init_database()
    return site


def endpoint(site,path):
    return next(r.endpoint for r in site.app.routes if r.path==path)


def test_routes_read_separate_tables_and_do_not_leak_keys_or_start_sync(dashboard):
    with dashboard.connection() as conn:
        conn.execute("INSERT INTO daily_financial VALUES('tris',1,'2026-10-12','{\"buyout_count\":999}', 'old')")
        service.save_snapshot(conn,{'store_slug':'tris','business_id':1},START,END,snapshot()['data'])
    result=endpoint(dashboard,'/api/rbe2/report')(None,START,END,'tris',None)
    assert result['values']['buyout_count']==1
    assert not result['can_sync']
    assert not result['automatic']
    assert 'secret' not in json.dumps(result)
    with dashboard.connection() as conn:
        assert json.loads(conn.execute('SELECT values_json FROM daily_financial').fetchone()[0])['buyout_count']==999
        assert conn.execute('SELECT COUNT(*) FROM sync_runs').fetchone()[0]==0


def test_sync_shares_lock_but_not_financial_runs_and_blocks_old_dates(dashboard):
    from rbe2_routes import Rbe2SyncRequest
    sync=endpoint(dashboard,'/api/rbe2/sync')
    with pytest.raises(Exception) as caught:
        sync(None,Rbe2SyncRequest(date_from=date(2020,1,1),date_to=date(2020,1,1)))
    assert caught.value.status_code==422
    dashboard.SYNC_LOCK.acquire()
    today=calculation.datetime.now(dashboard.MOSCOW).date()
    with pytest.raises(Exception) as caught:
        sync(None,Rbe2SyncRequest(date_from=today,date_to=today))
    assert caught.value.status_code==409
    dashboard.SYNC_LOCK.release()
