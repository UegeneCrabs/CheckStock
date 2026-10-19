"""Operational-only loader and isolated storage for RBE 2.

No financial import, first-financial-date ledger, or legacy daily table writes.
Opening a report reads snapshots; API requests require an explicit admin action.
"""
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from rbe2_calculation import date_range

SCHEMA = """
CREATE TABLE IF NOT EXISTS rbe2_snapshots (
    store_slug TEXT NOT NULL, business_id INTEGER NOT NULL,
    date_from TEXT NOT NULL, date_to TEXT NOT NULL,
    fetched_at TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY(store_slug,business_id,date_from,date_to)
);
CREATE TABLE IF NOT EXISTS rbe2_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
    finished_at TEXT, status TEXT NOT NULL, detail TEXT NOT NULL
);
"""
ORDER_FIELDS = ('orderId','shopSku','model','partnerId','creationDate','deliveryDate',
                'statusChanged','offerStatus','billingPrice','partnerPriceForDelivery',
                'buyerPaymentAmount','deliveredOrReturned','transferredForDelivery')
MARGIN_FIELDS = ('orderId','summaryCommission','saleCommission','expropriationResale',
                 'buyerDelivery','crossregionalDelivery','buyerExpressDelivery',
                 'crossborderDelivery','orderIntake','boost')


def selected(row, fields):
    return {key:row[key] for key in fields if key in row}


def api_module():
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from app.yandex import api
    return api


def pages(api, path, key, field, *, payload=None, params=None, method='POST'):
    params = dict(params or {})
    params.setdefault('limit', 100)
    seen, rows = set(), []
    while True:
        result = api.request(path,key,payload=payload,params=params,method=method)
        chunk = result.get(field)
        if not isinstance(chunk,list):
            raise ValueError(f'Яндекс не передал список {field}; прежние данные сохранены')
        rows.extend(chunk)
        token = str((result.get('paging') or {}).get('nextPageToken') or '')
        if not token:
            return rows
        if token in seen:
            raise ValueError('Яндекс повторил страницу ответа; прежние данные сохранены')
        seen.add(token)
        params['pageToken'] = token


def report_rows(value):
    if isinstance(value,list):
        for row in value:
            yield from report_rows(row)
    elif isinstance(value,dict):
        if 'orderId' in value:
            yield value
        else:
            for child in value.values():
                if isinstance(child,(dict,list)):
                    yield from report_rows(child)


def orders_report(api, account, start, end, campaigns, sleep=time.sleep):
    result = api.request('/v2/reports/united-orders/generate',account['api_key'],
        payload={'businessId':int(account['business_id']),'dateFrom':start.isoformat(),
                 'dateTo':end.isoformat(),'campaignIds':[int(c['id']) for c in campaigns]},
        params={'format':'JSON','language':'RU'})
    report_id = result.get('reportId')
    if not report_id:
        raise ValueError('Яндекс не вернул номер операционного отчёта')
    for attempt in range(121):
        info = api.request(f'/v2/reports/info/{report_id}',account['api_key'],method='GET')
        if info.get('status') == 'FAILED' or info.get('subStatus') == 'NO_DATA':
            raise ValueError('Операционный отчёт ещё не готов или недоступен; это не подтверждённый ноль')
        if info.get('status') == 'DONE':
            url = str(info.get('file') or '')
            if urllib.parse.urlsplit(url).scheme != 'https':
                raise ValueError('Небезопасная ссылка на отчёт')
            with urllib.request.urlopen(url,timeout=120) as response:
                content = response.read(32*1024*1024+1)
            if len(content) > 32*1024*1024:
                raise ValueError('Слишком большой операционный отчёт')
            sheets = {}
            with zipfile.ZipFile(BytesIO(content)) as archive:
                if sum(i.file_size for i in archive.infolist()) > 128*1024*1024:
                    raise ValueError('Слишком большой распакованный отчёт')
                for name in archive.namelist():
                    sheet = name.rsplit('/',1)[-1].removesuffix('.json')
                    if name.endswith('.json') and sheet in {'orders_and_offers_transactions','services_and_orders_margin'}:
                        data = json.loads(archive.read(name).decode('utf-8-sig'))
                        if not isinstance(data,(dict,list)):
                            raise ValueError('Изменилась структура операционного отчёта')
                        sheets[sheet] = list(report_rows(data))
                        if not sheets[sheet] and data not in ({},[]) and any(
                                not isinstance(v,(dict,list)) for v in (data.values() if isinstance(data,dict) else data)):
                            raise ValueError('Не удалось прочитать строки операционного отчёта')
            if 'orders_and_offers_transactions' not in sheets:
                raise ValueError('В операционном отчёте нет списка товаров заказов')
            return sheets
        if attempt < 120:
            sleep(5)
    raise TimeoutError('Операционный отчёт ещё формируется; повторите загрузку позже')


def fetch_packet(account, start, end, *, api=None, report_loader=orders_report):
    api = api or api_module()
    business = int(account['business_id'])
    own = [c for c in api.get_campaigns(account['api_key'])
           if int((c.get('business') or {}).get('id') or 0) == business]
    configured = {int(c) for c in account.get('campaign_ids',[]) or []}
    if configured - {int(c['id']) for c in own}:
        raise ValueError('Кампания не принадлежит выбранному кабинету')
    own = [c for c in own if not configured or int(c['id']) in configured]
    unavailable = [c for c in own if c.get('apiAvailability','AVAILABLE') != 'AVAILABLE']
    campaigns = [c for c in own if c not in unavailable]
    if not campaigns:
        raise ValueError('Нет доступных магазинов для операционного отчёта')
    raw = report_loader(api,account,start,end,campaigns)
    packet = {'orders':[], 'margins':[], 'stats':[], 'returns_by_day':{},
              'campaigns':[selected(c,('id','domain','placementType')) for c in campaigns],
              'complete':not unavailable,
              'notes':[f"Отключён API магазина {c['id']}; данные кабинета могут быть неполными." for c in unavailable]}
    order_campaigns = {}
    # Update-date filtering, not creation-date filtering: older orders can be
    # redeemed during this week. Explicit IDs below fill return price lookups.
    stats_by_campaign = {}
    for campaign in campaigns:
        campaign_id = int(campaign['id'])
        records = pages(api,f'/v2/campaigns/{campaign_id}/stats/orders',account['api_key'],'orders',
            payload={'updateFrom':start.isoformat(),'updateTo':end.isoformat()},params={'limit':200})
        stats_by_campaign[campaign_id] = {int(r['id']):r for r in records}
        order_campaigns.update({int(r['id']):campaign_id for r in records})
    for report_day in reversed(date_range(start,end)):
        daily = []
        for campaign in campaigns:
            campaign_id = int(campaign['id'])
            rows = pages(api,f'/v2/campaigns/{campaign_id}/returns',account['api_key'],'returns',
                # Keep cancellations in the raw snapshot as well. They must
                # invalidate an older membership of the same application.
                params={'fromDate':report_day,'toDate':report_day,'type':'RETURN','limit':100},method='GET')
            for row in rows:
                if not row.get('id') or not row.get('orderId') or not isinstance(row.get('items'),list):
                    raise ValueError('Некорректная заявка возврата')
                daily.append({**selected(row,('id','orderId','returnType','refundStatus','shipmentStatus',
                                            'creationDate','updateDate','amount')),
                    'campaign_id':campaign_id,
                    'items':[selected(i,('shopSku','count')) for i in row['items']]})
                order_campaigns[int(row['orderId'])] = campaign_id
            missing = sorted({int(r['orderId']) for r in rows}-set(stats_by_campaign[campaign_id]))
            for offset in range(0,len(missing),50):
                records = pages(api,f'/v2/campaigns/{campaign_id}/stats/orders',account['api_key'],'orders',
                    payload={'orders':missing[offset:offset+50]},params={'limit':200})
                stats_by_campaign[campaign_id].update({int(r['id']):r for r in records})
        packet['returns_by_day'][report_day] = daily
    # Detect an API snapshot changing during the sequence of daily requests.
    # Do not publish a complete-looking week assembled from incompatible lists.
    def return_signature(row):
        return (int(row['campaign_id']),int(row['id']),str(row.get('refundStatus')),
                str(row.get('shipmentStatus')),tuple(sorted((str(i['shopSku']),int(i['count'])) for i in row['items'])))
    for campaign in campaigns:
        campaign_id = int(campaign['id'])
        period = pages(api,f'/v2/campaigns/{campaign_id}/returns',account['api_key'],'returns',
            params={'fromDate':start.isoformat(),'toDate':end.isoformat(),'type':'RETURN','limit':100},method='GET')
        daily = {return_signature(r) for rows in packet['returns_by_day'].values()
                 for r in rows if r['campaign_id']==campaign_id}
        combined = {return_signature({**r,'campaign_id':campaign_id}) for r in period}
        if daily != combined:
            raise ValueError('Яндекс вернул разные списки возвратов по дням и за весь период. Прежние данные сохранены; повторите загрузку позже.')
    for campaign_id,records in stats_by_campaign.items():
        for row in records.values():
            if row.get('fake'):
                continue
            packet['stats'].append({**selected(row,('id','creationDate','status','statusUpdateDate')),
                'campaign_id':campaign_id,
                'commissions':[selected(c,('type','actual')) for c in row.get('commissions',[]) or []],
                'subsidies':[selected(s,('operationType','type','amount')) for s in row.get('subsidies',[]) or []],
                **{name:[selected(i,('shopSku','count','initialCount','prices','details'))
                         for i in row.get(name,[]) or []] for name in ('items','initialItems')}})
    fake_orders = {int(r['id']) for records in stats_by_campaign.values()
                   for r in records.values() if r.get('fake')}
    for row in raw['orders_and_offers_transactions']:
        if int(row['orderId']) in fake_orders:
            continue
        if row.get('businessId') is not None and int(row['businessId']) != business:
            raise ValueError('В операционном отчёте присутствует другой кабинет')
        campaign_id = order_campaigns.get(int(row['orderId']))
        if campaign_id is None:
            possible = [c for c in campaigns if c.get('placementType') == row.get('model')]
            if len(possible)==1:
                campaign_id = int(possible[0]['id'])
        packet['orders'].append({**selected(row,ORDER_FIELDS),'campaign_id':campaign_id})
    packet['margins'] = [selected(row,MARGIN_FIELDS) for row in raw.get('services_and_orders_margin',[])
                         if int(row['orderId']) not in fake_orders]
    return packet


def save_snapshot(conn, account, start, end, packet, fetched_at=None):
    stamp = fetched_at or datetime.now(UTC).isoformat()
    conn.execute('''INSERT INTO rbe2_snapshots VALUES(?,?,?,?,?,?)
        ON CONFLICT(store_slug,business_id,date_from,date_to) DO UPDATE SET
        fetched_at=excluded.fetched_at,payload_json=excluded.payload_json''',
        (account['store_slug'],int(account['business_id']),start.isoformat(),end.isoformat(),
         stamp,json.dumps(packet,ensure_ascii=False,allow_nan=False)))


def read_snapshots(conn, stores):
    if not stores:
        return []
    rows = conn.execute('SELECT * FROM rbe2_snapshots WHERE store_slug IN ('+
                        ','.join('?' for _ in stores)+') ORDER BY fetched_at',stores)
    return [{**dict(r),'data':json.loads(r['payload_json'])} for r in rows]
