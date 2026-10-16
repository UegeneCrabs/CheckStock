"""Purchase prices are identified by date, store and article, not article alone."""
from __future__ import annotations

import json
from collections import defaultdict

STORE_SHEET_MARKERS = ('rimili', 'tris', 'rockkiddo', 'trusthome', 'sokoloff', 'gogol', 'toyka')

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_cost_conflicts (
    cost_date TEXT NOT NULL,
    store_slug TEXT NOT NULL,
    article TEXT NOT NULL,
    values_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (cost_date, store_slug, article)
);
"""


def stores_for_sheet(sheet_name: str) -> list[str]:
    name = sheet_name.lower()
    return [store for store in STORE_SHEET_MARKERS if store in name]


def split_costs_by_store(rows, legacy_conflicts):
    """Resolve duplicates only within the same store (or unscoped source).

    The existing workbook reader preserves differing source values in its
    conflicts list. A shared tab explicitly assigns its price to both stores.
    Known store tabs never produce a global price for unrelated stores.
    """
    grouped = defaultdict(list)
    for row in [*rows, *(v for conflict in legacy_conflicts for v in conflict['values'])]:
        for store in stores_for_sheet(row['sheet_name']) or ['']:
            grouped[store, row['article']].append(row)
    global_rows, store_rows, conflicts = [], [], []
    for (store, article), values in sorted(grouped.items()):
        if len({v['cost'] for v in values}) != 1:
            conflicts.append({'store_slug': store, 'article': article, 'values': values})
            continue
        row = {'article': article, 'cost': values[0]['cost'],
               'sheet_name': ', '.join(sorted({v['sheet_name'] for v in values}))}
        if store:
            store_rows.append({**row, 'store_slug': store})
        else:
            global_rows.append(row)
    return global_rows, store_rows, conflicts


def write_store_costs(conn, cost_date, rows, conflicts, now):
    """Write one Google snapshot in the caller's transaction; keep explicit edits."""
    inserted = updated = unchanged = 0
    for row in rows:
        key = (cost_date, row['store_slug'], row['article'])
        old = conn.execute('SELECT cost,source,sheet_name FROM store_product_costs '
                           'WHERE cost_date=? AND store_slug=? AND article=?', key).fetchone()
        if old and (old['source'] in {'manual', 'excel'} or
                    (old['cost'] == row['cost'] and old['sheet_name'] == row['sheet_name'])):
            unchanged += 1
            continue
        conn.execute('''INSERT INTO store_product_costs
            (cost_date,store_slug,article,cost,source,sheet_name,imported_at) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(cost_date,store_slug,article) DO UPDATE SET
                cost=excluded.cost,source=excluded.source,sheet_name=excluded.sheet_name,
                imported_at=excluded.imported_at''',
            (*key, row['cost'], 'google', row['sheet_name'], now))
        updated += bool(old)
        inserted += not bool(old)
    # A conflict must also block an older price or an unrelated global fallback.
    conn.execute('DELETE FROM store_cost_conflicts WHERE cost_date=?', (cost_date,))
    conn.executemany('INSERT INTO store_cost_conflicts VALUES(?,?,?,?,?)',
        [(cost_date, row['store_slug'], row['article'],
          json.dumps(row['values'], ensure_ascii=False), now) for row in conflicts])
    return {'store_inserted': inserted, 'store_updated': updated, 'store_unchanged': unchanged}


def prices_for_day(conn, store, day):
    blocked = {(r['store_slug'], r['article']) for r in conn.execute(
        'SELECT store_slug,article FROM store_cost_conflicts WHERE cost_date=?', (day,))}
    prices = {str(r['article']): float(r['cost']) for r in conn.execute(
        'SELECT article,cost FROM store_product_costs WHERE store_slug=? AND cost_date=?', (store, day))
        if (store, r['article']) not in blocked}
    for row in conn.execute('SELECT article,cost,source,sheet_name FROM product_costs WHERE cost_date=?', (day,)):
        article = str(row['article'])
        if (store, article) in blocked or ('', article) in blocked:
            continue
        # Compatibility with older snapshots: a Google price from another
        # store's tab is never a valid fallback. Manual/Excel unscoped prices
        # retain their existing fallback behaviour.
        assigned = stores_for_sheet(row['sheet_name'] or '')
        if row['source'] == 'google' and assigned and store not in assigned:
            continue
        prices.setdefault(article, float(row['cost']))
    return prices
