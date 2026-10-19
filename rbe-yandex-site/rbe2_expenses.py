"""Gross service costs for the operational basket, not legacy financial P&L.

The source database is opened read-only. Each day uses ONE complete stored
archive version; overlapping ranges must never be concatenated. A missing
archive, original fee, or shop attribution is not a confirmed zero.
"""
import json
import os
import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from rbe2_calculation import date_range, day, money, quantity, DELIVERED

FIELDS = ('businessId','placementModel','partnerId','orderId','shopSku','count','campaignId','advertiserId',
          'serviceName','serviceDateTime','serviceDate','amountWithoutBonuses',
          'totalAmount','qualityIndexAmount','servicePrice','netting','postpaid',
          'prepaid','bonusPaid','payment','paidStorage','customerBonusAmount')
EXPENSE_METRICS = ('commission','logistics','advertising','other_direct_expenses',
                   'order_expenses','bank_receipt','marginal_profit','revenue')
LOGISTICS = {'delivery','express_delivery','crossborder_delivery',
             'delivery_via_transit_warehouse','order_intake'}
OTHER = {'payment_accepting','payment_transfer','money_withdraw','personal_manager',
         'reception_of_surplus','order_processing','order_processing_on_warehouse',
         'storage_of_returns','utilization','installment_plan'}
ADS = {'boost','cpm-boost','product-banners','shelf','pads'}


def read_cached_expenses(account, start, end, packet, source_path=None, *, read_days=True):
    """Copy sanitized source evidence into the NEW snapshot, never write source.

    This performs no financial API request/recalculation. Operational buyout
    quantities and dates do not depend on payment_transfer or old P&L values.
    """
    path = Path(source_path or os.getenv('CHECKSTOCK_DB_PATH',
                Path(__file__).resolve().parent.parent/'data/checkstock.db')).resolve()
    result = {'days':{}, 'placements':[], 'notes':[]}
    if not path.is_file():
        result['notes'].append('Исторический архив исходных комиссий пока недоступен. Комиссия старых возвратов может потребовать отдельной проверки.')
        return result
    owner = (account['store_slug'], int(account['business_id']))
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=10) as source:
        source.row_factory = sqlite3.Row
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        archives = {}
        try:
            for report_day in date_range(start,end) if read_days else []:
                version = source.execute('''SELECT period_from,period_to,imported_at
                    FROM yandex_financial_report_rows WHERE store_slug=? AND business_id=?
                    AND report_kind='united-services' AND period_from<=? AND period_to>=?
                    ORDER BY imported_at DESC,period_to DESC,period_from DESC LIMIT 1''',
                    (*owner,report_day,report_day)).fetchone()
                if not version:
                    continue
                key = tuple(version)
                if key not in archives:
                    sheets = defaultdict(list)
                    for record in source.execute('''SELECT sheet,payload_json FROM yandex_financial_report_rows
                        WHERE store_slug=? AND business_id=? AND report_kind='united-services'
                        AND period_from=? AND period_to=? AND imported_at=?''',(*owner,*key)):
                        row = json.loads(record['payload_json'])
                        sheets[record['sheet']].append({k:row[k] for k in FIELDS if k in row})
                    archives[key] = sheets
                result['days'][report_day] = {'source_at':version['imported_at'],
                    'sheets':{s:[r for r in rows if day(r.get('serviceDateTime') or r.get('serviceDate'))==report_day]
                              for s,rows in archives[key].items()}}
            wanted = {str(r['orderId']) for r in packet['orders']
                      if r.get('offerStatus') in DELIVERED
                      and start.isoformat()<=(day(r.get('deliveryDate')) or '')<=end.isoformat()}
            wanted.update(str(r['orderId']) for rows in packet['returns_by_day'].values() for r in rows)
            # Index by original SKU/date. A refreshed export replaces the old
            # values; changes of amount do not create another original charge.
            original = {}
            for record in source.execute('''SELECT payload_json,imported_at FROM yandex_financial_report_rows
                WHERE store_slug=? AND business_id=? AND report_kind='united-services' AND sheet='placement'
                ORDER BY imported_at,period_to,period_from''',owner):
                row = json.loads(record['payload_json'])
                if str(row.get('orderId')) not in wanted or row.get('amountWithoutBonuses') is None:
                    continue
                if money(row['amountWithoutBonuses'])<0 or not row.get('count'):
                    continue
                key = (str(row['orderId']),str(row.get('shopSku')),
                       row.get('serviceDateTime'),row.get('serviceName'))
                original[key] = {**{k:row[k] for k in FIELDS if k in row},'source_at':record['imported_at']}
            result['placements'] = list(original.values())
        except sqlite3.Error:
            # Do not leak a server path or turn an incompatible/missing table
            # into an empty but complete fee source.
            return {'days':{},'placements':[], 'notes':['Не удалось прочитать архив начислений услуг; прибыль пока недоступна.']}
    return result


def fetch_expenses(account,start,end,packet,api,report_loader):
    try:
        result = read_cached_expenses(account,start,end,packet,read_days=False)
    except (ValueError,KeyError,OSError):
        result = {'days':{},'placements':[],
                  'notes':['Не удалось прочитать исторические исходные комиссии. Неподтверждённые суммы не заменены нулями.']}
    # Historical originals may be read from cache, but current service costs
    # must be freshly checked. A 420/NO_DATA cannot fall back to stale green.
    result['days'] = {}
    try:
        raw = report_loader(api,account,start,end)
        from datetime import UTC,datetime
        stamp = datetime.now(UTC).isoformat()
        safe = {s:[{k:r[k] for k in FIELDS if k in r} for r in rows] for s,rows in raw.items()}
        for rows in safe.values():
            if any(day(r.get('serviceDateTime') or r.get('serviceDate')) is None for r in rows):
                raise ValueError('В начислении услуги не передана дата')
        result['days'] = {d:{'source_at':stamp,'sheets':{
            s:[r for r in rows if day(r.get('serviceDateTime') or r.get('serviceDate'))==d]
            for s,rows in safe.items()}} for d in date_range(start,end)}
        result['placements'].extend({**r,'source_at':stamp} for r in safe.get('placement',[])
            if r.get('amountWithoutBonuses') is not None and money(r['amountWithoutBonuses'])>=0 and r.get('count'))
    except Exception as error:
        from datetime import UTC,datetime
        result['days'] = {}
        result['failed_at'] = datetime.now(UTC).isoformat()
        result['load_error'] = 'Не удалось подтвердить полные расходы: '+str(error)[:350]
        result['notes'].append(result['load_error'])
    return result


def calculate_expenses(snapshots, accounts, start, end, events, campaign_id=None):
    """Return independent costs, provenance and metric-specific unknowns.

    Commission = original gross SKU fee × delivered qty minus the same
    original fee × returned qty. Other charges keep their service date/sign.
    Advertising account charges are counted once, not per marketplace shop.
    """
    owners = {(a['store_slug'],int(a['business_id'])) for a in accounts}
    configured = {(a['store_slug'],int(a['business_id'])):set(map(int,a.get('campaign_ids') or [])) for a in accounts}
    days, originals, campaigns, partners, invalidations = {}, {}, defaultdict(dict), defaultdict(set), {}
    notes, issues = [], defaultdict(set)
    for snapshot in sorted(snapshots,key=lambda s:s['fetched_at']):
        owner = (snapshot['store_slug'],int(snapshot['business_id']))
        if owner not in owners:
            continue
        data = snapshot['data']
        for c in data.get('business_campaigns',data.get('campaigns',[])):
            campaigns[owner][int(c['id'])] = c
        for r in data.get('orders',[]):
            if r.get('campaign_id') is not None and r.get('partnerId') is not None:
                partners[(*owner,int(r['campaign_id']))].add(str(r['partnerId']))
        expenses = data.get('expenses',{})
        notes.extend(expenses.get('notes',[]))
        if expenses.get('failed_at'):
            for d in date_range(date.fromisoformat(snapshot['date_from']),date.fromisoformat(snapshot['date_to'])):
                key = (*owner,d)
                invalidations[key] = max(invalidations.get(key,''),expenses['failed_at'])
        for report_day,source in expenses.get('days',{}).items():
            key = (*owner,report_day)
            if key not in days or source['source_at']>=days[key]['source_at']:
                days[key] = source
        for row in expenses.get('placements',[]):
            key = (*owner,str(row['orderId']),str(row['shopSku']),row.get('serviceDateTime'),row.get('serviceName'))
            if key not in originals or row['source_at']>=originals[key]['source_at']:
                originals[key] = row
    days = {key:r for key,r in days.items() if r['source_at']>=invalidations.get(key,'')}
    expected = {(*o,d) for o in owners for d in date_range(start,end)}
    absent = sorted(expected-set(days))
    for store,business,report_day in absent:
        for group in ('logistics','advertising','other_direct_expenses'):
            issues[group].add(f'{store.upper()} · {report_day}: нет архива начислений услуг')
    totals = defaultdict(Decimal)
    breakdown, unallocated, missing_fees = [], [], []
    rates = defaultdict(list)
    for key,row in originals.items():
        try:
            rates[key[:4]].append((money(row['amountWithoutBonuses']),quantity(row['count'])))
        except (ValueError,KeyError):
            pass
    for group,sign in (('sales',1),('returns',-1)):
        for event in events[group]:
            # Operational order IDs are unique within this owner; do not use
            # a SKU-only lookup or an unrelated shop's fee.
            owner = next((o for o in owners if o[0]==event['store'] and o[1]==event['business_id']),None)
            if owner is None:
                continue
            key = (*owner,str(event['order_id']),str(event['sku']))
            candidates = rates.get(key,[])
            count = sum(n for amount,n in candidates)
            if not candidates or not count or count<event['count']:
                label = f"{owner[0].upper()} · заказ {event['order_id']} · {event['sku']}: нет исходной комиссии до баллов"
                issues['commission'].add(label)
                missing_fees.append({**event,'kind':group})
                continue
            distinct = {amount/n for amount,n in candidates if n}
            # Different per-unit tariffs for one SKU cannot be allocated to
            # partial returns without a unit/lot link.
            if len(distinct)!=1:
                issues['commission'].add(f"{owner[0].upper()} · {event['order_id']}: разные исходные тарифы товара")
                continue
            value = distinct.pop()*event['count']*sign
            totals['commission'] += value
            totals['commission_sales' if sign>0 else 'commission_returns'] += abs(value)
            breakdown.append({'store':owner[0],'business_id':owner[1],'date':event['date'],
                              'group':'commission','sheet':'placement','order_id':event['order_id'],
                              'sku':event['sku'],'amount':float(value),'basis':group})

    def attributed(owner,row):
        targets = {campaign_id} if campaign_id is not None else configured[owner]
        if not targets or targets==set(campaigns[owner]):
            return True
        # Advertising campaignId is NOT the Marketplace campaign identifier.
        if row.get('partnerId') is not None:
            selected_partners = set().union(*(partners.get((*owner,c),set()) for c in targets))
            if str(row['partnerId']) in selected_partners:
                return True
            other_partners = set().union(*(partners.get((*owner,c),set()) for c in set(campaigns[owner])-targets))
            if str(row['partnerId']) in other_partners:
                return False
        matches = {int(c['id']) for c in campaigns[owner].values() if c.get('placementType')==row.get('placementModel')}
        if row.get('placementModel') and matches:
            if matches<=targets:
                return True
            if not matches&targets:
                return False
        return None

    for (store,business,report_day),source in sorted(days.items()):
        if not start.isoformat()<=report_day<=end.isoformat():
            continue
        for sheet,rows in source['sheets'].items():
            for row in rows:
                if row.get('businessId') is not None and int(row['businessId'])!=business:
                    raise ValueError('В источнике расходов присутствует другой кабинет')
                group,field = ('other_direct_expenses','servicePrice')
                if sheet=='placement':
                    # Gross commission above uses the basket and original fee.
                    # A negative totalAmount is the paid part of its reversal,
                    # not another full fee to deduct a second time.
                    group,field = 'other_direct_expenses','qualityIndexAmount'
                elif sheet in LOGISTICS:
                    group = 'logistics'
                elif sheet=='crossregional_delivery':
                    group = 'logistics'
                elif sheet in ADS:
                    group,field = 'advertising',('postpaid' if sheet=='boost' else 'payment')
                elif sheet.startswith('paid_storage_after_'):
                    field = 'paidStorage'
                elif sheet=='loyalty_and_reviews':
                    field = 'customerBonusAmount'
                elif sheet=='export_from_warehouse':
                    if ' '.join(str(row.get('serviceName') or '').split()).casefold()=='доставка грузоместа до склада назначения':
                        group = 'logistics'
                elif sheet not in OTHER:
                    issues['other_direct_expenses'].add(f'{store.upper()} · {report_day}: неизвестный лист расходов {sheet}')
                    continue
                try:
                    if sheet=='placement' and row.get(field) is None:
                        continue  # optional monetary surcharge, not a tariff
                    value = money(row.get(field))
                    if sheet=='crossregional_delivery' and row.get('netting') is not None:
                        value += money(row['netting'])
                    if sheet=='boost' and any(row.get(f) not in (None,0) for f in ('prepaid','bonusPaid')):
                        issues['advertising'].add(f'{store.upper()} · {report_day}: предоплата/бонусы рекламы требуют сверки способов оплаты')
                except ValueError:
                    issues[group].add(f'{store.upper()} · {report_day}: {sheet}, не передано поле {field}')
                    continue
                attribution = attributed((store,business),row)
                if attribution is False or value==0:
                    continue
                detail = {'store':store,'business_id':business,'date':report_day,'group':group,
                          'sheet':sheet,'order_id':str(row.get('orderId') or ''),
                          'sku':str(row.get('shopSku') or ''),'amount':float(value),
                          'source_at':source['source_at']}
                if attribution is None:
                    unallocated.append(detail)
                    issues[group].add(f'{store.upper()} · {report_day}: {sheet} без привязки к выбранной кампании')
                    continue
                totals[group] += value
                breakdown.append(detail)
    values = {k:float(totals[k].quantize(Decimal('.01'))) for k in ('commission','logistics','advertising','other_direct_expenses')}
    known = dict(values)
    for group in values:
        if issues[group]:
            values[group] = None
    for group,messages in issues.items():
        if messages:
            notes.append('Расходы не подтверждены: '+'; '.join(sorted(messages)[:8]))
    return {'values':values,'known_subtotals':known,'breakdown':breakdown,
            'issues':{group:sorted(messages) for group,messages in issues.items() if messages},
            'unallocated':unallocated,'missing_original_fees':missing_fees,
            'commission_sales':float(totals['commission_sales']),
            'commission_returns':float(totals['commission_returns']),
            'source_days':[{'store':s,'business_id':b,'date':d,'loaded_at':r['source_at']}
                           for (s,b,d),r in sorted(days.items()) if start.isoformat()<=d<=end.isoformat()],
            'missing_days':[{'store':s,'business_id':b,'date':d} for s,b,d in absent],
            'notes':list(dict.fromkeys(notes))}
