"""Keep a returned unit on its first financial recognition date.

The ledger is separate from replaceable report snapshots. Reads never write.
API return IDs are used only to verify cancellation, never to date a refund.
"""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.repositories.core import WRITE_LOCK, get_connection
from app.yandex.buyer_refunds import item_key, source_day


SCHEMA = """CREATE TABLE IF NOT EXISTS yandex_refund_recognitions (
    store_slug TEXT NOT NULL,
    business_id INTEGER NOT NULL,
    order_id TEXT NOT NULL,
    shop_sku TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    campaign_id INTEGER,
    return_id INTEGER,
    cancelled INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (store_slug,business_id,order_id,shop_sku,unit_index)
)"""


def read(conn, store_slug, business_id):
    try:
        rows = conn.execute("SELECT * FROM yandex_refund_recognitions WHERE store_slug=? AND business_id=?",
                            (store_slug, business_id))
    except Exception as error:
        # Older archived databases and read-only diagnostics predate the ledger.
        if 'no such table' in str(error) or 'does not exist' in str(error):
            return []
        raise
    return [dict(r) for r in rows]


def _unit_key(event, index):
    return (*item_key(event), index)


def split_units(events):
    units = {}
    for event in events:
        quantity = int(event['count'])
        indexes = event['refund_unit_indexes']
        if len(indexes) != quantity:
            raise ValueError('Не совпадает количество единиц финансового возврата')
        for index in indexes:
            key = _unit_key(event, index)
            if key in units:
                raise ValueError('Повторная единица финансового возврата')
            units[key] = {**event, 'count': 1, 'quantity': 1, 'refund_unit_indexes': [index],
                          'sumBillingPriceOfItems': event['sumBillingPriceOfItems'] / quantity,
                          'refundBuyerPaymentAmount': event['refundBuyerPaymentAmount'] / quantity}
    return units


def merge(events, saved):
    """New evidence may refine amounts; a saved date and basis remain fixed."""
    units = split_units(events)
    for row in saved:
        key = (row['order_id'], row['shop_sku'], row['unit_index'])
        previous = json.loads(row['payload_json'])
        current = units.get(key, previous)
        units[key] = {**current, 'refund_day': previous['refund_day'],
                      'refund_basis': previous['refund_basis'],
                      'refund_first_at': previous.get('refund_first_at'),
                      'refund_event_key': previous['refund_event_key']}
    return units


def visible(events, saved, date_from, date_to):
    if not saved:
        return [e for e in events if date_from <= e['refund_day'] <= date_to]
    cancelled = {(r['order_id'], r['shop_sku'], r['unit_index']) for r in saved if r['cancelled']}
    return [e for key, e in merge(events, saved).items()
            if key not in cancelled and date_from <= e['refund_day'] <= date_to]


def _write(conn, store_slug, business_id, units, previous, links, now):
    for key, event in units.items():
        old = previous.get(key, {})
        campaign, return_id, cancelled = links.get(key, (
            old.get('campaign_id'), old.get('return_id'), old.get('cancelled', 0)))
        if old.get('return_id') is not None and (campaign, return_id) != (old['campaign_id'], old['return_id']):
            raise ValueError('Изменилось сопоставление заявки возврата; требуется повторная проверка')
        conn.execute("""INSERT INTO yandex_refund_recognitions
            (store_slug,business_id,order_id,shop_sku,unit_index,payload_json,campaign_id,return_id,cancelled,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(store_slug,business_id,order_id,shop_sku,unit_index) DO UPDATE SET
                payload_json=excluded.payload_json, campaign_id=excluded.campaign_id,
                return_id=excluded.return_id, cancelled=excluded.cancelled, updated_at=excluded.updated_at""",
            (store_slug, business_id, *key, json.dumps(event, ensure_ascii=False),
             campaign, return_id, int(cancelled), now))


def seed(store_slug, business_id, now):
    """Pin existing evidence before an import replaces its first snapshot."""
    from app.repositories.yandex_financial import buyer_refund_events
    conn = get_connection()
    try:
        with WRITE_LOCK:
            conn.execute(SCHEMA)
            conn.commit()
        if read(conn, store_slug, business_id):
            return
        coverage = next(iter(conn.execute("SELECT 1 FROM yandex_financial_report_rows WHERE store_slug=? "
            "AND business_id=? AND report_kind='united-netting' LIMIT 1", (store_slug, business_id))), None)
        if coverage is None:
            return
        events = buyer_refund_events(store_slug, business_id, '0001-01-01', '9999-12-31',
                                     source=conn, pinned=False)
        with WRITE_LOCK:
            # Re-read in the write transaction in case another import seeded it.
            saved = read(conn, store_slug, business_id)
            previous = {(r['order_id'], r['shop_sku'], r['unit_index']): r for r in saved}
            _write(conn, store_slug, business_id, merge(events, saved), previous, {}, now)
            conn.commit()
    finally:
        conn.close()


def _created_before(row, event):
    created = source_day(row.get('creationDate'))
    if not created or created > event['refund_day']:
        return False
    first_at = str(event.get('refund_first_at') or '')
    if not first_at:
        return True
    try:
        started = datetime.fromisoformat(str(row['creationDate']))
        first = datetime.strptime(first_at, '%d.%m.%Y %H:%M')
        first = first.replace(tzinfo=ZoneInfo('Europe/Moscow'))
        if started.tzinfo is None:
            started = started.replace(tzinfo=ZoneInfo('Europe/Moscow'))
        # Financial exports have minute precision; include that full minute.
        return started < first + timedelta(minutes=1)
    except ValueError:
        return True


def cancellation_links(units, saved, returns, *, incoming=None):
    """Match a financial unit to a return ID before interpreting cancellation.

    Retain existing links. For new links, consider applications created no
    later than the first financial operation, respecting their quantities.
    If separate applications for the same SKU cannot be distinguished,
    stop the calculation rather than cancel an unrelated returned unit.
    """
    by_item = defaultdict(list)
    by_id = {}
    for row in returns:
        if row.get('returnType') != 'RETURN':
            continue
        identity = (row['campaign_id'], row['id'])
        by_id[identity] = row
        for item in row.get('items', []):
            by_item[(str(row['orderId']), str(item['shopSku']))].append((row, int(item['count'])))
    previous = {(r['order_id'], r['shop_sku'], r['unit_index']): r for r in saved}
    used = defaultdict(int)
    links = {}
    # Previously identified units reserve the application's quantity first.
    for key in units:
        old = previous.get(key, {})
        identity = (old.get('campaign_id'), old.get('return_id'))
        if identity[1] is not None:
            used[(*identity, *key[:2])] += 1
            if identity in by_id:
                row = by_id[identity]
                cancelled = row.get('refundStatus') == 'CANCELLED' or row.get('shipmentStatus') == 'CANCELLED'
                current = (incoming or {}).get(key)
                if cancelled and current:
                    previous_event = json.loads(old['payload_json'])
                    if current['refund_event_key'] != previous_event['refund_event_key']:
                        other = [r for r, _ in by_item[key[:2]]
                                 if (r['campaign_id'], r['id']) != identity
                                 and r.get('refundStatus') != 'CANCELLED'
                                 and r.get('shipmentStatus') != 'CANCELLED'
                                 and source_day(r.get('creationDate'))
                                 and previous_event['refund_day'] < source_day(r['creationDate']) <= current['refund_day']]
                        if other:
                            raise ValueError(f'Нельзя однозначно связать новый возврат после отмены: заказ {key[0]}, артикул {key[1]}')
                links[key] = (*identity, cancelled)
    for key, event in sorted(units.items(), key=lambda kv: (kv[1]['refund_day'], kv[0])):
        if previous.get(key, {}).get('return_id') is not None:
            continue
        candidates = [(r, count) for r, count in by_item[key[:2]]
                      if used[(r['campaign_id'], r['id'], *key[:2])] < count]
        eligible = [(r, count) for r, count in candidates if _created_before(r, event)]
        if not eligible:
            continue
        if len(eligible) != 1:
            if any(r.get('refundStatus') == 'CANCELLED' or r.get('shipmentStatus') == 'CANCELLED'
                   for r, _ in eligible):
                raise ValueError(f'Нельзя однозначно связать финансовый возврат с заявкой: заказ {key[0]}, артикул {key[1]}')
            # No cancellation to apply. Retain the financially proven units
            # without guessing an ID; a later cancellation must be checked.
            continue
        row, _ = eligible[0]
        identity = (row['campaign_id'], row['id'])
        used[(*identity, *key[:2])] += 1
        links[key] = (*identity, row.get('refundStatus') == 'CANCELLED'
                      or row.get('shipmentStatus') == 'CANCELLED')
    return links


def sync(store_slug, business_id, api_key, campaign_ids, now, *, current_day: date | None = None):
    """Check cancellations for recent financial refunds with targeted GETs."""
    from app.repositories.yandex_financial import buyer_refund_events
    from app.yandex import api
    conn = get_connection()
    try:
        saved = read(conn, store_slug, business_id)
        events = buyer_refund_events(store_slug, business_id, '0001-01-01', '9999-12-31',
                                     source=conn, pinned=False)
        units = merge(events, saved)
    finally:
        conn.close()
    cutoff = ((current_day or datetime.now(ZoneInfo('Europe/Moscow')).date()) - timedelta(days=90)).isoformat()
    recent = {key: e for key, e in units.items() if e['refund_day'] >= cutoff}
    order_ids = sorted({int(key[0]) for key in recent})
    returns = []
    if order_ids:
        campaigns = [r for r in api.get_campaigns(api_key)
                     if int((r.get('business') or {}).get('id') or 0) == business_id
                     and (not campaign_ids or int(r['id']) in campaign_ids)]
        if not campaigns:
            raise ValueError('Нет магазина нужного кабинета для проверки отмены возвратов')
        for campaign in campaigns:
            for offset in range(0, len(order_ids), 50):
                returns.extend({**r, 'campaign_id': int(campaign['id'])} for r in
                    api.get_returns(api_key, int(campaign['id']), order_ids[offset:offset + 50]))
    links = cancellation_links(recent, saved, returns, incoming=split_units(events))
    # Only persist after all API pages succeeded. Missing responses cannot
    # silently cancel goods or overwrite a known cancellation.
    with WRITE_LOCK:
        conn = get_connection()
        try:
            conn.execute(SCHEMA)
            latest = read(conn, store_slug, business_id)
            previous = {(r['order_id'], r['shop_sku'], r['unit_index']): r for r in latest}
            _write(conn, store_slug, business_id, merge(events, latest), previous, links, now)
            conn.commit()
            stored = read(conn, store_slug, business_id)
        finally:
            conn.close()
    return visible(events, stored, '0001-01-01', '9999-12-31')
