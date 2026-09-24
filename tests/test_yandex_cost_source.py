from app.economics.sources import yandex


def test_yandex_cost_source_uses_export_prices_and_preserves_store_binding() -> None:
    report = yandex.parse_source_values(
        [
            {
                "sheet_id": 1,
                "title": "Для выгрузки",
                "rows": [["Артикул", "Себестоимость"], ["SKU-1", "123,45"]],
            }
        ],
        [
            {
                "store_slug": "tris",
                "article": "SKU-1",
                "purchase_price": 1,
                "manager": "Менеджер",
            },
            {"store_slug": "rimili", "article": "NOT-EXPORTED", "purchase_price": 2},
        ],
    )

    assert report["matched"] == 1
    assert report["unmatched"] == 1
    assert report["missing_articles"] == ["NOT-EXPORTED"]
    assert report["rows"] == [
        {
            "store_slug": "tris",
            "article": "SKU-1",
            "purchase_price": 123.45,
            "manager": "Менеджер",
            "source_sheet_id": 1,
            "source_sheet_title": "Для выгрузки",
            "source_row": 2,
        },
        {
            "store_slug": "rimili",
            "article": "NOT-EXPORTED",
            "purchase_price": None,
            "source_sheet_id": 1,
            "source_sheet_title": "Для выгрузки",
            "source_row": 0,
        }
    ]
