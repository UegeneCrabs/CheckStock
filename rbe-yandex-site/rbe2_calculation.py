"""Independent operational RBE. Never reads financial facts or refund pins.

Deliveries use deliveryDate, not order creation, statusChanged or bank dates.
Returns use membership in the requested daily getReturns response. API dates
must not be reconstructed from creationDate/updateDate of a return document.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

RETURN_STATUSES = {'REFUNDED','REFUNDED_WITH_BONUSES','REFUNDED_BY_SHOP','REFUND_IN_PROGRESS'}
HANDOVER_STATUSES = {'RECEIVED','IN_TRANSIT','READY_FOR_PICKUP','PICKED','LOST',
    'FULFILMENT_RECEIVED','PREPARED_FOR_UTILIZATION','UTILIZED',
    'READY_FOR_EXPROPRIATION','RECEIVED_FOR_EXPROPRIATION','NOT_IN_DEMAND'}


def recognized_return(row):
    if row.get('returnType')!='RETURN' or row.get('shipmentStatus') in {'CANCELLED','EXPIRED'}:
        return False
    status = row.get('refundStatus')
    if status=='REFUND_IN_PROGRESS':
        # A claim alone is not a returned product. Already refunded fast
        # returns below need not have a physical shipment.
        return row.get('shipmentStatus') in HANDOVER_STATUSES
    return status in RETURN_STATUSES
RETURNED = {'Возврат оформлен', 'Возврат отправлен', 'Возврат готов к передаче вам',
            'Возврат передан вам', 'Возврат принят', 'Возврат принят на складе',
            'Полный возврат принят на складе'}
DELIVERED = RETURNED | {'Доставлен покупателю', 'Частично доставлен'}
CANCELLED = {'Отменён', 'Отменен', 'Невыкуп отправлен', 'Невыкуп принят на складе',
             'Невыкуп готов к передаче вам', 'Невыкуп передан вам'}
METRICS = ('orders_turnover', 'cancelled_count', 'cancelled_turnover',
           'detail_redeemed_count', 'detail_returned_count', 'buyout_count',
           'detail_seller_sales', 'detail_seller_returns', 'buyout_seller_turnover',
           'detail_buyer_sales', 'detail_buyer_returns', 'buyout_buyer_turnover',
           'commission', 'logistics', 'advertising', 'other_direct_expenses',
           'order_expenses', 'revenue', 'bank_receipt', 'sold_cost_of_goods', 'marginal_profit')
FEE_GROUPS = {
    'commission': ('saleCommission', 'expropriationResale'),
    'logistics': ('buyerDelivery', 'crossregionalDelivery', 'buyerExpressDelivery',
                  'crossborderDelivery', 'orderIntake'),
    'advertising': ('boost',),
}


def day(value):
    text = str(value or '').strip()
    for pattern in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(text[:10], pattern).date().isoformat()
        except ValueError:
            pass
    return None


def money(value):
    if value is None or isinstance(value, bool):
        raise ValueError('Не указана сумма')
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('Некорректная сумма') from error
    if not result.is_finite():
        raise ValueError('Некорректная сумма')
    return result


def quantity(value):
    number = money(value)
    if number < 0 or number != int(number):
        raise ValueError('Некорректное количество товара')
    return int(number)


def date_range(start, end):
    return [(start + timedelta(days=i)).isoformat() for i in range((end-start).days+1)]


def canonical(snapshots):
    """Latest entity versions; replace whole daily return responses, including []."""
    orders, margins, stats, daily_returns, coverage = {}, {}, {}, {}, {}
    latest_return_state = {}
    notes = []
    for snapshot in sorted(snapshots, key=lambda s: (s['fetched_at'], s['date_to'], s['date_from'])):
        owner = (snapshot['store_slug'], int(snapshot['business_id']))
        packet = snapshot['data']
        for row in packet['orders']:
            orders[(*owner, str(row['orderId']), str(row['shopSku']))] = row
        for row in packet['margins']:
            margins[(*owner, str(row['orderId']))] = row
        for row in packet.get('stats', []):
            stats[(*owner, str(row['id']))] = row
        for report_day, returns in packet['returns_by_day'].items():
            daily_returns[(*owner, report_day)] = returns
            for row in returns:
                latest_return_state[(*owner,int(row['campaign_id']),int(row['id']))] = row
        for report_day in date_range(date.fromisoformat(snapshot['date_from']), date.fromisoformat(snapshot['date_to'])):
            coverage[(*owner, report_day)] = packet.get('complete',True)
        notes.extend(packet.get('notes', []))
    # A document returned on two API days is not two returned products.
    applications, pending = {}, {}
    duplicate_days = defaultdict(set)
    for (store, business, report_day), rows in sorted(daily_returns.items()):
        for row in rows:
            identity = (store,business,int(row['campaign_id']),int(row['id']))
            row = latest_return_state[identity]
            if not recognized_return(row):
                if row.get('returnType')=='RETURN' and row.get('refundStatus')=='REFUND_IN_PROGRESS' and row.get('shipmentStatus') not in {'CANCELLED','EXPIRED'}:
                    pending.setdefault(identity,(report_day,row))
                continue
            key = identity
            duplicate_days[key].add(report_day)
            if key not in applications:
                applications[key] = (report_day, row)
    for key, days in duplicate_days.items():
        if len(days) > 1:
            notes.append(f'{key[0].upper()}: возврат {key[3]} получен для нескольких дней; учтён один раз за {min(days)}. Дату нужно проверить по исходному заказу и данным Маркета.')
    return orders, margins, stats, applications, {key for key, complete in coverage.items() if complete}, list(dict.fromkeys(notes)), pending


def calculate(snapshots, accounts, start, end, price_for_day, campaign_id=None):
    """Operational basket plus independently verified gross expenses.

    Cumulative paid fees of the order report remain diagnostic only: gross
    seller revenue must not be reduced by fees after Market bonus offsets.
    """
    orders, margins, stats, applications, coverage, notes, pending = canonical(snapshots)
    owners = {(a['store_slug'], int(a['business_id'])) for a in accounts}
    selected_models = {(s['store_slug'],int(s['business_id'])):c.get('placementType')
                       for s in snapshots for c in s['data'].get('campaigns',[])
                       if campaign_id is not None and int(c['id'])==campaign_id}
    expected = {(*owner, d) for owner in owners for d in date_range(start, end)}
    missing = sorted(expected - coverage)
    unknown_campaign = campaign_id is not None and any(
        key[:2] in owners and row.get('campaign_id') is None
        and (not selected_models.get(key[:2]) or not row.get('model')
             or row['model']==selected_models[key[:2]])
        and any(start.isoformat() <= (day(row.get(field)) or '') <= end.isoformat()
                for field in ('deliveryDate','creationDate')) for key,row in orders.items())
    if unknown_campaign:
        notes.append('У части заказов не определена кампания. Итог выбранной кампании может быть неполным; выберите все модели или обновите данные.')
    totals = defaultdict(Decimal)
    issues = defaultdict(set)
    sales, returns, cancellations, fees = [], [], [], []
    returned_units = defaultdict(int)
    fee_orders = {}
    delivery_anchors, cancellation_anchors = {}, {}
    for key,row in orders.items():
        delivery = day(row.get('deliveryDate'))
        cancellation = day(row.get('statusChanged'))
        if delivery and row.get('offerStatus') in DELIVERED | CANCELLED:
            delivery_anchors[key[:3]] = min(delivery,delivery_anchors.get(key[:3],delivery))
        elif row.get('offerStatus') in CANCELLED and cancellation:
            cancellation_anchors[key[:3]] = min(cancellation,cancellation_anchors.get(key[:3],cancellation))
    fee_anchors = {**cancellation_anchors,**delivery_anchors}
    total_cost = Decimal(0)
    price_cache = {}
    start_key, end_key = start.isoformat(), end.isoformat()

    def in_period(value):
        return value is not None and start_key <= value <= end_key

    def add_cost(owner, row, report_day, count, sign):
        nonlocal total_cost
        identity = (*owner, report_day)
        if identity not in price_cache:
            price_cache[identity] = price_for_day(owner[0], report_day)
        sku = str(row['shopSku'])
        price = price_cache[identity].get(sku)
        if price is None:
            issues['sold_cost_of_goods'].add(f'{owner[0].upper()} · {report_day} · {sku}')
        else:
            total_cost += money(price) * count * sign

    def statistic_item(owner, order_id, sku):
        record = stats.get((*owner, order_id), {})
        return next((i for i in [*record.get('items', []), *record.get('initialItems', [])]
                     if str(i.get('shopSku')) == sku), None)

    def prices(row, item=None):
        count = quantity(row['deliveredOrReturned'])
        seller = money(row['billingPrice'])
        buyer = row.get('buyerPaymentAmount')
        if buyer is not None and count:
            return seller, money(buyer)/count
        buyer_price = next((p['costPerItem'] for p in (item or {}).get('prices', []) if p['type'] == 'BUYER'), None)
        return seller, money(buyer_price)

    for key, row in orders.items():
        owner, order_id, sku = key[:2], key[2], key[3]
        if owner not in owners or (campaign_id is not None and row.get('campaign_id') != campaign_id):
            continue
        delivery, created = day(row.get('deliveryDate')), day(row.get('creationDate'))
        cancelled = row.get('offerStatus') in CANCELLED and not delivery
        stat = statistic_item(owner, order_id, sku)
        try:
            count = quantity(row.get('deliveredOrReturned'))
            original_value = stat.get('initialCount') if stat else None
            original = quantity(original_value) if original_value is not None else max(
                count,quantity((stat or {}).get('count') or 0),quantity(row.get('transferredForDelivery') or 0))
            cancelled_quantity = original if cancelled else max(0, original-quantity(stat.get('count',original) if stat else original))
            if in_period(created):
                if not original:
                    raise ValueError('Нет первоначального количества заказа')
                totals['orders_turnover'] += money(row.get('partnerPriceForDelivery'))*original
                if cancelled_quantity:
                    cancelled_amount = money(row.get('partnerPriceForDelivery'))*cancelled_quantity
                    totals['detail_orders_cancelled'] += cancelled_amount
                    totals['cancelled_count'] += cancelled_quantity
                    totals['cancelled_turnover'] += cancelled_amount
                    cancellations.append({'store':owner[0], 'order_id':order_id, 'sku':sku,
                        'count':cancelled_quantity, 'date':created, 'basis':'Дата оформления отменённых заказов'})
        except (ValueError, KeyError):
            if in_period(created):
                issues['orders_turnover'].add(f'{owner[0]} · заказ {order_id}, {sku}: нет количества или цены')
            if in_period(delivery) and row.get('offerStatus') in DELIVERED | CANCELLED:
                issues['sales_quantity'].add(f'{owner[0]} · заказ {order_id}, {sku}: нет количества')
            continue
        if cancelled and in_period(day(row.get('statusChanged'))):
            if in_period(fee_anchors.get((*owner,order_id))):
                fee_orders[(*owner, order_id)] = fee_anchors[(*owner,order_id)]
        # A dispatch, pickup or non-purchase is never a delivery, even if the
        # report's "deliveredOrReturned" counter is already positive.
        if not delivery or row.get('offerStatus') not in DELIVERED | CANCELLED or not in_period(delivery):
            continue
        if not count:
            issues['sales_quantity'].add(f'{owner[0]} · заказ {order_id}, {sku}: нет количества')
            continue
        totals['detail_redeemed_count'] += count
        if in_period(fee_anchors.get((*owner,order_id))):
            fee_orders[(*owner, order_id)] = fee_anchors[(*owner,order_id)]
        event = {'store':owner[0], 'business_id':owner[1], 'campaign_id':row.get('campaign_id'),
                 'order_id':order_id, 'sku':sku, 'count':count, 'date':delivery}
        try:
            seller, buyer = prices(row, stat)
            totals['detail_seller_sales'] += seller*count
            totals['detail_buyer_sales'] += buyer*count
            event.update({'seller_amount':float(seller*count), 'buyer_amount':float(buyer*count)})
        except (ValueError, KeyError):
            issues['sales'].add(f'{owner[0]} · заказ {order_id}, {sku}: нет цены')
        add_cost(owner, row, delivery, count, 1)
        sales.append(event)

    for key, (report_day, application) in applications.items():
        owner = key[:2]
        if owner not in owners or not in_period(report_day) or (campaign_id is not None and key[2] != campaign_id):
            continue
        order_id = str(application['orderId'])
        for item in application['items']:
            sku, count = str(item['shopSku']), quantity(item['count'])
            if not count:
                continue
            totals['detail_returned_count'] += count
            row = orders.get((*owner, order_id, sku))
            stat = statistic_item(owner, order_id, sku)
            unit_key = (*owner,order_id,sku)
            returned_units[unit_key] += count
            known_count = row.get('deliveredOrReturned') if row else (stat or {}).get('initialCount',(stat or {}).get('count'))
            if known_count is not None and returned_units[unit_key] > quantity(known_count):
                issues['return_quantity'].add(f'{owner[0]} · заказ {order_id}, {sku}: заявки превышают количество купленных товаров')
            event = {'store':owner[0], 'business_id':owner[1], 'order_id':order_id, 'sku':sku, 'count':count,
                     'date':report_day, 'return_id':key[3], 'campaign_id':key[2]}
            try:
                if row:
                    seller, buyer = prices(row, stat)
                elif stat:
                    price_parts = {p['type']:money(p['costPerItem']) for p in stat.get('prices', [])}
                    if set(price_parts)-{'BUYER','MARKETPLACE','CASHBACK'}:
                        raise ValueError('Неизвестный вид цены')
                    buyer = price_parts['BUYER']
                    seller = sum(price_parts.values())
                else:
                    raise ValueError('Нет товара заказа')
                totals['detail_seller_returns'] += seller*count
                # Price per unit of the original order, not the application's
                # whole amount (which can include several SKUs/quantities).
                totals['detail_buyer_returns'] += buyer*count
                event.update({'seller_amount':float(seller*count), 'buyer_amount':float(buyer*count)})
            except (ValueError, KeyError):
                issues['returns'].add(f'{owner[0]} · заказ {order_id}, {sku}: нет цены')
            add_cost(owner, {'shopSku':sku}, report_day, count, -1)
            returns.append(event)

    for key, report_day in fee_orders.items():
        row = margins.get(key)
        if not row or row.get('summaryCommission') is None:
            issues['expenses'].add(f'{key[0]} · заказ {key[2]}: нет расходов по заказу')
            continue
        try:
            summary = money(row['summaryCommission'])
            groups = {group:sum(money(row.get(field,0)) for field in fields) for group, fields in FEE_GROUPS.items()}
            for group, value in groups.items():
                totals[group] += value
            # summaryCommission is authoritative; all remaining named/new
            # services are a residual, never added a second time.
            other = summary-sum(groups.values())
            totals['other_direct_expenses'] += other
            totals['order_expenses'] += summary
            fees.append({'store':key[0], 'order_id':key[2], 'date':report_day, 'amount':float(summary)})
        except ValueError:
            issues['expenses'].add(f'{key[0]} · заказ {key[2]}: некорректные расходы')

    totals['buyout_count'] = totals['detail_redeemed_count']-totals['detail_returned_count']
    totals['buyout_seller_turnover'] = totals['detail_seller_sales']-totals['detail_seller_returns']
    totals['buyout_buyer_turnover'] = totals['detail_buyer_sales']-totals['detail_buyer_returns']
    totals['sold_cost_of_goods'] = total_cost
    totals['bank_receipt'] = totals['buyout_seller_turnover']-totals['order_expenses']
    totals['marginal_profit'] = totals['bank_receipt']-total_cost
    totals['detail_orders_remaining'] = totals['orders_turnover']-totals['detail_orders_cancelled']
    values = {k:float(totals[k].quantize(Decimal('.01'))) for k in (*METRICS, 'detail_orders_remaining', 'detail_orders_cancelled')}
    if not any((s['store_slug'],int(s['business_id'])) in owners for s in snapshots) or not owners:
        values = {k:None for k in values}
    if issues['orders_turnover']:
        values['orders_turnover'] = None
    if issues['sales'] or issues['returns'] or issues['sales_quantity']:
        for metric in ('buyout_seller_turnover','buyout_buyer_turnover','bank_receipt','marginal_profit'):
            values[metric] = None
    if issues['sales'] or issues['sales_quantity']:
        values['detail_seller_sales'] = values['detail_buyer_sales'] = None
    if issues['sales_quantity']:
        for metric in ('detail_redeemed_count','buyout_count','sold_cost_of_goods'):
            values[metric] = None
    if issues['returns']:
        values['detail_seller_returns'] = values['detail_buyer_returns'] = None
    if issues['return_quantity']:
        for metric in ('detail_returned_count','buyout_count','detail_seller_returns','detail_buyer_returns',
                       'buyout_seller_turnover','buyout_buyer_turnover','sold_cost_of_goods'):
            values[metric] = None
    if issues['expenses']:
        for metric in ('commission','logistics','advertising','other_direct_expenses','order_expenses','bank_receipt','marginal_profit'):
            values[metric] = None
    if issues['sold_cost_of_goods']:
        values['sold_cost_of_goods'] = values['marginal_profit'] = None
    if missing:
        values['marginal_profit'] = None
    for field, items in issues.items():
        if items and field!='expenses':
            notes.append('Не хватает данных: ' + '; '.join(sorted(items)[:12]))
    if missing:
        notes.append('Не загружены магазин/день: ' + '; '.join(f'{s.upper()} · {d}' for s,b,d in missing[:14]))
    from rbe2_expenses import calculate_expenses, EXPENSE_METRICS
    expense_result = calculate_expenses(snapshots,accounts,start,end,
        {'sales':sales,'returns':returns},campaign_id)
    # Preserve the net/paid order summary for reconciliation, but never mix
    # its groups with gross service costs or use it as a fallback to profit.
    diagnostic_order_fees = {k:float(totals[k]) for k in
        ('commission','logistics','advertising','other_direct_expenses','order_expenses')}
    values.update(expense_result['values'])
    if values['detail_redeemed_count'] is None or values['detail_returned_count'] is None:
        values['commission'] = None
        expense_result['issues'].setdefault('commission',[]).append('Не определено количество товаров операционной корзины')
    def difference(base, *costs):
        if base is None or any(c is None for c in costs):
            return None
        return float((money(base)-sum((money(c) for c in costs),Decimal(0))).quantize(Decimal('.01')))
    cost_values = [values[k] for k in ('commission','logistics','advertising','other_direct_expenses')]
    values['order_expenses'] = None if any(v is None for v in cost_values) else float(sum(map(money,cost_values)))
    values['revenue'] = difference(values['buyout_seller_turnover'],values['commission'],values['logistics'])
    values['bank_receipt'] = difference(values['revenue'],values['advertising'],values['other_direct_expenses'])
    values['marginal_profit'] = difference(values['bank_receipt'],values['sold_cost_of_goods'])
    if missing or unknown_campaign:
        values['revenue'] = values['bank_receipt'] = values['marginal_profit'] = None
    # Match the financial dashboard's ratio while retaining RBE 2's own sources.
    buyer_turnover = values['buyout_buyer_turnover']
    profit = values['marginal_profit']
    values['marginal_profit_percent'] = (
        float((money(profit) / money(buyer_turnover) * 100).quantize(Decimal('.01')))
        if profit is not None and buyer_turnover is not None and buyer_turnover != 0 else None
    )
    # Storage is already included in other direct expenses; never deduct it twice.
    values['storage'] = None
    notes.extend(expense_result['notes'])
    pending_events = []
    for key,(report_day,row) in pending.items():
        if key[:2] in owners and in_period(report_day) and (campaign_id is None or key[2]==campaign_id):
            pending_events.append({'store':key[0],'business_id':key[1],'campaign_id':key[2],
                'return_id':key[3],'order_id':str(row['orderId']),'date':report_day,
                'refund_status':row['refundStatus'],'shipment_status':row.get('shipmentStatus'),
                'items':row['items']})
    if pending_events:
        notes.append('Заявки без подтверждения передачи товара/возврата денег пока не уменьшают продажи: '+
                     '; '.join(f"{r['store'].upper()} · возврат {r['return_id']} · заказ {r['order_id']}" for r in pending_events))
    expense_detail = ('Выкупы и возвраты — операционные. Комиссия — исходное начисление до баллов по заказу и артикулу, '
                      'за минусом комиссии возвратов. Прочие услуги — по датам начисления. '
                      'Реклама кабинета учитывается один раз. Налоги и расходы вне Маркета не включены.')
    notes.insert(0, expense_detail)
    quality = {metric:{'complete':not missing and not unknown_campaign and values[metric] is not None,
                       'estimated':False,
                       'detail':expense_detail if metric in EXPENSE_METRICS else 'Выкупы — по дате доставки; возвраты — по дневной выборке API, без финансового отчёта.'} for metric in METRICS}
    quality['storage'] = {'complete':quality['other_direct_expenses']['complete'], 'estimated':False,
                          'detail':'Хранение включено в строку «Иное» и здесь не прибавляется второй раз.'}
    quality['marginal_profit_percent'] = {
        'complete':quality['marginal_profit']['complete'] and quality['buyout_buyer_turnover']['complete']
                   and values['marginal_profit_percent'] is not None,
        'estimated':False,
        'detail':'Маржинальная прибыль / выкупы минус возвраты в цене покупателя × 100. '
                 'Если оборот равен нулю или прибыль неполная, процент не рассчитывается.'}
    for metric,messages in expense_result['issues'].items():
        quality[metric]['detail'] = '; '.join(messages[:6])
    if missing:
        by_store = defaultdict(list)
        for store, business, report_day in missing:
            by_store[store].append(report_day)
        unavailable = '; '.join(
            f'{store.upper()} · {days[0]}' + (f'–{days[-1]}' if len(days)>1 else '')
            for store, days in sorted(by_store.items()))
        reason = 'Нет полного ответа API для ' + unavailable + '. Общий итог и расчётная сумма к перечислению не подтверждены.'
        for state in quality.values():
            if not state['complete']:
                state['detail'] = reason + ' ' + state['detail']
    return {'values':values, 'quality':quality, 'notes':list(dict.fromkeys(notes)),
            'events':{'sales':sales,'returns':returns,'cancellations':cancellations,'expenses':fees},
            'expenses':expense_result, 'order_report_expenses':diagnostic_order_fees,
            'order_report_missing_expenses':sorted(issues['expenses']),
            'pending_returns':pending_events,
            'missing_days':[{'store':s,'business_id':b,'date':d} for s,b,d in missing]}
