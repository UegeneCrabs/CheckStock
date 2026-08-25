import io

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import db, stock_total
from app.container import ApplicationContainer
from app.dto.identity import User
from app.web import middleware

NOW = "2026-08-21T12:00:00+03:00"
FULFILLMENT = "ФулСервис Подольск"


def _catalog_item(article: str, barcode: str, name: str) -> dict:
    return {"article": article, "barcode": barcode, "name": name}


def test_total_stock_merges_marketplaces_by_barcode_and_keeps_stores_separate(database_path) -> None:
    del database_path
    db.replace_catalog(
        "rimili",
        "WB",
        [_catalog_item("WB-ARTICLE", "2200000000001", "Общий товар")],
        NOW,
    )
    db.replace_catalog(
        "rimili",
        "OZON",
        [
            _catalog_item("OZON-ARTICLE", "2200000000001", "Общий товар Ozon"),
            _catalog_item("OZON-ZERO", "", "Товар без штрихкода"),
        ],
        NOW,
    )
    db.replace_catalog(
        "rimili",
        "YANDEX MARKET",
        [_catalog_item("YANDEX-ARTICLE", "2200000000001", "Общий товар Яндекс")],
        NOW,
    )
    db.replace_catalog(
        "tris",
        "WB",
        [_catalog_item("TRIS-ARTICLE", "2200000000001", "Такой же штрихкод, другой магазин")],
        NOW,
    )

    db.upsert_ff_stock("rimili", "WB-ARTICLE", FULFILLMENT, 2, NOW, "WB")
    db.upsert_ff_stock("rimili", "OZON-ARTICLE", FULFILLMENT, 4, NOW, "OZON")
    db.upsert_mp_stock("rimili", "WB-ARTICLE", "WB", "fbs", 7, NOW)
    db.upsert_mp_stock("rimili", "OZON-ARTICLE", "OZON", "rfbs", 3, NOW)
    db.upsert_mp_stock("rimili", "YANDEX-ARTICLE", "YANDEX MARKET", "fbo", 5, NOW)
    db.upsert_mp_stock("tris", "TRIS-ARTICLE", "WB", "fbo", 11, NOW)

    rows = stock_total.build_rows(("rimili", "tris"))
    shared = next(row for row in rows if row["store_slug"] == "rimili" and row["barcode"] == "2200000000001")
    tris = next(row for row in rows if row["store_slug"] == "tris" and row["barcode"] == "2200000000001")
    zero = next(row for row in rows if row["article"] == "OZON-ZERO")

    assert shared["article"] == "WB-ARTICLE"
    assert shared["ff_wb"] == 2
    assert shared["ff_ozon"] == 4
    assert shared["fbs_wb"] == 7
    assert shared["rfbs_ozon"] == 3
    assert shared["fbo_yandex"] == 5
    assert shared["grand_total"] == 21
    assert tris["grand_total"] == 11
    assert zero["grand_total"] == 0
    assert all(row["grand_total"] >= rows[index + 1]["grand_total"] for index, row in enumerate(rows[:-1]))


def test_total_stock_xlsx_has_store_column_grouped_headers_and_zeroes(database_path) -> None:
    del database_path
    rows = [
        {
            "store_slug": "rimili",
            "store_name": "RIMILI",
            "article": "ARTICLE-1",
            "barcode": "0012345678901",
            "name": "Тестовый товар",
            "grand_total": 9,
            **{key: (9 if key == "fbs_wb" else 0) for key in stock_total.QUANTITY_KEYS},
        }
    ]

    content, filename = stock_total.build_xlsx(rows)
    workbook = openpyxl.load_workbook(io.BytesIO(content), data_only=False)
    sheet = workbook["Остатки Тотал"]

    assert filename.startswith("ostatki_total_")
    assert sheet["A1"].value == "МАГАЗИН"
    assert sheet["E1"].value == "ГРАНД ТОТАЛ"
    assert sheet["F1"].value == "ДОСТУПНО ФФ ДЛЯ РАСПРЕДЕЛЕНИЯ"
    assert sheet["I1"].value == "ТЕКУЩИЙ СТОК В ПРОДАЖЕ FBS"
    assert sheet["L1"].value == "ТЕКУЩИЙ СТОК В ПРОДАЖЕ RFBS"
    assert sheet["O1"].value == "ТЕКУЩИЙ СТОК В ПРОДАЖЕ FBO"
    assert {str(item) for item in sheet.merged_cells.ranges} >= {
        "A1:A2",
        "E1:E2",
        "F1:H1",
        "I1:K1",
        "L1:N1",
        "O1:Q1",
    }
    assert sheet["A4"].value == "RIMILI"
    assert sheet["B4"].value == "ARTICLE-1"
    assert sheet["C4"].value == "0012345678901"
    assert sheet["E4"].value == 9
    assert sheet["F4"].value == 0
    assert sheet["I4"].value == 9
    assert sheet.freeze_panes == "F4"


def test_total_routes_resolve_before_the_dynamic_store_route(
    application: FastAPI,
    client: TestClient,
    user_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user: User = user_factory()
    container: ApplicationContainer = application.state.container
    monkeypatch.setattr(container.identity, "user_for_token", lambda _token: user)
    client.cookies.set(middleware.auth.SESSION_COOKIE, "stock-total-test")

    page = client.get("/stock/total")
    download = client.get("/stock/total.xlsx")

    assert page.status_code == 200
    assert "Остатки Тотал" in page.text
    assert 'id="stock-total-table"' in page.text
    assert download.status_code == 200
    assert download.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
