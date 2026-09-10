import pytest

from app import db, stock_total
from app import unit_economics_1c_source_data as source
from tests.unit.test_unit_economics_1c_source_data import NOW, _row, _sheet


def price_sheet(article="303077583", purchase="848,59"):
    return _sheet(
        "SOKOLOFF + TRUSTHOME WB",
        1248315136,
        [_row(article, tag="", external="", purchase=purchase, fulfillment="100", commission="4")],
    )


def values():
    with db.get_connection() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT s.marketplace,s.barcode,p.* FROM stock_items s "
                "JOIN unit_economics_1c_source_values p ON p.stock_item_id=s.id ORDER BY s.marketplace"
            ).fetchall()
        ]


def test_all_marketplace_purchase_prices_and_total_use_article_with_different_barcodes(database_path):
    for market, barcode, quantity in [
        ("WB", "2041938585779", 10),
        ("OZON", "204193858779", 20),
        ("YANDEX MARKET", "204193858779", 30),
    ]:
        db.replace_catalog(
            "trusthome", market, [{"article": "303077583", "barcode": barcode, "name": "Смеситель"}], NOW
        )
        db.upsert_ff_stock("trusthome", "303077583", "ФулСервис Подольск", quantity, NOW, market)
    report = source.sync_all([price_sheet()])
    assert report["purchase_prices_saved"] == 3
    assert report["saved"] == 3
    assert report["unmatched_price_rows"] == []
    rows = values()
    assert len(rows) == 3
    assert all(row["purchase_price"] == 848.59 for row in rows)
    assert all(row["fulfillment_cost"] is None for row in rows if row["marketplace"] != "WB")
    assert all(row["team_commission_percent"] is None for row in rows if row["marketplace"] != "WB")
    assert next(row for row in rows if row["marketplace"] == "WB")["fulfillment_cost"] == 100
    total = stock_total.build_rows(("trusthome",))
    assert len(total) == 1
    assert total[0]["article"] == "303077583"
    assert total[0]["grand_total"] == 60
    assert total[0]["purchase_price"] == 848.59
    assert total[0]["store_marketplaces"] == "TRUSTHOME WB, TRUSTHOME OZON, TRUSTHOME ЯМ"
    source.sync_all([price_sheet(purchase="900")])
    assert all(row["purchase_price"] == 900 for row in values())


def test_price_loads_without_a_wb_card_and_does_not_match_other_stores(database_path):
    for store in ("trusthome", "rimili"):
        db.replace_catalog(store, "OZON", [{"article": "303077583", "barcode": "111", "name": "Товар"}], NOW)
    report = source.sync_all([price_sheet()])
    assert report["saved"] == 1
    assert report["purchase_prices_saved"] == 1
    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT s.store_slug,p.purchase_price FROM stock_items s "
            "JOIN unit_economics_1c_source_values p ON p.stock_item_id=s.id"
        ).fetchone()
    assert row["store_slug"] == "trusthome"
    assert row["purchase_price"] == 848.59


def test_conflicting_source_prices_preserve_previous_snapshot(database_path):
    db.replace_catalog("trusthome", "OZON", [{"article": "303077583", "name": "Товар"}], NOW)
    source.sync_all([price_sheet()])
    with pytest.raises(source.SourceDataError, match="Разные ЗЦ"):
        source.sync_all([price_sheet(purchase="100"), price_sheet(purchase="200")])
    assert values()[0]["purchase_price"] == 848.59
