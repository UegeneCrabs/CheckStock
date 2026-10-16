import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

SITE = Path(__file__).resolve().parents[1] / 'rbe-yandex-site'
sys.path.insert(0, str(SITE))
from cost_snapshots import split_costs_by_store, write_store_costs, prices_for_day

spec = importlib.util.spec_from_file_location('rbe_store_costs_test', SITE / 'main.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)

DAY = date(2026, 10, 13)
SKU = '1015852320'


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'DATABASE_PATH', tmp_path / 'rbe.db')
    dashboard.init_database()
    with dashboard.connection() as conn:
        yield conn


def workbook(*sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        sheet = wb.create_sheet(name)
        sheet.append(['АртикулВБ', 'Себес, руб'])
        for row in rows:
            sheet.append(row)
    return wb


def run_snapshot(monkeypatch, wb, day=DAY):
    monkeypatch.setattr(dashboard, '_read_google_costs', lambda: dashboard._extract_costs_from_workbook(wb))
    return dashboard._run_cost_google_sync(day)


def test_real_gogol_example_saves_both_prices_and_shared_tab(db, monkeypatch):
    assert run_snapshot(monkeypatch, workbook(
        ('GOGOL WB', [(SKU, 466.36)]), ('ROCKKIDDO + TOYKA WB', [(SKU, 350.36)])))
    assert prices_for_day(db, 'gogol', str(DAY)) == {SKU: 466.36}
    assert prices_for_day(db, 'rockkiddo', str(DAY)) == {SKU: 350.36}
    assert prices_for_day(db, 'toyka', str(DAY)) == {SKU: 350.36}
    assert prices_for_day(db, 'tris', str(DAY)) == {}
    assert db.execute('SELECT COUNT(*) FROM product_costs').fetchone()[0] == 0
    run = db.execute('SELECT status,detail FROM cost_import_runs').fetchone()
    assert run['status'] == 'completed'
    assert json.loads(run['detail'])['store_inserted'] == 3
    assert json.loads(run['detail'])['conflicts'] == []


def test_equal_prices_also_remain_scoped(db, monkeypatch):
    run_snapshot(monkeypatch, workbook(('GOGOL WB', [(SKU, 10)]), ('TRIS WB', [(SKU, 10)])))
    assert prices_for_day(db, 'gogol', str(DAY)) == {SKU: 10}
    assert prices_for_day(db, 'tris', str(DAY)) == {SKU: 10}
    assert prices_for_day(db, 'rimili', str(DAY)) == {}


def test_same_store_conflict_blocks_old_price_and_fallback_then_recovers(db, monkeypatch):
    run_snapshot(monkeypatch, workbook(('GOGOL WB', [(SKU, 10)])))
    dashboard._write_costs(DAY, [{'article': SKU, 'cost': 15, 'sheet_name': None}], 'manual', 'tester')
    run_snapshot(monkeypatch, workbook(
        ('GOGOL WB', [(SKU, 11)]), ('GOGOL WB extra', [(SKU, 12)]), ('TRIS WB', [(SKU, 20)])))
    assert prices_for_day(db, 'gogol', str(DAY)) == {}
    assert prices_for_day(db, 'tris', str(DAY)) == {SKU: 20}
    assert db.execute('SELECT status FROM cost_import_runs ORDER BY id DESC').fetchone()[0] == 'partial'
    run_snapshot(monkeypatch, workbook(('GOGOL WB', [(SKU, 12)])))
    assert prices_for_day(db, 'gogol', str(DAY)) == {SKU: 12}
    assert db.execute('SELECT COUNT(*) FROM store_cost_conflicts').fetchone()[0] == 0


def test_snapshots_are_idempotent_and_date_specific(db, monkeypatch):
    wb = workbook(('GOGOL WB', [(SKU, 466.36)]))
    run_snapshot(monkeypatch, wb)
    run_snapshot(monkeypatch, wb)
    assert db.execute('SELECT COUNT(*) FROM store_product_costs').fetchone()[0] == 1
    assert json.loads(db.execute('SELECT detail FROM cost_import_runs ORDER BY id DESC').fetchone()[0])['store_unchanged'] == 1
    run_snapshot(monkeypatch, workbook(('GOGOL WB', [(SKU, 500)])), date(2026, 10, 14))
    assert prices_for_day(db, 'gogol', '2026-10-13') == {SKU: 466.36}
    assert prices_for_day(db, 'gogol', '2026-10-14') == {SKU: 500}
    assert prices_for_day(db, 'gogol', '2026-10-15') == {}


def test_legacy_fallback_cannot_leak_another_stores_google_price(db):
    dashboard._write_costs(DAY, [{'article': SKU, 'cost': 350.36, 'sheet_name': 'ROCKKIDDO + TOYKA WB'}], 'google', None)
    assert prices_for_day(db, 'gogol', str(DAY)) == {}
    assert prices_for_day(db, 'rockkiddo', str(DAY)) == {SKU: 350.36}
    dashboard._write_costs(DAY, [{'article': 'manual', 'cost': 5, 'sheet_name': None}], 'manual', 'tester')
    assert prices_for_day(db, 'gogol', str(DAY)) == {'manual': 5}


def test_unscoped_prices_and_explicit_edits_are_preserved(db, monkeypatch):
    dashboard._write_costs(DAY, [{'article': 'global', 'cost': 42, 'sheet_name': None}], 'manual', 'tester')
    db.execute('INSERT INTO store_product_costs VALUES(?,?,?,?,?,?,?)', (str(DAY), 'gogol', SKU, 77, 'excel', 'GOGOL WB', 'before'))
    db.commit()
    run_snapshot(monkeypatch, workbook(('General WB', [('global', 10)]), ('GOGOL WB', [(SKU, 20)])))
    assert prices_for_day(db, 'gogol', str(DAY)) == {SKU: 77, 'global': 42}


def test_failed_store_write_rolls_back_entire_snapshot(db, monkeypatch):
    def fail(*args):
        raise RuntimeError('test failure')
    monkeypatch.setattr(dashboard, 'write_store_costs', fail)
    assert not run_snapshot(monkeypatch, workbook(('General WB', [('global', 10)]), ('GOGOL WB', [(SKU, 20)])))
    assert db.execute('SELECT COUNT(*) FROM product_costs').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM cost_products').fetchone()[0] == 0
    assert db.execute('SELECT status FROM cost_import_runs').fetchone()[0] == 'retrying'


def test_costs_api_shows_store_for_each_variant(db, monkeypatch):
    run_snapshot(monkeypatch, workbook(('GOGOL WB', [(SKU, 466.36)]), ('ROCKKIDDO + TOYKA WB', [(SKU, 350.36)])))
    monkeypatch.setattr(dashboard, '_require_user', lambda _: {})
    response = dashboard.costs(None, DAY, query=SKU)
    assert {(r['store_slug'], r['cost']) for r in response['rows']} == {('gogol', 466.36), ('rockkiddo', 350.36), ('toyka', 350.36)}


def test_cogs_uses_selling_store_price_for_sales_and_returns(db, tmp_path, monkeypatch):
    run_snapshot(monkeypatch, workbook(('GOGOL WB', [(SKU, 466.36)]), ('ROCKKIDDO + TOYKA WB', [(SKU, 350.36)])))
    spec = importlib.util.spec_from_file_location('rbe_scoped_recalc_test', SITE / 'scripts' / 'recalculate_cost_of_goods.py')
    recalc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recalc)
    monkeypatch.setattr(recalc, 'connection', dashboard.connection)
    stores = ('gogol', 'rockkiddo')
    source_reports = {(store, 1): {'united-orders': {}, 'united-services': {'payment_transfer': [
        {'orderId': 1, 'shopSku': SKU, 'count': 3, 'serviceDate': str(DAY)}]}} for store in stores}
    monkeypatch.setattr(recalc, '_source_reports', lambda *_: source_reports)
    monkeypatch.setattr(recalc.yandex_financial, 'buyer_refund_events', lambda *a, **kw: [
        {'shopSku': SKU, 'count': 1, 'refund_day': str(DAY), 'refund_event_key': 'return-1'}])
    for store in stores:
        db.execute('INSERT INTO daily_financial VALUES(?,?,?,?,?)', (store, 1, str(DAY), json.dumps({'bank_receipt': 1234, 'sold_cost_of_goods': None}), 'original'))
    db.commit()
    result = recalc.recalculate(tmp_path / 'source.db', stores=stores, date_from=DAY, date_to=DAY)
    assert result == {'recalculated': 2, 'incomplete': 0, 'without_source': 0}
    for row in db.execute('SELECT * FROM daily_financial'):
        price = 466.36 if row['store_slug'] == 'gogol' else 350.36
        values = json.loads(row['values_json'])
        assert values['sold_cost_of_goods'] == round(2 * price, 2)
        assert values['returned_cost_of_goods'] == price
        assert values['bank_receipt'] == 1234
        assert row['calculated_at'] == 'original'


def test_historical_repair_uses_dated_log_not_current_prices(db, tmp_path):
    spec = importlib.util.spec_from_file_location('rbe_cost_repair_test', SITE / 'scripts' / 'repair_store_cost_snapshots.py')
    repair_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(repair_module)
    detail = {'scanned_articles': 1, 'conflicts': [{'article': SKU, 'values': [
        {'article': SKU, 'cost': 466.36, 'sheet_name': 'GOGOL WB'},
        {'article': SKU, 'cost': 350.36, 'sheet_name': 'ROCKKIDDO + TOYKA WB'}]}]}
    db.execute('INSERT INTO cost_import_runs(started_at,finished_at,cost_date,source,status,detail) VALUES(?,?,?,?,?,?)',
               ('old', 'old', str(DAY), 'google', 'partial', json.dumps(detail)))
    db.execute('INSERT INTO store_product_costs VALUES(?,?,?,?,?,?,?)', (str(DAY), 'toyka', SKU, 99, 'manual', None, 'old'))
    db.execute('INSERT INTO daily_financial VALUES(?,?,?,?,?)', ('gogol', 1, str(DAY), '{}', 'old'))
    db.commit()
    before = [tuple(r) for r in db.execute('SELECT * FROM daily_financial')]
    preview = repair_module.repair(dashboard.DATABASE_PATH, str(DAY), str(DAY))
    assert preview['missing_store_prices'] == 2
    assert not preview['applied']
    assert db.execute('SELECT COUNT(*) FROM store_product_costs').fetchone()[0] == 1
    applied = repair_module.repair(dashboard.DATABASE_PATH, str(DAY), str(DAY), apply=True)
    assert applied['missing_store_prices'] == 2
    assert prices_for_day(db, 'gogol', str(DAY)) == {SKU: 466.36}
    assert prices_for_day(db, 'rockkiddo', str(DAY)) == {SKU: 350.36}
    assert prices_for_day(db, 'toyka', str(DAY)) == {SKU: 99}
    assert repair_module.repair(dashboard.DATABASE_PATH, str(DAY), str(DAY), apply=True)['missing_store_prices'] == 0
    assert [tuple(r) for r in db.execute('SELECT * FROM daily_financial')] == before


def test_repair_refuses_truncated_history_without_writing(db):
    spec = importlib.util.spec_from_file_location('rbe_cost_repair_invalid_test', SITE / 'scripts' / 'repair_store_cost_snapshots.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db.execute('INSERT INTO cost_import_runs(started_at,cost_date,source,status,detail) VALUES(?,?,?,?,?)',
               ('old', str(DAY), 'google', 'partial', '{"scanned_articles":'))
    db.commit()
    with pytest.raises(ValueError):
        module.repair(dashboard.DATABASE_PATH, str(DAY), str(DAY), apply=True)
    assert db.execute('SELECT COUNT(*) FROM store_product_costs').fetchone()[0] == 0
