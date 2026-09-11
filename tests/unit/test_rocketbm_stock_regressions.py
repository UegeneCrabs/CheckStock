from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError

from app import db, stock_total
from app.catalog_identity import CatalogIndex, CatalogMatchError
from app.fulfillment_names import fulfillment_lookup, normalize
from app.ozon import api as ozon_api
from app.ozon import sync as ozon_sync
from app.repositories import core
from app.repositories.stock_snapshot import replace_snapshot
from app.yandex import api as ya_api
from app.yandex import sync as ya_sync

NOW = "2026-09-11T00:00:00+00:00"


def test_explicit_article_wins_shared_barcode_and_conflicts_fail():
    index = CatalogIndex(
        [
            {"article": "415716412", "barcode": "2037607704121"},
            {"article": "151965108", "barcode": "2037607704121"},
            {"article": "other", "barcode": "0042"},
        ]
    )
    assert index.resolve("415716412", "2037607704121")["article"] == "415716412"
    with pytest.raises(CatalogMatchError, match="нескольким"):
        index.resolve(barcode="2037607704121")
    with pytest.raises(CatalogMatchError, match="разным"):
        index.resolve("415716412", "0042")
    assert index.resolve_code("0042")["article"] == "other"
    assert index.resolve_code("42") is None


def test_changed_primary_retains_all_barcodes_and_search(database_path):
    db.replace_catalog("rimili", "WB", [{"article": "949558341", "barcode": "2050292584830"}], NOW)
    db.replace_catalog(
        "rimili",
        "WB",
        [{"article": "949558341", "barcode": "04615526270026", "barcodes": ["04615526270026", "000123"]}],
        NOW,
    )
    item = db.get_catalog_items("rimili", "WB")[0]
    assert set(item["barcodes"]) == {"04615526270026", "2050292584830", "000123"}
    assert db.search_catalog("rimili", "000123")[0]["article"] == "949558341"
    assert db.search_catalog("rimili", "2050292584830")[0]["article"] == "949558341"


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("FBS Afflatus", "AFFLATUS Купавна"),
        ("FBS Аффлатус Купавна", "AFFLATUS Купавна"),
        ("FBS ФуллСервис Подольск", "ФулСервис Подольск"),
        ("FBS Казань Царицыно", "ФФ Царицыно Казань"),
        ("FBS Екатеринбург", "ФФ GO Екатеринбург"),
    ],
)
def test_warehouse_aliases_are_explicit(alias, canonical):
    lookup = fulfillment_lookup([canonical])
    assert lookup[normalize(alias)] == canonical
    assert normalize("Неизвестная Казань") not in lookup


def test_fbs_allocation_debits_free_stock_once_and_sales_do_not_release_it(container):
    from app.dto.marketplace import Marketplace
    from app.dto.stock import ShipmentCommand, SignedStockEntries, SignedStockEntry
    from app.errors import StockValidationError

    db.replace_catalog("rimili", "OZON", [{"article": "A", "barcode": "001"}], NOW)
    db.upsert_ff_stock("rimili", "A", "AFFLATUS Купавна", 220, NOW, "OZON")
    command = ShipmentCommand(
        store_slug="rimili",
        marketplace=Marketplace.OZON,
        fulfillment="AFFLATUS Купавна",
        entries=SignedStockEntries((SignedStockEntry(code="A", quantity=139),)),
    )
    container.stock.register_fbs_transfer(command)
    replace_snapshot(
        "rimili",
        "OZON",
        {"fbs": {"A": 139}, "fbo": {"A": 10}},
        {"fbs": [("A", "AFFLATUS Купавна", None, 139)]},
        NOW,
    )
    assert db.get_ff_available_totals("rimili", "AFFLATUS Купавна", "OZON")["A"] == 81
    assert db.get_stock_items("rimili", "OZON")[0]["ff_available"] == 81
    result = stock_total.build_rows(("rimili",))[0]
    assert result["grand_total"] == 230
    with pytest.raises(StockValidationError, match="Недостаточно"):
        container.stock.register_fbs_transfer(command)
    replace_snapshot(
        "rimili", "OZON", {"fbs": {"A": 120}}, {"fbs": [("A", "AFFLATUS Купавна", None, 120)]}, NOW
    )
    assert db.get_ff_available_totals("rimili", "AFFLATUS Купавна", "OZON")["A"] == 81
    assert stock_total.build_rows(("rimili",))[0]["grand_total"] == 211
    with pytest.raises(StockValidationError, match="мусорку"):
        container.stock.register_fbs_transfer(command.model_copy(update={"to_trash": True}))


def test_total_keeps_distinct_articles_with_shared_legacy_barcode_separate(database_path):
    db.replace_catalog(
        "rimili", "WB", [{"article": "A", "barcode": "04615526270026", "barcodes": ["2050292584830"]}], NOW
    )
    db.replace_catalog("rimili", "OZON", [{"article": "B", "barcode": "2050292584830"}], NOW)
    db.upsert_ff_stock("rimili", "A", "FF", 2, NOW, "WB")
    db.upsert_mp_stock("rimili", "B", "OZON", "fbo", 3, NOW)
    rows = stock_total.build_rows(("rimili",))
    assert len(rows) == 2
    assert {row["article"]: row["grand_total"] for row in rows} == {"A": 2, "B": 3}
    assert "2050292584830" in next(row for row in rows if row["article"] == "A")["barcodes"]


def test_snapshot_rolls_back_totals_and_details_together(database_path):
    replace_snapshot("rimili", "OZON", {"fbs": {"A": 2}}, {"fbs": [("A", "FF", None, 2)]}, NOW)
    with pytest.raises(IntegrityError):
        replace_snapshot("rimili", "OZON", {"fbs": {"A": 99}}, {"fbs": [("A", None, None, 99)]}, NOW)
    assert db.get_mp_stock_totals("rimili", "OZON", "fbs") == {"A": 2}
    assert db.get_mp_stock_by_warehouse("rimili", "OZON", "fbs", "FF") == {"A": 2}


def test_ozon_fbs_paginates_and_refuses_incomplete_snapshot():
    with patch.object(
        ozon_api,
        "_request",
        side_effect=[
            {"products": [{"offer_id": "A"}], "has_next": True, "cursor": "next"},
            {"products": [{"offer_id": "B"}], "has_next": False},
        ],
    ) as request:
        assert len(ozon_api.get_fbs_stock_by_warehouse("c", "k", ["1"])) == 2
        assert request.call_args.args[3]["cursor"] == "next"
    with patch.object(ozon_api, "_request", return_value={"products": [], "has_next": True}):
        with pytest.raises(ozon_api.OzonApiError):
            ozon_api.get_fbs_stock_by_warehouse("c", "k", ["1"])


def test_ozon_loads_seller_warehouse_and_matching_total(database_path, monkeypatch):
    db.replace_catalog("rockkiddo", "OZON", [{"article": "824852970", "barcode": "2049049633723"}], NOW)
    monkeypatch.setattr(ozon_sync.ozon_tokens, "get_credentials", lambda _: ("c", "k"))
    monkeypatch.setattr(
        ozon_api,
        "get_product_stocks",
        lambda *a: [{"offer_id": "824852970", "stocks": [{"type": "fbs", "sku": 123, "present": 140}]}],
    )
    monkeypatch.setattr(ozon_api, "get_fbo_stock_by_warehouse", lambda *a: [])
    monkeypatch.setattr(ozon_api, "get_own_warehouses", lambda *a: [{"warehouse_id": 1, "is_rfbs": False}])
    monkeypatch.setattr(
        ozon_api,
        "get_fbs_stock_by_warehouse",
        lambda *a: [
            {
                "offer_id": "824852970",
                "sku": 123,
                "warehouse_id": 1,
                "warehouse_name": "FBS Afflatus Купавна",
                "present": 140,
                "free_stock": 139,
            }
        ],
    )
    ozon_sync.sync_store("rockkiddo")
    assert db.get_mp_stock_totals("rockkiddo", "OZON", "fbs") == {"824852970": 139}
    assert db.get_mp_stock_by_warehouse("rockkiddo", "OZON", "fbs", "AFFLATUS Купавна") == {"824852970": 139}


def _setup_yandex(monkeypatch):
    monkeypatch.setattr(ya_sync.ya_tokens, "get_api_key", lambda _: "key")
    monkeypatch.setattr(ya_sync, "_warehouse_names", lambda _: {305: "Маркет"})
    monkeypatch.setattr(
        ya_sync,
        "resolve_campaigns",
        lambda *a: [
            {"id": 1, "scheme_key": "fbo", "name": "FBY"},
            {"id": 2, "scheme_key": "fbs", "name": "FBS Екатеринбург"},
            {"id": 3, "scheme_key": "fbs", "name": "FBS Екатеринбург"},
        ],
    )


def test_yandex_disabled_duplicate_campaign_does_not_abort_active_ones(database_path, monkeypatch):
    db.replace_catalog("gogol", "YANDEX MARKET", [{"article": "1015852319", "barcode": "1"}], NOW)
    _setup_yandex(monkeypatch)

    def stocks(key, campaign):
        if campaign == 2:
            raise ya_api.YandexApiError(403, "API_DISABLED: API for campaign 2 manually disabled.")
        return [
            {"article": "1015852319", "warehouse_id": 305, "stocks": [{"type": "AVAILABLE", "count": 20}]}
        ]

    monkeypatch.setattr(ya_api, "get_stocks", stocks)
    ya_sync.sync_store("gogol")
    assert db.get_mp_stock_totals("gogol", "YANDEX MARKET", "fbo") == {"1015852319": 20}
    assert db.get_mp_stock_by_warehouse("gogol", "YANDEX MARKET", "fbs", "ФФ GO Екатеринбург") == {
        "1015852319": 20
    }


def test_yandex_failed_fbs_preserves_snapshot_but_updates_fbo(database_path, monkeypatch):
    db.replace_catalog("gogol", "YANDEX MARKET", [{"article": "A", "barcode": "1"}], NOW)
    replace_snapshot("gogol", "YANDEX MARKET", {"fbs": {"A": 7}}, {"fbs": [("A", "FF", None, 7)]}, NOW)
    _setup_yandex(monkeypatch)

    def stocks(key, campaign):
        if campaign == 2:
            raise ya_api.YandexApiError(503, "unavailable")
        return [{"article": "A", "warehouse_id": 305, "stocks": [{"type": "AVAILABLE", "count": 20}]}]

    monkeypatch.setattr(ya_api, "get_stocks", stocks)
    with pytest.raises(ya_api.YandexApiError, match="частично"):
        ya_sync.sync_store("gogol")
    assert db.get_mp_stock_totals("gogol", "YANDEX MARKET", "fbo") == {"A": 20}
    assert db.get_mp_stock_totals("gogol", "YANDEX MARKET", "fbs") == {"A": 7}
    assert db.get_mp_stock_by_warehouse("gogol", "YANDEX MARKET", "fbs", "FF") == {"A": 7}


def test_catalog_rename_conserves_balances_and_import_snapshot(database_path):
    old = {"article": "old", "barcode": "001", "mp_product_id": "123"}
    new = {**old, "article": "new"}
    db.replace_catalog("rockkiddo", "OZON", [old, new], NOW)
    with core.get_connection() as conn:
        for article, quantity in [("old", 40), ("new", 5)]:
            conn.execute(
                "INSERT INTO ff_stock(store_slug,marketplace,article,fulfillment,quantity) "
                "VALUES ('rockkiddo','OZON',?,'FF',?)",
                (article, quantity),
            )
            conn.execute(
                "INSERT INTO ff_import_snapshots(store_slug,marketplace,article,fulfillment,"
                "source_type,source_key,quantity,updated_at) "
                "VALUES ('rockkiddo','OZON',?,'FF','file','same-file',?,?)",
                (article, quantity, NOW),
            )
        conn.commit()
    report = db.replace_catalog("rockkiddo", "OZON", [new], NOW)
    assert report["reconciled"] == 1
    assert db.get_ff_stock_one("rockkiddo", "new", "FF", "OZON") == 45
    assert db.get_ff_stock_one("rockkiddo", "old", "FF", "OZON") == 0
    catalog = db.get_catalog_items("rockkiddo", "OZON")
    assert len(catalog) == 1
    assert CatalogIndex(catalog).resolve_code("old")["article"] == "new"
    with core.get_connection() as conn:
        snapshot = conn.execute("SELECT article,quantity FROM ff_import_snapshots").fetchall()
    assert [(row["article"], row["quantity"]) for row in snapshot] == [("new", 45)]
    db.replace_catalog("rockkiddo", "OZON", [new], NOW)
    assert db.get_ff_stock_one("rockkiddo", "new", "FF", "OZON") == 45


def test_yandex_distinct_live_offers_with_one_barcode_remain_separate(database_path):
    offers = [
        {"article": "numeric", "barcode": "001", "mp_sku": "123"},
        {"article": "text", "barcode": "001", "mp_sku": "456"},
    ]
    db.replace_catalog("sokoloff", "YANDEX MARKET", offers, NOW)
    result = db.replace_catalog("sokoloff", "YANDEX MARKET", offers, NOW)
    assert result["reconciled"] == 0
    assert len(db.get_catalog_items("sokoloff", "YANDEX MARKET")) == 2


def test_wb_chrt_id_request_does_not_count_barcode_aliases_twice(database_path, monkeypatch):
    from app.wb import api as wb_api
    from app.wb import sync as wb_sync

    db.replace_catalog("rimili", "WB", [{"article": "A", "barcode": "046", "barcodes": ["204"]}], NOW)
    monkeypatch.setattr(wb_sync.wb_tokens, "get_token", lambda _: "key")
    monkeypatch.setattr(
        wb_api, "get_cards_list", lambda *a: [{"sizes": [{"chrtID": 123, "skus": ["046", "204"]}]}]
    )
    monkeypatch.setattr(wb_api, "get_own_warehouses", lambda *a: [{"id": 1, "name": "AFFLATUS Купавна"}])
    with patch.object(wb_api, "_request", return_value={"stocks": [{"chrtId": 123, "amount": 7}]}) as request:
        wb_sync.sync_store_fbs("rimili")
    assert request.call_args.kwargs["json_body"] == {"chrtIds": [123]}
    assert db.get_mp_stock_totals("rimili", "WB", "fbs") == {"A": 7}
    assert db.get_mp_stock_by_warehouse("rimili", "WB", "fbs", "AFFLATUS Купавна") == {"A": 7}


def test_wb_partial_warehouse_failure_preserves_previous_snapshot(database_path, monkeypatch):
    from app.wb import api as wb_api
    from app.wb import sync as wb_sync

    db.replace_catalog("rimili", "WB", [{"article": "A", "barcode": "1"}], NOW)
    replace_snapshot("rimili", "WB", {"fbs": {"A": 9}}, {"fbs": [("A", "old", None, 9)]}, NOW)
    monkeypatch.setattr(wb_sync.wb_tokens, "get_token", lambda _: "key")
    monkeypatch.setattr(wb_api, "get_cards_list", lambda *a: [{"sizes": [{"chrtID": 123, "skus": ["1"]}]}])
    monkeypatch.setattr(
        wb_api, "get_own_warehouses", lambda *a: [{"id": 1, "name": "ok"}, {"id": 2, "name": "failed"}]
    )

    def stocks(token, warehouse, barcodes, **kwargs):
        if warehouse == 2:
            raise wb_api.WBApiError(503, detail="unavailable")
        return {"1": 3}

    monkeypatch.setattr(wb_api, "get_fbs_stock", stocks)
    with pytest.raises(wb_api.WBApiError, match="сохранены"):
        wb_sync.sync_store_fbs("rimili")
    assert db.get_mp_stock_totals("rimili", "WB", "fbs") == {"A": 9}
    assert db.get_mp_stock_by_warehouse("rimili", "WB", "fbs", "old") == {"A": 9}
