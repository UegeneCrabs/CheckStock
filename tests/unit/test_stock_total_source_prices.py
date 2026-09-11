from app import db, stock_total
from app.repositories import yandex_source_values

NOW = "2026-09-12T00:00:00+00:00"


def _catalog(store, marketplace, article, barcode):
    db.replace_catalog(store, marketplace, [
        {"article": article, "barcode": barcode, "name": article}
    ], NOW)


def _ym_prices(*values):
    yandex_source_values.replace_values([
        {"store_slug": store, "article": article, "purchase_price": price,
         "source_sheet_id": 1, "source_sheet_title": "YM", "source_row": index}
        for index, (store, article, price) in enumerate(values, 2)
    ], {}, NOW)


def test_total_reads_ym_price_when_wb_barcode_differs(database_path):
    _catalog("trusthome", "WB", "303077583", "2041938585779")
    _catalog("trusthome", "OZON", "303077583", "204193858779")
    _catalog("trusthome", "YANDEX MARKET", "303077583", "204193858779")
    wb_id = db.list_unit_economics_1c_active_wb_stock_items("trusthome")[0]["id"]
    db.replace_unit_economics_1c_source_values([
        {"stock_item_id": wb_id, "purchase_price": 848.59, "source_sheet_id": 2,
         "source_sheet_title": "TRUSTHOME WB", "source_row": 52}
    ], {}, NOW)
    _ym_prices(("trusthome", "303077583", 848.6))
    db.upsert_mp_stock("trusthome", "303077583", "OZON", "fbs", 547, NOW)
    db.upsert_mp_stock("trusthome", "303077583", "YANDEX MARKET", "fbs", 23, NOW)
    rows = {r["barcode"]: r for r in stock_total.build_rows(("trusthome",))}
    assert len(rows) == 2
    assert rows["2041938585779"]["purchase_price"] == 848.59
    assert rows["204193858779"]["purchase_price"] == 848.6
    assert rows["204193858779"]["grand_total"] == 570
    from app.web.routers.stock_total import _render_rows, _render_totals
    target = rows["204193858779"]
    assert "848,60 ₽" in _render_rows([target])
    assert "483\xa0702,00 ₽" in _render_totals([target])


def test_total_ym_prices_respect_store_marketplace_and_access(database_path):
    _catalog("trusthome", "YANDEX MARKET", "shared", "ym-barcode")
    _catalog("trusthome", "OZON", "shared", "different-ozon-barcode")
    _catalog("rimili", "YANDEX MARKET", "shared", "ym-barcode")
    _ym_prices(("trusthome", "shared", 848.6), ("rimili", "shared", 100))
    db.upsert_mp_stock("trusthome", "shared", "OZON", "fbs", 1, NOW)
    rows = [row for row in stock_total.build_rows(("trusthome", "rimili")) if row["article"] == "shared"]
    assert {(r["store_slug"], r["barcode"]): r["purchase_price"] for r in rows} == {
        ("trusthome", "ym-barcode"): 848.6,
        ("trusthome", "different-ozon-barcode"): None,
        ("rimili", "ym-barcode"): 100,
    }
    limited = stock_total.build_rows(("trusthome",), (("trusthome", "OZON"),))
    assert len(limited) == 1
    assert limited[0]["purchase_price"] is None


def test_total_ym_price_refresh_preserves_zero_and_missing_values(database_path):
    _catalog("trusthome", "YANDEX MARKET", "shared", "barcode")
    for price in (848.6, 900, 0, None):
        _ym_prices(("trusthome", "shared", price))
        rows = stock_total.build_rows(("trusthome",))
        if price is None:
            assert rows == []
        else:
            assert len(rows) == 1
            assert rows[0]["purchase_price"] == price
