"""Synthetic fixtures: no live API, buyer information or production database."""
import copy
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'rbe-yandex-site'))
import rbe2_expenses as expenses
from test_rbe2 import report,snapshot,order,returned,START,END
from rbe2_calculation import date_range


def source(*,fee=500,sheets=None):
    return {'days':{d:{'source_at':'2026-10-19T08:00:00+00:00','sheets':sheets if d=='2026-10-12' and sheets else {}}
                    for d in date_range(START,END)},
        'placements':[{'orderId':1,'shopSku':'sku','count':1,'amountWithoutBonuses':fee,
                       'serviceDateTime':'2026-10-12 12:00:00','serviceName':'Размещение',
                       'source_at':'2026-10-19T08:00:00+00:00'}]}


def test_gross_fees_enable_profit_without_using_paid_order_margin():
    data=source(sheets={'delivery':[{'servicePrice':20}], 'boost':[{'postpaid':10,'servicePrice':1,'netting':9}],
                        'payment_transfer':[{'servicePrice':30}]})
    result=report([snapshot(expenses=data)])
    values=result['values']
    assert values['commission']==500  # not saleCommission=100 in the order report
    assert values['logistics']==20 and values['advertising']==10
    assert values['order_expenses']==560
    assert values['revenue']==480 and values['bank_receipt']==440
    assert values['marginal_profit']==340
    assert result['quality']['marginal_profit']['complete']


def test_missing_paid_order_margin_does_not_block_confirmed_gross_costs():
    result=report([snapshot(margins=[],expenses=source())])
    assert result['values']['marginal_profit']==400
    assert result['quality']['marginal_profit']['complete']
    assert result['order_report_missing_expenses']
    assert not any(n.startswith('Не хватает данных:') for n in result['notes'])


def test_unknown_fbs_campaign_does_not_invalidate_known_fbo_deliveries():
    rows=[order(),order(orderId=2,campaign_id=None,model='FBS')]
    result=report([snapshot(orders=rows)],campaign_id=100)
    assert result['values']['detail_redeemed_count']==1
    assert result['quality']['detail_redeemed_count']['complete']


def test_original_commission_reversed_once_not_again_via_negative_paid_amount():
    data=source(sheets={'placement':[{'totalAmount':-5,'amountWithoutBonuses':None}]})
    result=report([snapshot(expenses=data,returns={'2026-10-13':[returned()]})])
    assert result['values']['commission']==0
    assert result['expenses']['commission_sales']==500
    assert result['expenses']['commission_returns']==500
    assert result['values']['order_expenses']==0


def test_partial_return_uses_per_unit_original_fee_and_store_business_identity():
    data=source(fee=1000)
    data['placements'][0]['count']=2
    s=snapshot(orders=[order(deliveredOrReturned=2,buyerPaymentAmount=1000)],expenses=data,
               returns={'2026-10-13':[returned()]})
    other=snapshot(store='other',business=2,expenses=source(fee=50))
    result=report([s,other],accounts=[{'store_slug':'tris','business_id':1},{'store_slug':'other','business_id':2}])
    assert result['values']['commission']==550


def test_zero_fee_is_explicit_zero_not_missing():
    result=report([snapshot(expenses=source(fee=0))])
    assert result['values']['commission']==0
    assert result['values']['marginal_profit']==900


def test_missing_original_fee_and_failed_expense_fetch_never_fake_full_profit():
    s=source();s['placements']=[]
    result=report([snapshot(expenses=s)])
    assert result['values']['commission'] is None
    assert result['values']['marginal_profit'] is None
    assert result['expenses']['missing_original_fees'][0]['order_id']=='1'


def test_unknown_service_and_unknown_money_are_not_free():
    for sheets in ({'new_service':[{'servicePrice':10}]},{'boost':[{'postpaid':None,'servicePrice':1}]}):
        result=report([snapshot(expenses=source(sheets=sheets))])
        assert result['values']['marginal_profit'] is None


def test_logistics_restores_points_once_and_preserves_reversal_signs():
    result=report([snapshot(expenses=source(sheets={'crossregional_delivery':[
        {'servicePrice':1,'netting':99},{'servicePrice':-2,'netting':-8}]}))])
    assert result['values']['logistics']==90


def test_business_advertising_counted_once_not_per_shop_or_overlapping_snapshot():
    s=snapshot(expenses=source(sheets={'shelf':[{'payment':30}], 'product-banners':[{'payment':20}]}),
        campaigns=[{'id':100,'placementType':'FBY'},{'id':101,'placementType':'FBS'}])
    second=copy.deepcopy(s);second['fetched_at']='v2'
    assert report([s,second])['values']['advertising']==50
    selected=report([s,second],campaign_id=100)
    assert selected['values']['advertising'] is None
    assert sum(r['amount'] for r in selected['expenses']['unallocated'])==50
    assert selected['values']['marginal_profit'] is None


def test_newer_source_version_wins_not_later_copy_of_stale_cache():
    s=snapshot(expenses=source(sheets={'delivery':[{'servicePrice':20}]}))
    newer=copy.deepcopy(s);newer['fetched_at']='v2'
    newer['data']['expenses']['days']['2026-10-12']['source_at']='2026-10-20'
    newer['data']['expenses']['days']['2026-10-12']['sheets']['delivery'][0]['servicePrice']=40
    stale=copy.deepcopy(s);stale['fetched_at']='v3'
    assert report([s,newer,stale])['values']['logistics']==40


def test_pending_claim_is_visible_but_not_returned_goods_until_handover_or_refund():
    pending=returned(refundStatus='REFUND_IN_PROGRESS',shipmentStatus=None)
    s=snapshot(returns={'2026-10-18':[pending]})
    result=report([s])
    assert result['values']['detail_returned_count']==0
    assert result['pending_returns'][0]['return_id']==10
    pending['shipmentStatus']='IN_TRANSIT'
    assert report([s])['values']['detail_returned_count']==1
    pending['shipmentStatus']=None;pending['refundStatus']='REFUNDED'
    assert report([s])['values']['detail_returned_count']==1


def test_current_update_day_can_move_while_financial_rbe_is_not_used():
    old=snapshot(returns={'2026-10-18':[returned()]})
    new=copy.deepcopy(old);new['fetched_at']='v2'
    new['data']['returns_by_day']={'2026-10-18':[],'2026-10-19':[returned(updateDate='2026-10-19T11:25:00+03:00')]}
    assert report([old,new])['values']['detail_returned_count']==0
    assert report([old,new],start=date(2026,10,19),end=date(2026,10,19))['values']['detail_returned_count']==1


def test_empty_confirmed_service_report_is_zero_failed_does_not_use_stale_cache(tmp_path):
    account={'store_slug':'tris','business_id':1}
    packet=snapshot()['data']
    empty=expenses.fetch_expenses(account,START,END,packet,None,lambda *a:{})
    assert len(empty['days'])==7
    def failed(*args):raise ValueError('NO_DATA')
    missing=expenses.fetch_expenses(account,START,END,packet,None,failed)
    assert missing['days']=={} and 'NO_DATA' in missing['load_error']


def test_failed_refresh_invalidates_older_overlapping_expense_coverage():
    old=snapshot(expenses=source())
    latest=snapshot(stamp='v2',expenses={'days':{},'placements':[],
        'failed_at':'2026-10-20T00:00:00+00:00','load_error':'NO_DATA'})
    result=report([old,latest])
    assert result['values']['advertising'] is None
    assert len(result['expenses']['missing_days'])==7


def test_configured_subset_is_not_given_the_entire_cabinet_advertising():
    s=snapshot(expenses=source(sheets={'shelf':[{'payment':30}]}),
        campaigns=[{'id':100,'placementType':'FBY'}],
        business_campaigns=[{'id':100,'placementType':'FBY'},{'id':101,'placementType':'FBS'}])
    result=report([s],accounts=[{'store_slug':'tris','business_id':1,'campaign_ids':[100]}])
    assert result['values']['advertising'] is None
    assert sum(r['amount'] for r in result['expenses']['unallocated'])==30


def test_read_cache_is_read_only_selects_one_archive_and_never_imports_old_pnl(tmp_path):
    path=tmp_path/'source.db'
    db=sqlite3.connect(path)
    db.execute('''CREATE TABLE yandex_financial_report_rows
        (store_slug,business_id,report_kind,sheet,period_from,period_to,imported_at,payload_json)''')
    def put(sheet,period_from,period_to,stamp,row):
        db.execute('INSERT INTO yandex_financial_report_rows VALUES(?,?,?,?,?,?,?,?)',
            ('tris',1,'united-services',sheet,period_from,period_to,stamp,json.dumps(row)))
    put('delivery','2026-10-12','2026-10-18','v1',{'businessId':1,'serviceDateTime':'2026-10-12','servicePrice':10,'private':'NOT_FOR_SNAPSHOT'})
    put('delivery','2026-10-12','2026-10-12','v2',{'businessId':1,'serviceDateTime':'2026-10-12','servicePrice':20})
    put('placement','2026-10-12','2026-10-18','v1',{'orderId':1,'shopSku':'sku','count':1,'amountWithoutBonuses':0,'serviceDateTime':'2026-10-12'})
    db.commit();before=db.total_changes;stamp=path.stat().st_mtime_ns
    result=expenses.read_cached_expenses({'store_slug':'tris','business_id':1},START,END,snapshot()['data'],path)
    assert result['days']['2026-10-12']['sheets']['delivery']==[
        {'businessId':1,'serviceDateTime':'2026-10-12','servicePrice':20}]
    assert result['placements'][0]['amountWithoutBonuses']==0
    assert 'NOT_FOR_SNAPSHOT' not in json.dumps(result)
    assert db.total_changes==before and path.stat().st_mtime_ns==stamp
    db.close()
