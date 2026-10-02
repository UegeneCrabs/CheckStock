"""Barcode display regression tests with a temporary DB and no external services."""

import asyncio
import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

YM = "YANDEX MARKET"
ZERO = "04601234567890"
EAN = "4601234567890"
LONG = "14601234567890"


class YandexBarcodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.ya = importlib.import_module("app.yandex.api")
            cls.wb = importlib.import_module("app.wb.api")
            cls.catalog = importlib.import_module("app.repositories.catalog")
            cls.core = importlib.import_module("app.repositories.core")
            cls.fulfillment = importlib.import_module("app.repositories.fulfillment_stock")
            cls.mp_stock = importlib.import_module("app.repositories.marketplace_stock")
            cls.stock_repository = importlib.import_module("app.infrastructure.stock_repository")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.orm = importlib.import_module("app.infrastructure.orm")
            cls.identity = importlib.import_module("app.stock.catalog_identity")
            cls.total = importlib.import_module("app.stock.total")
            cls.calculations = importlib.import_module("app.economics.yandex.calculations")
            cls.calendar = importlib.import_module("app.web.routers.economics_calendar")
            cls.inbound = importlib.import_module("app.web.routers.inbound_supplies")
            cls.cost_report = importlib.import_module("app.web.routers.stock_cost_report")
            cls.inbound_dto = importlib.import_module("app.dto.inbound_supplies")
            cls.rendering = importlib.import_module("app.web.stock_rendering")
            cls.stock_dto = importlib.import_module("app.dto.stock")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-barcodes-")
        self.addCleanup(directory.cleanup)
        self.database = self.database_module.Database(Path(directory.name) / "isolated.sqlite")
        self.addCleanup(self.database.dispose)
        self.orm.OrmBase.metadata.create_all(self.database.engine)
        redirect = patch.object(self.core, "database_for_path", return_value=self.database)
        redirect.start()
        self.addCleanup(redirect.stop)
        with self.database.connect() as conn:
            for article, barcode in (("mixed", ZERO), ("only-zero", "0123")):
                conn.execute(
                    "INSERT INTO stock_items (store_slug, marketplace, article, barcode, name) "
                    "VALUES ('rimili', ?, ?, ?, ?)",
                    (YM, article, barcode, article),
                )
                for table in ("ff_stock", "trash_stock"):
                    conn.execute(
                        f"INSERT INTO {table} (store_slug,marketplace,article,fulfillment,quantity) "
                        "VALUES ('rimili', ?, ?, 'Test FF', 3)",
                        (YM, article),
                    )
                conn.execute(
                    "INSERT INTO mp_warehouse_stock "
                    "(store_slug,marketplace,article,scheme,warehouse,quantity) "
                    "VALUES ('rimili', ?, ?, 'fbs', 'Test FF', 4)",
                    (YM, article),
                )
            for code in (EAN, LONG):
                conn.execute(
                    "INSERT INTO catalog_barcodes (stock_item_id,barcode) "
                    "SELECT id, ? FROM stock_items WHERE article='mixed'",
                    (code,),
                )
            conn.commit()

    def test_api_uses_same_priority_as_wb_and_keeps_original_codes(self):
        codes = [None, True, " ", " " + ZERO + " ", LONG, EAN, EAN, "2000000000000"]
        product = self.ya.normalize_catalog_item({"offer": {"offerId": "A", "barcodes": codes}})
        self.assertEqual(product["barcode"], EAN)
        self.assertEqual(product["barcodes"], [EAN, "2000000000000", LONG, ZERO])
        wb = self.wb.normalize_card({"sizes": [{"skus": codes}]})["sizes"][0]
        self.assertEqual(wb["barcode"], product["barcode"])
        self.assertEqual([wb["barcode"], *wb["extra_barcodes"]], product["barcodes"])

    def test_no_allowed_barcode_stays_empty_without_stripping_zero(self):
        for codes in ([ZERO, "0123"], [], [None, False, ""]):
            with self.subTest(codes=codes):
                product = self.ya.normalize_catalog_item({"offer": {"barcodes": codes}})
                self.assertEqual(product["barcode"], "")
        product = self.ya.normalize_catalog_item({"offer": {"barcodes": [ZERO]}})
        self.assertEqual(product["barcodes"], [ZERO])

    def test_non_13_digit_code_is_valid_when_no_ean_exists(self):
        product = self.ya.normalize_catalog_item({"offer": {"barcodes": [ZERO, LONG, "222"]}})
        self.assertEqual(product["barcode"], LONG)

    def test_saved_catalog_hides_zero_without_losing_alias_matching(self):
        for loader in (self.catalog.get_catalog_items, self.catalog.get_stock_items):
            with self.subTest(loader=loader.__name__):
                products = loader("rimili", YM)
                by_article = {p["article"]: p for p in products}
                self.assertEqual(by_article["mixed"]["barcode"], EAN)
                self.assertEqual(by_article["only-zero"]["barcode"], "")
                index = self.identity.CatalogIndex(products)
                self.assertEqual(index.resolve(barcode=ZERO)["article"], "mixed")
                self.assertEqual(index.resolve(barcode="0123")["article"], "only-zero")
        with self.database.connect() as conn:
            original = conn.execute("SELECT barcode FROM stock_items WHERE article='mixed'").fetchone()
            self.assertEqual(original["barcode"], ZERO)

    def test_stock_movement_catalog_preserves_legacy_primary_for_matching(self):
        with self.database.session_factory() as session:
            repo = self.stock_repository.SqlAlchemyStockRepository(session)
            query = self.stock_dto.CatalogQuery(store_slug="rimili", marketplace=YM)
            products = [p.model_dump() for p in repo.catalog(query).root]
        index = self.identity.CatalogIndex(products)
        self.assertEqual(index.resolve(barcode=ZERO)["barcode"], EAN)
        self.assertEqual(index.resolve(barcode="0123")["barcode"], "")

    def test_all_warehouse_views_use_allowed_barcodes(self):
        loaders = (
            lambda: self.mp_stock.get_mp_warehouse_details("rimili", YM, "fbs"),
            lambda: self.mp_stock.get_mp_fbs_warehouse_details("rimili", YM),
            lambda: self.fulfillment.get_ff_warehouse_details_by_mp("rimili", YM),
            lambda: self.fulfillment.get_trash_details("rimili", YM),
        )
        for index, loader in enumerate(loaders):
            with self.subTest(view=index):
                rows = loader()
                self.assertEqual({r["barcode"] for r in rows}, {EAN, ""})
                html = self.rendering.render_warehouse_table(rows, "Empty")
                self.assertNotIn(ZERO, html)
                self.assertNotIn("0123</button>", html)
                self.assertIn(EAN, html)

    def test_search_by_hidden_alias_shows_allowed_barcode(self):
        results = self.fulfillment.search_catalog("rimili", ZERO, marketplace=YM)
        self.assertEqual([(r["article"], r["barcode"]) for r in results], [("mixed", EAN)])

    def test_total_does_not_restore_zero_from_aliases(self):
        rows = self.total.build_rows(("rimili",), (("rimili", YM),))
        by_article = {r["article"]: r for r in rows}
        self.assertEqual(by_article["mixed"]["barcode"], EAN)
        self.assertEqual(by_article["only-zero"]["barcode"], "")
        self.assertNotIn(ZERO, by_article["mixed"]["barcodes"])
        self.assertEqual(by_article["only-zero"]["barcodes"], [])
        self.assertEqual(by_article["mixed"]["grand_total"], 3)

    def test_economics_product_and_calendar_hide_zero_aliases(self):
        raw = {"article": "A", "barcode": ZERO}
        self.assertIsNone(self.calculations.catalog_product("rimili", raw)["barcode"])
        raw["barcodes"] = [EAN]
        self.assertEqual(self.calculations.catalog_product("rimili", raw)["barcode"], EAN)
        products = self.catalog.get_catalog_items("rimili", YM)
        for product in products:
            result = self.calculations.catalog_product("rimili", product)
            self.assertIn(result["barcode"], (EAN, None))
        with (
            patch.object(self.calendar, "coerce_user", return_value=None),
            patch.object(self.calendar, "yandex_catalog", return_value=products),
        ):
            visible = self.calendar.products(YM, "rimili", None)
        self.assertEqual(visible[0]["barcodes"], [LONG, EAN])
        self.assertEqual(visible[1]["barcodes"], [])
        self.assertIn(ZERO, products[0]["barcodes"])

    def test_historical_cost_report_hides_zero_barcode(self):
        item = {
            "article": "A",
            "barcode": ZERO,
            "store_slug": "rimili",
            "marketplace": YM,
            "purchase_cost": 10,
            "purchase_price": 10,
            "quantity": 1,
            "start_quantity": 2,
            "moved_quantity": 0,
            "end_quantity": 1,
        }
        html = self.cost_report._fbs_sales_table({"fbs_sales": [item], "fbs_actual_sales": []})
        self.assertNotIn(ZERO, html)
        self.assertEqual(item["barcode"], ZERO)

    def test_saved_inbound_snapshot_is_filtered_only_for_display(self):
        snapshots = [
            self.inbound_dto.InboundSnapshot(
                store_slug="rimili",
                marketplace=market,
                supplies=[
                    self.inbound_dto.InboundSupply(
                        key="one",
                        supply_id="1",
                        status="test",
                        status_label="Test",
                        stage="transit",
                        items=[self.inbound_dto.InboundItem(article="A", barcode=ZERO, quantity=4)],
                    )
                ],
            )
            for market in (YM, "OZON")
        ]
        service = SimpleNamespace(report=Mock(return_value=snapshots))
        with patch.object(self.inbound, "allowed_targets", return_value=(("rimili", YM),)):
            response = asyncio.run(self.inbound.inbound_data(None, SimpleNamespace(inbound_supplies=service)))
        targets = json.loads(response.body)["targets"]
        self.assertEqual(targets[0]["supplies"][0]["items"][0]["barcode"], "")
        self.assertEqual(targets[1]["supplies"][0]["items"][0]["barcode"], ZERO)
        self.assertEqual(snapshots[0].supplies[0].items[0].barcode, ZERO)

    def test_other_marketplaces_are_unchanged(self):
        item = {"barcode": ZERO, "barcodes": [EAN]}
        for marketplace in ("WB", "OZON"):
            self.assertEqual(self.identity.display_barcode(item, marketplace), ZERO)


if __name__ == "__main__":
    unittest.main()
