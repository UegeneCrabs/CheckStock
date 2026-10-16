"""Restore only missing store prices from dated, already stored Google imports.

No network calls, no today's-price backdating, no changes to financial facts.
Defaults to a read-only preview. Stop the cost importer and back up before
--apply; recalculate COGS separately for the affected stores/dates afterward.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cost_snapshots import SCHEMA, split_costs_by_store


def plan_repair(conn, start, end):
    runs = conn.execute('''SELECT cost_date,detail,finished_at FROM cost_import_runs
        WHERE source='google' AND status IN ('completed','partial')
        AND cost_date BETWEEN ? AND ? ORDER BY id''', (start, end)).fetchall()
    snapshots = {}
    for run in runs:
        # A damaged/truncated log cannot safely reconstruct its original prices.
        detail = json.loads(run['detail'])
        if 'scanned_articles' in detail:
            snapshots[run['cost_date']] = (detail, run['finished_at'])
    prices, conflicts = [], []
    for day, (detail, imported_at) in snapshots.items():
        if 'store_inserted' in detail:
            continue  # Already written by the new store-aware importer.
        global_rows = [dict(r) for r in conn.execute('''SELECT article,cost,sheet_name
            FROM product_costs WHERE cost_date=? AND source='google' ''', (day,))]
        _, scoped_rows, scoped_conflicts = split_costs_by_store(global_rows, detail.get('conflicts', []))
        for row in scoped_rows:
            key = (day, row['store_slug'], row['article'])
            if conn.execute('SELECT 1 FROM store_product_costs WHERE cost_date=? AND store_slug=? AND article=?', key).fetchone():
                continue  # Never overwrite an existing store-specific decision.
            prices.append((*key, row['cost'], 'google', row['sheet_name'], imported_at))
        conflicts.extend((day, row['store_slug'], row['article'], json.dumps(row['values'], ensure_ascii=False), imported_at)
                         for row in scoped_conflicts)
    return prices, conflicts


def repair(database, start, end, apply=False):
    path = Path(database).resolve().as_posix()
    with sqlite3.connect(f'file:{path}?mode={"rw" if apply else "ro"}', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if apply:
            conn.executescript(SCHEMA)
            conn.execute('BEGIN IMMEDIATE')
        else:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
        rows, conflicts = plan_repair(conn, start, end)
        if apply:
            conn.executemany('INSERT OR IGNORE INTO store_product_costs VALUES(?,?,?,?,?,?,?)', rows)
            conn.executemany('INSERT OR IGNORE INTO store_cost_conflicts VALUES(?,?,?,?,?)', conflicts)
    return {'applied': apply, 'missing_store_prices': len(rows), 'conflicts': len(conflicts),
            'affected': sorted({(r[0], r[1]) for r in rows})}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--from', dest='start', required=True, type=date.fromisoformat)
    parser.add_argument('--to', dest='end', required=True, type=date.fromisoformat)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.end < args.start:
        parser.error('Дата окончания раньше даты начала')
    print(json.dumps(repair(args.database, str(args.start), str(args.end), args.apply), ensure_ascii=False))
