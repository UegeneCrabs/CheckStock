import io
from html.parser import HTMLParser

import openpyxl
import pytest

from app import db, stock_total
from app.web import middleware

NOW = "2026-09-11T12:00:00+03:00"


def add_item(store, market, article, barcode, quantity=0, price=None, synced_at=NOW, **extra):
    db.replace_catalog(
        store, market, [{"article": article, "barcode": barcode, "name": article, **extra}], NOW
    )
    db.upsert_ff_stock(store, article, "ФулСервис Подольск", quantity, NOW, market)
    if price is not None:
        with db.get_connection() as connection:
            item_id = connection.execute(
                "SELECT id FROM stock_items WHERE store_slug=? AND marketplace=? AND article=?",
                (store, market, article),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO unit_economics_1c_source_values "
                "(stock_item_id,purchase_price,source_sheet_id,source_sheet_title,source_row,synced_at) "
                "VALUES (?,?,1,'Test',2,?)",
                (item_id, price, synced_at),
            )
            connection.commit()


def test_global_total_uses_maximum_price_even_from_zero_stock_and_store_filter(database_path):
    add_item("rockkiddo", "OZON", "303077583", "204193858779", 106, 90, "2026-09-10T12:00:00+03:00")
    add_item("toyka", "WB", "303077583", "2041938585779", 412, 100)
    add_item("tris", "WB", "303077583", "2041938585779", 0, 125, "2026-09-09T10:00:00+00:00")
    rows = stock_total.build_rows(("rockkiddo", "toyka", "tris"))
    assert len(rows) == 1
    row = rows[0]
    assert row["grand_total"] == 518
    assert row["total_wb"] == 412
    assert row["total_ozon"] == 106
    assert row["purchase_price"] == 125
    assert row["store_marketplaces"] == "ROCKKIDDO OZON, TOYKA WB"
    filtered = stock_total.build_rows(("rockkiddo", "toyka", "tris"), selected_store="rockkiddo")
    assert filtered[0]["grand_total"] == 106
    assert filtered[0]["purchase_price"] == 125
    assert filtered[0]["store_marketplaces"] == "ROCKKIDDO OZON"
    assert stock_total.build_rows(("rockkiddo", "toyka", "tris"), selected_store="tris") == []


def test_scope_excludes_hidden_stocks_prices_and_alias_bridges(database_path):
    add_item("rockkiddo", "OZON", "OZ", "001", 10, 100)
    add_item("toyka", "WB", "WB", "002", 20, 200)
    add_item("tris", "WB", "HIDDEN", "001", 99, 999, barcodes=["001", "002"])
    rows = stock_total.build_rows(("rockkiddo", "toyka", "tris"), (("rockkiddo", "OZON"), ("toyka", "WB")))
    assert len(rows) == 2
    assert sorted(row["purchase_price"] for row in rows) == [100, 200]
    assert sum(row["grand_total"] for row in rows) == 30
    assert all("TRIS" not in row["store_marketplaces"] for row in rows)


def test_different_articles_stay_separate_despite_shared_barcode(database_path):
    add_item("rimili", "WB", "WB", "04615526270026", 7, barcodes=["04615526270026", "2050292584830"])
    add_item("trusthome", "OZON", "OZ", "2050292584830", 9, 50)
    rows = stock_total.build_rows(("rimili", "trusthome"))
    assert len(rows) == 2
    assert sum(row["grand_total"] for row in rows) == 16
    assert next(row for row in rows if row["article"] == "WB")["purchase_price"] is None
    from app.web.routers.stock_total import _render_rows

    assert 'data-search-aliases="WB 04615526270026 2050292584830"' in _render_rows(rows)


def test_articles_without_barcodes_are_merged_and_all_zero_rows_disappear(database_path):
    add_item("rockkiddo", "OZON", "SAME", "", 3)
    add_item("toyka", "WB", "SAME", "", 4)
    add_item("tris", "WB", "ZERO", "009", 0, 100)
    rows = stock_total.build_rows(("rockkiddo", "toyka", "tris"))
    assert len(rows) == 1
    assert rows[0]["grand_total"] == 7
    assert all(row["barcode"] == "" for row in rows)


def test_opposite_quantities_are_not_mistaken_for_no_stock(monkeypatch):
    catalog = [
        {"store_slug": store, "marketplace": "WB", "article": "SAME", "barcode": "001"}
        for store in ("rimili", "tris")
    ]
    stocks = [{**item, "quantity": value} for item, value in zip(catalog, [5, -5], strict=True)]
    monkeypatch.setattr(stock_total.repository, "get_source_rows", lambda _: (catalog, [], stocks, []))
    rows = stock_total.build_rows(("rimili", "tris"))
    assert len(rows) == 1
    assert rows[0]["grand_total"] == 0
    assert rows[0]["store_marketplaces"] == "RIMILI WB, TRIS WB"


class BodyRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = False
        self.rows = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "tbody":
            self.in_body = True
        if self.in_body and tag == "tr":
            self.rows.append([])
        if self.in_body and tag == "td":
            self.rows[-1].append("")
            self.in_cell = True

    def handle_endtag(self, tag):
        if tag == "tbody":
            self.in_body = False
        if tag == "td":
            self.in_cell = False

    def handle_data(self, data):
        if self.in_body and self.in_cell:
            self.rows[-1][-1] += data


@pytest.mark.parametrize(
    "selected,expected,labels",
    [
        ("", 518, "ROCKKIDDO OZON, TOYKA WB"),
        ("rockkiddo", 106, "ROCKKIDDO OZON"),
    ],
)
def test_http_html_export_and_embedded_total_agree(
    application, client, user_factory, monkeypatch, selected, expected, labels
):
    add_item("rockkiddo", "OZON", "1046650397", "2051363797456", 106)
    add_item("toyka", "WB", "1046650397", "2051363797456", 412, 400)
    add_item("tris", "WB", "ZERO", "009", 0)
    monkeypatch.setattr(application.state.container.identity, "user_for_token", lambda _: user_factory())
    client.cookies.set(middleware.auth.SESSION_COOKIE, "total-global-test")
    response = client.get("/stock/total", params={"store": selected})
    assert response.status_code == 200
    parser = BodyRows()
    parser.feed(response.text)
    assert len(parser.rows) == 1
    cells = parser.rows[0]
    assert len(cells) == 24
    assert cells[1].strip() == "2051363797456"
    assert cells[3] == "400,00 ₽"
    assert cells[4] == labels
    assert int(cells[5]) == expected
    exported = client.get("/stock/total.xlsx", params={"store": selected})
    sheet = openpyxl.load_workbook(io.BytesIO(exported.content), data_only=True).active
    assert sheet.max_row == 5
    assert sheet["D5"].value == 400
    assert sheet["E5"].value == labels
    assert sheet["F5"].value == expected
    assert sheet["F4"].value == expected * 400
    embedded = client.get("/stock/rockkiddo/total-data").json()["rows"]
    assert len(embedded) == 1
    assert embedded[0]["grand_total"] == 106
    assert embedded[0]["purchase_price"] == 400
