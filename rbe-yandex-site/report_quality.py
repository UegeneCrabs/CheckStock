"""Evidence for daily financial coverage, independent of calculated amounts.

An archive covering a requested period is not proof that its trailing dates
are ready. Keep unconfirmed trailing zeroes pending. An interior zero day is
valid when the same service report contains operations on a later day.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from hashlib import sha256
from math import isfinite


SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_source_quality (
    store_slug TEXT NOT NULL,
    business_id INTEGER NOT NULL,
    report_date TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    orders_complete INTEGER NOT NULL,
    financial_complete INTEGER NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (store_slug, business_id, report_date)
);
"""


def source_day(value):
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def financial_evidence(day, orders, services):
    """Do not infer a confirmed zero from absent service operations alone."""
    label = datetime.fromisoformat(day).strftime("%d.%m.%Y")
    if orders is None:
        return False, f"Нет подтверждённого отчёта по заказам за {label}"
    if services is None:
        return False, f"Нет подтверждённого отчёта по услугам за {label}"
    latest = max(services['days'], default=None)
    if latest is None or latest < day:
        until = datetime.fromisoformat(latest).strftime("%d.%m.%Y") if latest else "неизвестной даты"
        return False, (f"Нет финансовых операций за {label}. "
                       f"В отчёте по услугам данные только до {until}; нулевой день не подтверждён")
    if day in orders['deliveries'] and day not in services['payments']:
        return False, f"За {label} есть доставки, но ещё нет сведений об оплате выкупов"
    if day in services['payments']:
        expected = services.get('payment_items', {}).get(day, {})
        commissions = services.get('commission_items', {}).get(day, {})
        if not expected or any(not all(item) or commissions.get(item, 0) < quantity
                               for item, quantity in expected.items()):
            return False, f"За {label} есть выкупы, но комиссия получена не по всем товарам. Отсутствие данных не означает нулевую комиссию"
    return True, "Исходные отчёты проверены"


def build_quality(source, daily_rows):
    """Read archived reports once; never import application modules or write source DB.

    Use only snapshots available at the calculation time, independently for
    each cabinet and report kind. Later raw imports must not certify stale facts.
    """
    if not daily_rows:
        return []
    accounts = {(r['store_slug'], r['business_id']) for r in daily_rows}
    first = min(r['report_date'] for r in daily_rows)
    last = max(r['report_date'] for r in daily_rows)
    snapshots = {}
    for store, business, kind, start, end, imported, sheet, raw in source.execute(
        """SELECT store_slug,business_id,report_kind,period_from,period_to,imported_at,sheet,payload_json
           FROM yandex_financial_report_rows WHERE report_kind IN ('united-orders','united-services','united-netting')
           AND period_from<=? AND period_to>=?""", (last, first)
    ):
        if (store, business) not in accounts:
            continue
        key = (store, business, kind, start, end, imported)
        summary = snapshots.setdefault(key, {'days': set(), 'payments': set(), 'deliveries': set(),
                                            'payment_items': {}, 'commission_items': {}, 'seen': set()})
        # Keep a compact identity, not another full copy of every archived row.
        identity = (sheet, sha256(raw.encode('utf-8')).digest())
        if identity in summary['seen']:
            continue
        summary['seen'].add(identity)
        row = json.loads(raw)
        if kind == 'united-services':
            day = source_day(row.get('serviceDateTime') or row.get('serviceDate'))
            if day:
                summary['days'].add(day)
                if sheet == 'payment_transfer':
                    summary['payments'].add(day)
                if sheet in {'payment_transfer', 'placement'}:
                    item = (str(row.get('orderId') or ''), str(row.get('shopSku') or ''))
                    field = 'payment_items' if sheet == 'payment_transfer' else 'commission_items'
                    try:
                        amount = float(row['amountWithoutBonuses']) if sheet == 'placement' else 0
                        quantity = float(row.get('count') or row.get('quantity') or 1)
                    except (ValueError, TypeError, KeyError):
                        continue
                    if isfinite(amount) and isfinite(quantity) and quantity > 0:
                        items = summary[field].setdefault(day, {})
                        items[item] = items.get(item, 0) + quantity
        elif kind == 'united-netting' and sheet == 'transaction_date':
            day = source_day(row.get('transactionDate'))
            if day:
                summary['days'].add(day)
        elif sheet == 'orders_and_offers_transactions':
            day = source_day(row.get('deliveryDate'))
            if day:
                summary['deliveries'].add(day)
    grouped = defaultdict(list)
    for key, summary in snapshots.items():
        store, business, kind, start, end, imported = key
        grouped[store, business, kind].append((imported, end, start, summary))
    for candidates in grouped.values():
        candidates.sort(key=lambda x: x[:3], reverse=True)
    result = []
    for row in daily_rows:
        day = row['report_date']
        selected = {}
        for kind in ('united-orders', 'united-services', 'united-netting'):
            selected[kind] = next((summary for imported, end, start, summary
                in grouped[row['store_slug'], row['business_id'], kind]
                if start <= day <= end and imported <= row['calculated_at']), None)
        complete, detail = financial_evidence(day, selected['united-orders'], selected['united-services'])
        netting = selected['united-netting']
        # A successfully downloaded report covers its requested period, even
        # when no money operations occurred on a particular day. Only a missing
        # report is missing evidence; a quiet day is not a missing refund.
        if complete and netting is None:
            complete, detail = False, 'Нет полученного финансового отчёта для проверки возвратов за выбранный день'
        result.append((row['store_slug'], row['business_id'], day, row['calculated_at'],
                       int(selected['united-orders'] is not None), int(complete), detail))
    return result


def save_quality(target, rows):
    target.executemany("""INSERT INTO daily_source_quality VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(store_slug,business_id,report_date) DO UPDATE SET
        calculated_at=excluded.calculated_at,orders_complete=excluded.orders_complete,
        financial_complete=excluded.financial_complete,detail=excluded.detail""", rows)


def backfill(source_path, target_path):
    """Idempotent metadata-only migration; no amounts, dates or raw reports change."""
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source, sqlite3.connect(target_path) as target:
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        target.row_factory = sqlite3.Row
        target.executescript(SCHEMA)
        daily = target.execute('SELECT * FROM daily_financial').fetchall()
        rows = build_quality(source, daily)
        save_quality(target, rows)
    return {'checked': len(rows), 'incomplete': sum(not r[5] for r in rows)}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Backfill only RBE source-quality metadata')
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    print(json.dumps(backfill(args.source, args.target)))
