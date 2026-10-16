"""Remove legacy unscoped copies only after proving scoped calculations are unchanged.

Dry-run by default. Application must already require store-specific prices.
An exclusive new SQLite backup is mandatory for --apply. No Yandex requests,
no price inference, no historical recalculation, no database compaction.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cost_snapshots import STORE_SHEET_MARKERS, stores_for_sheet


def digest(conn, table):
    result = hashlib.sha256()
    for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid'):
        result.update(json.dumps(tuple(row), ensure_ascii=False).encode())
        result.update(b'\n')
    return result.hexdigest()


def check(conn):
    count = conn.execute('SELECT COUNT(*) FROM product_costs').fetchone()[0]
    for source, sheet in conn.execute('SELECT DISTINCT source,sheet_name FROM product_costs'):
        if source != 'google' or not stores_for_sheet(sheet or ''):
            raise ValueError('Found an unscoped price without a proven store copy; review required before deletion')
    for store in STORE_SHEET_MARKERS:
        missing = conn.execute("""SELECT COUNT(*) FROM product_costs p
            LEFT JOIN store_product_costs s ON s.cost_date=p.cost_date AND s.article=p.article AND s.store_slug=?
            WHERE instr(lower(p.sheet_name),?)>0 AND s.article IS NULL
            AND NOT EXISTS (SELECT 1 FROM store_cost_conflicts x WHERE x.cost_date=p.cost_date AND x.article=p.article AND x.store_slug=?)""",
            (store, store, store)).fetchone()[0]
        if missing:
            raise ValueError(f'{store}: {missing} prices lack a scoped copy; stopped')
    empty = conn.execute("SELECT COUNT(*) FROM store_product_costs WHERE length(trim(store_slug))=0").fetchone()[0]
    if empty:
        raise ValueError('Unexpected empty store in scoped table; review required')
    return count


def remove(database, *, apply=False, backup=None, expected_count=None):
    database = Path(database).resolve(strict=True)
    conn = sqlite3.connect(f'{database.as_uri()}?mode=rw' if apply else f'{database.as_uri()}?mode=ro', uri=True, timeout=30)
    try:
        count = check(conn)
        result = {'applied':False, 'unscoped_prices':count,
                  'unscoped_audit_rows':conn.execute('SELECT COUNT(*) FROM cost_changes').fetchone()[0]}
        if not apply:
            return result
        if expected_count is None or count != expected_count:
            raise ValueError('Unscoped count changed or was not explicitly confirmed')
        if not backup:
            raise ValueError('A backup path is required')
        backup = Path(backup).resolve()
        if backup == database or not backup.parent.is_dir():
            raise ValueError('Invalid backup destination')
        # Exclusive creation never overwrites an earlier backup.
        with backup.open('xb'):
            pass
        backup.chmod(0o600)
        with sqlite3.connect(backup) as target:
            conn.backup(target, pages=256, sleep=0.05)
            if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Backup verification failed')
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if check(conn) != count:
                raise ValueError('Prices changed during backup; stopped without deleting')
            protected = {table:digest(conn, table) for table in ('store_product_costs', 'daily_financial')}
            conn.execute('DELETE FROM product_costs')
            conn.execute('DELETE FROM cost_changes')
            conn.execute("DELETE FROM store_cost_conflicts WHERE length(trim(store_slug))=0")
            # Retired loaders must fail instead of silently recreating common prices.
            conn.execute("""CREATE TRIGGER IF NOT EXISTS no_unscoped_cost_insert BEFORE INSERT ON product_costs
                BEGIN SELECT RAISE(ABORT, 'Store-specific prices required'); END""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS no_unscoped_cost_update BEFORE UPDATE ON product_costs
                BEGIN SELECT RAISE(ABORT, 'Store-specific prices required'); END""")
            for operation in ('INSERT','UPDATE'):
                conn.execute(f"""CREATE TRIGGER IF NOT EXISTS store_cost_requires_store_{operation.lower()}
                    BEFORE {operation} ON store_product_costs WHEN length(trim(NEW.store_slug))=0
                    BEGIN SELECT RAISE(ABORT, 'Store required'); END""")
            assert all(digest(conn, table) == checksum for table, checksum in protected.items())
            assert conn.execute('SELECT COUNT(*) FROM product_costs').fetchone()[0] == 0
        result.update(applied=True, backup=str(backup), scoped_prices_unchanged=True, financial_rows_unchanged=True)
        return result
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    parser.add_argument('--expected-count', type=int)
    args = parser.parse_args()
    print(json.dumps(remove(args.database, apply=args.apply, backup=args.backup, expected_count=args.expected_count)))
