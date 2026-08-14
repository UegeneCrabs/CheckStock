import logging
import unittest
from unittest import mock

from app.ozon import api as ozon_api
from app.ozon import catalog as ozon_catalog
from app.ozon import sync as ozon_sync
from app.wb import api as wb_api
from app.wb import catalog as wb_catalog
from app.wb import sync as wb_sync
from app.yandex import api as yandex_api
from app.yandex import catalog as yandex_catalog
from app.yandex import sync as yandex_sync


class WildberriesSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_catalog_helpers_and_sync(self) -> None:
        cards = [
            {
                "nmID": 10,
                "vendorCode": " Nice_Item ",
                "title": "Fallback",
                "updatedAt": "now",
                "photos": [{"big": "image"}],
                "sizes": [
                    {"techSize": "S", "skus": ["bc1"]},
                    {"techSize": "M", "skus": ["bc2"]},
                ],
                "tags": [{"name": "Old"}],
            },
            {"nmID": None, "sizes": [{"skus": ["x"]}]},
            {"nmID": 11, "sizes": []},
        ]
        self.assertEqual(wb_catalog.clean_name(" A__B "), "A B")
        self.assertTrue(wb_catalog.card_has_tag(cards[0], " old "))
        self.assertFalse(wb_catalog.card_has_tag(cards[0], "new"))
        self.assertEqual(wb_catalog.tagged_nm_ids(cards, "Old"), {"10"})
        self.assertEqual(wb_catalog.articles_for_nm_ids({"10 / S", "12"}, {"10"}), {"10 / S"})
        items, stats = wb_catalog.build_items(cards)
        self.assertEqual(len(items), 2)
        self.assertEqual(stats["multi_size"], 1)
        self.assertEqual(stats["no_article"], 1)
        self.assertEqual(stats["no_barcode"], 1)
        excluded, excluded_stats = wb_catalog.build_items(cards, "Old")
        self.assertEqual(excluded, [])
        self.assertEqual(excluded_stats["excluded_tag"], 1)

        normalized = {
            "nm_id": "10",
            "vendor_code": "A_10",
            "title": "Name",
            "updated_at": "now",
            "image_url": "image",
            "sizes": [{"tech_size": "", "barcode": "bc"}],
        }
        with (
            mock.patch.object(wb_catalog.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(wb_catalog.wb_api, "get_cards_list", return_value=[{"nmID": 10}]),
            mock.patch.object(wb_catalog.wb_api, "normalize_card", return_value=normalized),
            mock.patch.object(wb_catalog.db, "get_catalog_items", return_value=[{"article": "10 / old"}]),
            mock.patch.object(
                wb_catalog.db,
                "replace_catalog",
                return_value={"added": 1, "updated": 0, "removed": 1, "kept": 0},
            ) as replace,
        ):
            report = wb_catalog.sync_store("rimili")
        self.assertEqual(report["total"], 1)
        replace.assert_called_once()

        with (
            mock.patch.object(wb_catalog.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(wb_catalog.wb_api, "get_cards_list", return_value=[]),
        ):
            self.assertEqual(wb_catalog.sync_store("store")["total"], 0)
        with (
            mock.patch.object(wb_catalog.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(wb_catalog.wb_api, "get_cards_list", return_value=[{"nmID": 10}]),
            mock.patch.object(wb_catalog.wb_api, "normalize_card", return_value=normalized),
        ):
            self.assertTrue(wb_catalog.sync_store("store", apply=False)["dry_run"])

    def test_catalog_sync_all_success_and_failure(self) -> None:
        with (
            mock.patch.object(wb_catalog, "STORES", {"good": {}, "bad": {}, "none": {}}),
            mock.patch.object(wb_catalog.wb_tokens, "has_token", side_effect=lambda slug: slug != "none"),
            mock.patch.object(
                wb_catalog,
                "sync_store",
                side_effect=lambda slug: (
                    {"total": 1} if slug == "good" else (_ for _ in ()).throw(ValueError("boom"))
                ),
            ),
            mock.patch.object(wb_catalog.db, "record_sync_health") as health,
        ):
            report = wb_catalog.sync_all()
        self.assertTrue(report["good"]["ok"])
        self.assertFalse(report["bad"]["ok"])
        self.assertEqual(health.call_count, 2)
        self.assertIn("valueerror", wb_catalog._error_message(ValueError("x")).casefold())
        self.assertEqual(wb_catalog._store_label("unknown"), "UNKNOWN")

    def test_stock_sync_fbs_and_fbo(self) -> None:
        catalog = [
            {"article": "10", "barcode": "bc1"},
            {"article": "11", "barcode": "bc2"},
        ]
        warehouses = [{"id": 1, "name": " FF One "}, {"id": 2, "name": "Other"}]

        def fbs_stock(_token, warehouse_id, _barcodes):
            if warehouse_id == 1:
                return {"bc1": 2, "bc2": 1}
            return {"bc1": 3}

        with (
            mock.patch.object(wb_sync.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(wb_sync.db, "get_catalog_items", return_value=catalog),
            mock.patch.object(wb_sync.wb_api, "get_own_warehouses", return_value=warehouses),
            mock.patch.object(wb_sync.db, "get_fulfillments", return_value=["ff one"]),
            mock.patch.object(wb_sync.wb_api, "get_fbs_stock", side_effect=fbs_stock),
            mock.patch.object(wb_sync.db, "replace_ff_warehouse_map") as replace_map,
            mock.patch.object(wb_sync.db, "replace_mp_warehouse_stock") as replace_stock,
            mock.patch.object(wb_sync.db, "upsert_mp_stock") as upsert,
        ):
            self.assertEqual(wb_sync.sync_store_fbs("store"), 2)
        replace_map.assert_called_once()
        replace_stock.assert_called_once()
        self.assertEqual(upsert.call_count, 2)

        fbo = {("bc1", "Allowed"): 4, ("bc1", next(iter(wb_sync.EXCLUDED_FBO_WAREHOUSES))): 9}
        with (
            mock.patch.object(wb_sync.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(wb_sync.db, "get_catalog_items", return_value=catalog),
            mock.patch.object(wb_sync.wb_api, "get_fbo_stock_by_warehouse", return_value=fbo),
            mock.patch.object(wb_sync.db, "replace_mp_warehouse_stock") as replace_fbo,
            mock.patch.object(wb_sync.db, "upsert_mp_stock") as upsert_fbo,
        ):
            self.assertEqual(wb_sync.sync_store_fbo("store"), 2)
        self.assertEqual(upsert_fbo.call_args_list[0].args[4], 4)
        replace_fbo.assert_called_once()

    def test_gogol_warehouse_aliases_use_canonical_fulfillment_names(self) -> None:
        with mock.patch.object(
            wb_sync.db,
            "get_fulfillments",
            return_value=["ФулСервис Подольск", "ФФ GO Екатеринбург"],
        ):
            known = wb_sync._known_ff_by_warehouse("gogol")

        self.assertEqual(
            known[wb_sync._normalize_ff_name("ФуллСервис Подольск")],
            "ФулСервис Подольск",
        )
        self.assertEqual(
            known[wb_sync._normalize_ff_name("ФФ GO Екатерибург")],
            "ФФ GO Екатеринбург",
        )

    def test_stock_sync_edge_cases_and_all(self) -> None:
        with (
            mock.patch.object(wb_sync.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(wb_sync.db, "get_catalog_items", return_value=[]),
        ):
            self.assertEqual(wb_sync.sync_store_fbs("store"), 0)
            self.assertEqual(wb_sync.sync_store_fbo("store"), 0)

        with (
            mock.patch.object(wb_sync.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(
                wb_sync.db, "get_catalog_items", return_value=[{"article": "1", "barcode": "b"}]
            ),
            mock.patch.object(wb_sync.wb_api, "get_own_warehouses", return_value=[]),
        ):
            with self.assertRaises(wb_api.WBApiError):
                wb_sync.sync_store_fbs("store")

        with (
            mock.patch.object(wb_sync, "STORES", {"good": {}, "bad": {}, "none": {}}),
            mock.patch.object(wb_sync.wb_tokens, "has_token", side_effect=lambda slug: slug != "none"),
            mock.patch.object(
                wb_sync,
                "sync_store_fbs",
                side_effect=lambda slug: 1 if slug == "good" else (_ for _ in ()).throw(ValueError("bad")),
            ),
            mock.patch.object(wb_sync, "sync_store_fbo", return_value=2),
            mock.patch.object(wb_sync.db, "record_sync_health") as health,
        ):
            report = wb_sync.sync_all()
        self.assertFalse(report["none"]["token"])
        self.assertTrue(report["good"]["fbs"]["ok"])
        self.assertFalse(report["bad"]["fbs"]["ok"])
        self.assertEqual(health.call_count, 4)


class OzonSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_catalog_sync_and_helpers(self) -> None:
        self.assertEqual(ozon_catalog._pick_barcode(["OZN1", "real"]), "real")
        self.assertEqual(ozon_catalog._pick_barcode(["OZN1"]), "")
        service_hint = ozon_catalog.SERVICE_HINTS[0]
        raw = [
            {"id": 1},
            {"id": 2},
            {"id": 3},
        ]
        normalized = [
            {
                "offer_id": "A",
                "barcodes": ["OZN1", "bc"],
                "name": "A",
                "sku": 1,
                "product_id": 1,
                "updated_at": "now",
                "image_url": "",
            },
            {
                "offer_id": service_hint,
                "barcodes": [],
                "name": "S",
                "sku": 2,
                "product_id": 2,
                "updated_at": "now",
                "image_url": "",
            },
            {
                "offer_id": "",
                "barcodes": [],
                "name": "X",
                "sku": 3,
                "product_id": 3,
                "updated_at": "now",
                "image_url": "",
            },
        ]
        with (
            mock.patch.object(ozon_catalog.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(ozon_catalog.ozon_api, "get_product_list", return_value=raw),
            mock.patch.object(ozon_catalog.ozon_api, "get_product_info", return_value=raw),
            mock.patch.object(ozon_catalog.ozon_api, "normalize_product", side_effect=normalized),
            mock.patch.object(
                ozon_catalog.db,
                "replace_catalog",
                return_value={"added": 2, "updated": 0, "removed": 0, "kept": 0},
            ),
            mock.patch.object(ozon_catalog.ozon_api, "clear_store_context") as clear,
        ):
            report = ozon_catalog.sync_store("store")
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["service"], 1)
        clear.assert_called_once()

        with (
            mock.patch.object(ozon_catalog.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(ozon_catalog.ozon_api, "get_product_list", return_value=[]),
        ):
            self.assertEqual(ozon_catalog.sync_store("store")["total"], 0)

    def test_catalog_sync_all_paths(self) -> None:
        with (
            mock.patch.object(ozon_catalog, "STORES", {"good": {}, "api": {}, "bad": {}, "none": {}}),
            mock.patch.object(
                ozon_catalog.ozon_tokens, "has_credentials", side_effect=lambda slug: slug != "none"
            ),
            mock.patch.object(
                ozon_catalog,
                "sync_store",
                side_effect=[{"total": 1}, ozon_api.OzonApiError(403, "denied"), ValueError("boom")],
            ),
            mock.patch.object(ozon_catalog.db, "record_sync_health") as health,
        ):
            report = ozon_catalog.sync_all()
        self.assertTrue(report["good"]["ok"])
        self.assertFalse(report["api"]["ok"])
        self.assertFalse(report["bad"]["ok"])
        self.assertEqual(health.call_count, 3)

    def test_stock_sync_and_all(self) -> None:
        catalog = [{"article": "A"}, {"article": "B"}]
        items = [
            {"offer_id": "A", "stocks": [{"type": "fbo", "present": "3"}, {"type": "fbs", "present": "bad"}]},
            {"offer_id": "X", "stocks": [{"type": "fbo", "present": 9}]},
            {"offer_id": "B", "stocks": [{"type": "unknown", "present": 1}]},
        ]
        totals = ozon_sync._totals_by_scheme(items, {"A", "B"})
        self.assertEqual(totals["fbo"]["A"], 3)
        rows = [
            {"item_code": "A", "warehouse_name": "WH", "free_to_sell_amount": "2"},
            {"item_code": "A", "warehouse_name": "Bad", "free_to_sell_amount": "bad"},
            {"item_code": "X", "warehouse_name": "WH", "free_to_sell_amount": 5},
        ]
        with (
            mock.patch.object(ozon_sync.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(ozon_sync.db, "get_catalog_items", return_value=catalog),
            mock.patch.object(ozon_sync.ozon_api, "get_product_stocks", return_value=items),
            mock.patch.object(ozon_sync.ozon_api, "get_fbo_stock_by_warehouse", return_value=rows),
            mock.patch.object(ozon_sync.db, "get_warehouse_clusters", return_value={"WH": "C"}),
            mock.patch.object(ozon_sync.db, "replace_mp_warehouse_stock") as replace,
            mock.patch.object(ozon_sync.db, "upsert_mp_stock") as upsert,
            mock.patch.object(ozon_sync.ozon_api, "clear_store_context") as clear,
        ):
            self.assertEqual(ozon_sync.sync_store("store"), 1)
        replace.assert_called_once()
        self.assertEqual(upsert.call_count, 6)
        clear.assert_called_once()

        with (
            mock.patch.object(ozon_sync.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(ozon_sync.db, "get_catalog_items", return_value=[]),
        ):
            self.assertEqual(ozon_sync.sync_store("store"), 0)

        with (
            mock.patch.object(ozon_sync, "STORES", {"good": {}, "bad": {}, "none": {}}),
            mock.patch.object(
                ozon_sync.ozon_tokens, "has_credentials", side_effect=lambda slug: slug != "none"
            ),
            mock.patch.object(
                ozon_sync,
                "sync_store",
                side_effect=lambda slug: 2 if slug == "good" else (_ for _ in ()).throw(ValueError("boom")),
            ),
            mock.patch.object(ozon_sync.db, "record_sync_health") as health,
        ):
            report = ozon_sync.sync_all()
        self.assertTrue(report["good"]["ozon"]["ok"])
        self.assertFalse(report["bad"]["ozon"]["ok"])
        self.assertFalse(report["none"]["token"])
        self.assertEqual(health.call_count, 2)


class YandexSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_catalog_sync_and_all(self) -> None:
        campaigns = [
            {"id": 1, "business": {"id": 3}, "domain": "a"},
            {"id": 2, "business": {"id": 4}, "domain": "b"},
        ]
        with (
            mock.patch.object(yandex_catalog.ya_tokens, "get_business_id", return_value=None),
            mock.patch.object(yandex_catalog.ya_api, "get_campaigns", return_value=campaigns),
            mock.patch.object(
                yandex_catalog.ya_api,
                "normalize_campaign",
                side_effect=[{"business_id": 3}, {"business_id": 4}],
            ),
        ):
            self.assertEqual(yandex_catalog.resolve_business_id("store", "key"), 3)
        with mock.patch.object(yandex_catalog.ya_tokens, "get_business_id", return_value=9):
            self.assertEqual(yandex_catalog.resolve_business_id("store", "key"), 9)

        rows = [{"x": 1}, {"x": 2}, {"x": 3}]
        products = [
            {
                "article": "A",
                "barcode": "bc",
                "name": "A",
                "market_sku": 1,
                "updated_at": "now",
                "image_url": "",
            },
            {
                "article": "B",
                "barcode": "",
                "name": "B",
                "market_sku": 2,
                "updated_at": "now",
                "image_url": "",
            },
            {
                "article": "",
                "barcode": "",
                "name": "X",
                "market_sku": 3,
                "updated_at": "now",
                "image_url": "",
            },
        ]
        with (
            mock.patch.object(yandex_catalog.ya_tokens, "get_api_key", return_value="key"),
            mock.patch.object(yandex_catalog, "resolve_business_id", return_value=3),
            mock.patch.object(yandex_catalog.ya_api, "get_catalog", return_value=rows),
            mock.patch.object(yandex_catalog.ya_api, "normalize_catalog_item", side_effect=products),
            mock.patch.object(
                yandex_catalog.db,
                "replace_catalog",
                return_value={"added": 2, "updated": 0, "removed": 0, "kept": 0},
            ),
        ):
            report = yandex_catalog.sync_store("store")
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["no_barcode"], 1)

        with (
            mock.patch.object(yandex_catalog.ya_tokens, "get_api_key", return_value="key"),
            mock.patch.object(yandex_catalog, "resolve_business_id", return_value=None),
        ):
            self.assertEqual(yandex_catalog.sync_store("store")["total"], 0)

        with (
            mock.patch.object(yandex_catalog, "STORES", {"good": {}, "api": {}, "bad": {}, "none": {}}),
            mock.patch.object(
                yandex_catalog.ya_tokens, "has_credentials", side_effect=lambda slug: slug != "none"
            ),
            mock.patch.object(
                yandex_catalog,
                "sync_store",
                side_effect=[{"total": 1}, yandex_api.YandexApiError(403, "denied"), ValueError("boom")],
            ),
            mock.patch.object(yandex_catalog.db, "record_sync_health") as health,
        ):
            all_report = yandex_catalog.sync_all()
        self.assertTrue(all_report["good"]["ok"])
        self.assertFalse(all_report["api"]["ok"])
        self.assertFalse(all_report["bad"]["ok"])
        self.assertEqual(health.call_count, 3)

    def test_stock_helpers_sync_and_all(self) -> None:
        configured = [{"id": 1, "scheme": "fbs", "name": "One", "scheme_key": "fbs"}]
        with mock.patch.object(yandex_sync.ya_tokens, "get_campaigns", return_value=configured):
            self.assertEqual(yandex_sync.resolve_campaigns("store", "key"), configured)
        with (
            mock.patch.object(yandex_sync.ya_tokens, "get_campaigns", return_value=[]),
            mock.patch.object(yandex_sync.ya_api, "get_campaigns", return_value=[{"x": 1}, {"x": 2}]),
            mock.patch.object(
                yandex_sync.ya_api,
                "normalize_campaign",
                side_effect=[
                    {"campaign_id": 1, "scheme": "fbs", "domain": "shop"},
                    {"campaign_id": None, "scheme": "fbo", "domain": ""},
                ],
            ),
        ):
            self.assertEqual(yandex_sync.resolve_campaigns("store", "key")[0]["scheme_key"], "fbs")

        duplicate = [
            {"scheme_key": "fbs", "scheme": "fbs", "name": "One", "id": 1},
            {"scheme_key": "fbs", "scheme": "fbs", "name": "Two", "id": 2},
        ]
        with mock.patch.object(yandex_sync.ya_tokens, "get_campaigns", return_value=duplicate):
            self.assertEqual(len(yandex_sync.store_schemes("store")), 1)

        with mock.patch.object(
            yandex_sync.db,
            "get_fulfillments",
            return_value=["ФулСервис Подольск", "AFFLATUS Купавна"],
        ):
            known = yandex_sync.known_ff_by_campaign()
        self.assertEqual(
            known[yandex_sync._normalize_ff_name("Фулл Сервис")],
            "ФулСервис Подольск",
        )
        self.assertEqual(
            known[yandex_sync._normalize_ff_name("Afflatus")],
            "AFFLATUS Купавна",
        )

        with mock.patch.object(
            yandex_sync.ya_api,
            "get_fulfillment_warehouses",
            return_value=[{"id": "1", "name": "WH"}, {"id": "bad"}, {}],
        ):
            self.assertEqual(yandex_sync._warehouse_names("key"), {1: "WH"})
        with mock.patch.object(yandex_sync.ya_api, "get_fulfillment_warehouses", side_effect=ValueError("x")):
            self.assertEqual(yandex_sync._warehouse_names("key"), {})

        catalog = [{"article": "A"}, {"article": "B"}]
        campaigns = [
            {"id": 1, "scheme_key": yandex_sync.ya_tokens.FBY_SCHEME_KEY, "name": "FBY"},
            {"id": 2, "scheme_key": yandex_sync.ya_tokens.FBS_SCHEME_KEY, "name": "Afflatus"},
            {"id": 3, "scheme_key": yandex_sync.ya_tokens.FBS_SCHEME_KEY, "name": "ФуллСервис"},
        ]

        def stocks(_key, campaign_id):
            if campaign_id == 1:
                return [
                    {"article": "A", "stocks": [{"type": "AVAILABLE", "count": 2}], "warehouse_id": 1},
                    {"article": "X", "stocks": [{"type": "AVAILABLE", "count": 3}], "warehouse_id": 1},
                ]
            quantity = 1 if campaign_id == 2 else 2
            return [
                {
                    "article": "B",
                    "stocks": [{"type": "AVAILABLE", "count": quantity}],
                    "warehouse_id": campaign_id,
                }
            ]

        with (
            mock.patch.object(yandex_sync.ya_tokens, "get_api_key", return_value="key"),
            mock.patch.object(yandex_sync.db, "get_catalog_items", return_value=catalog),
            mock.patch.object(yandex_sync, "resolve_campaigns", return_value=campaigns),
            mock.patch.object(yandex_sync, "_warehouse_names", return_value={1: "WH"}),
            mock.patch.object(
                yandex_sync,
                "known_ff_by_campaign",
                return_value={
                    yandex_sync._normalize_ff_name("Afflatus"): "AFFLATUS Купавна",
                    yandex_sync._normalize_ff_name("ФуллСервис"): "ФулСервис Подольск",
                },
            ),
            mock.patch.object(yandex_sync.ya_api, "get_stocks", side_effect=stocks),
            mock.patch.object(yandex_sync.db, "replace_mp_warehouse_stock") as replace,
            mock.patch.object(yandex_sync.db, "upsert_mp_stock") as upsert,
            mock.patch.object(yandex_sync.db, "delete_mp_stock_scheme_variants") as cleanup,
        ):
            self.assertEqual(yandex_sync.sync_store("store"), 2)
        self.assertEqual(replace.call_count, 2)
        self.assertEqual(
            replace.call_args_list[1].args[3],
            [
                ("B", "AFFLATUS Купавна", None, 1, mock.ANY),
                ("B", "ФулСервис Подольск", None, 2, mock.ANY),
            ],
        )
        self.assertEqual(upsert.call_count, 4)
        self.assertIn(
            mock.call("store", "B", "YANDEX MARKET", "fbs", 3, mock.ANY),
            upsert.call_args_list,
        )
        cleanup.assert_called_once_with("store", "YANDEX MARKET", "fbs")

        with (
            mock.patch.object(yandex_sync.ya_tokens, "get_api_key", return_value="key"),
            mock.patch.object(yandex_sync.db, "get_catalog_items", return_value=[]),
        ):
            self.assertEqual(yandex_sync.sync_store("store"), 0)
        with (
            mock.patch.object(yandex_sync.ya_tokens, "get_api_key", return_value="key"),
            mock.patch.object(yandex_sync.db, "get_catalog_items", return_value=catalog),
            mock.patch.object(yandex_sync, "resolve_campaigns", return_value=[]),
        ):
            self.assertEqual(yandex_sync.sync_store("store"), 0)

        with (
            mock.patch.object(yandex_sync, "STORES", {"good": {}, "bad": {}, "none": {}}),
            mock.patch.object(
                yandex_sync.ya_tokens, "has_credentials", side_effect=lambda slug: slug != "none"
            ),
            mock.patch.object(
                yandex_sync,
                "sync_store",
                side_effect=lambda slug: 1 if slug == "good" else (_ for _ in ()).throw(ValueError("boom")),
            ),
            mock.patch.object(yandex_sync.db, "record_sync_health") as health,
        ):
            report = yandex_sync.sync_all()
        self.assertTrue(report["good"]["yandex"]["ok"])
        self.assertFalse(report["bad"]["yandex"]["ok"])
        self.assertFalse(report["none"]["token"])
        self.assertEqual(health.call_count, 2)


if __name__ == "__main__":
    unittest.main()
