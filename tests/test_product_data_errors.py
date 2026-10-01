"""Product warnings must not inherit another article's batch sync failures."""

import importlib
import os
import unittest
from contextlib import ExitStack
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.economics.data_errors import SOURCE_LABELS, product_errors


class WbProductErrorsTests(unittest.TestCase):
    def setUp(self):
        self.product = {"article": "776057582", "fbs_stock": 0, "fbo_stock": 3, "ff_available": 0}
        self.prices = {"retail_price": 200, "customer_price_with_spp": 180, "customer_price_with_wallet": 170}
        self.reference = {
            "purchase_price": 100,
            "fulfillment_cost": 0,
            "subject_commission_percent": 20,
            "team_commission_percent": 0,
            "category": "Товар",
            "abc_code": "A",
        }
        self.metrics = {"funnel_updated_at": "2026-10-01", "buyout_updated_at": "2026-10-01"}
        self.reputation = {"rating": 0, "reviews_count": 0}
        self.states = {
            scope: {"ok": False, "error": "Нет цены для 42 товаров, артикул 523320519"}
            for scope in SOURCE_LABELS
        }

    def errors(self):
        return product_errors(
            self.product, self.prices, self.reference, self.metrics, self.reputation, self.states
        )

    def test_complete_product_ignores_other_articles_and_store_failures(self):
        self.assertEqual(self.errors(), [])

    def test_missing_price_warns_only_affected_article_without_batch_details(self):
        healthy = self.errors()
        self.prices["customer_price_with_wallet"] = None
        affected = self.errors()
        self.assertEqual(healthy, [])
        self.assertIn("Не загружена цена WB Кошелька", affected)
        self.assertTrue(any(error.startswith("Цены СПП и WB Кошелька:") for error in affected))
        self.assertFalse(any(error.startswith("Цены WB:") for error in affected))
        self.assertFalse(any("523320519" in error or "42 товаров" in error for error in affected))

    def test_zero_is_known_but_null_nan_and_infinity_remain_missing(self):
        self.assertEqual(self.errors(), [])
        for value in (None, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.reference["fulfillment_cost"] = value
                self.assertIn("Не загружены затраты на ФФ", self.errors())

    def test_missing_reference_and_funnel_are_still_visible(self):
        self.reference["purchase_price"] = None
        self.metrics["funnel_updated_at"] = None
        errors = self.errors()
        self.assertIn("Не загружена себестоимость", errors)
        self.assertIn("Не загружены заказы и воронка WB за выбранный период", errors)

    def test_successful_stock_snapshot_allows_missing_zero_stock_row(self):
        self.product["ff_available"] = None
        self.states["ff"] = {"ok": True}
        self.assertEqual(self.errors(), [])


class YandexProductErrorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.module = importlib.import_module("app.economics.yandex.calculations")

    def load(self, *, incomplete_product=False, missing_ad_day=False):
        module = self.module
        today = date(2026, 10, 1)
        days = {(today - timedelta(days=offset)).isoformat() for offset in range(21)}
        catalog = [
            {"article": article, "name": article, "mp_sku": article} for article in ("healthy", "affected")
        ]
        snapshots = {
            "orders": {
                "data": [],
                "period_from": min(days),
                "period_to": max(days),
                "error": "Сбой другого товара",
            },
            "advertising": {"data": None, "error": "Сбой другого товара"},
            "reputation": {
                "data": [{"sku": row["article"], "rating": 5, "reviews_count": 0} for row in catalog],
                "error": "Сбой другого товара",
            },
            "buyout": {
                "data": [],
                "period_from": "2026-09-17",
                "period_to": "2026-09-30",
                "error": "Сбой другого товара",
            },
        }
        refs = {row["article"]: {"purchase_price": 100, "fulfillment_cost": 0} for row in catalog}
        prices = {row["article"]: {"seller_price": 200, "spp_percent": 10, "status": "ok"} for row in catalog}
        if incomplete_product:
            refs.pop("affected")
            prices["affected"].update(status="failed", message="Не получена цена этого товара")

        def history(store, source, start, end):
            loaded = {day for day in days if start <= day <= end}
            if source == "advertising" and missing_ad_day:
                loaded.discard("2026-09-30")
            return [], loaded

        inbound = SimpleNamespace(
            catalog=catalog,
            quantities={row["article"]: 0 for row in catalog},
            confirmed_quantities={},
            available=True,
        )
        fixtures = (
            (module.yandex_assortment, "active_articles", {row["article"] for row in catalog}),
            (module.yandex_assortment, "archived_articles", set()),
            (module.db, "get_catalog_items", catalog),
            (module.stock_sheet_inbound, "load", inbound),
            (module.yandex_economics, "catalog_group_ids", {}),
            (
                module.db,
                "get_stock_items",
                [
                    {"article": row["article"], "fbs_stock": 0, "fbo_stock": 3, "ff_available": 0}
                    for row in catalog
                ],
            ),
            (module.repository, "get_snapshots", snapshots),
            (module.yandex_product_statuses, "get_statuses", {}),
            (module.yandex_source_values, "get_values", refs),
            (module.yandex_storefront, "get_prices", prices),
            (module.repository, "get_daily_orders", []),
            (module.db, "get_sales_sync_states", []),
            (
                module.repository,
                "get_buyout_settings",
                {"buyout_period_days": 14, "default_buyout_percent": 95},
            ),
        )
        with ExitStack() as stack:
            for target, method, result in fixtures:
                stack.enter_context(patch.object(target, method, return_value=result))
            stack.enter_context(patch.object(module.repository, "get_history", side_effect=history))
            # A store-level failure exists even though one article has complete data.
            health = importlib.import_module("app.repositories.unit_economics_data_errors")
            stack.enter_context(
                patch.object(
                    health,
                    "source_states",
                    return_value={"gogol": {"catalog": {"ok": False, "error": "Сбой другого товара"}}},
                )
            )
            return module.load_products(("gogol",), today=today, include_economics=False)

    def test_batch_snapshot_and_sync_failures_do_not_mark_complete_products(self):
        products = self.load()
        self.assertEqual(len(products), 2)
        self.assertTrue(all(product["data_errors"] == [] for product in products))
        self.assertEqual(products[0]["advertising"]["spend"], 0)

    def test_individual_price_and_reference_errors_stay_on_the_affected_product(self):
        products = {product["article"]: product for product in self.load(incomplete_product=True)}
        self.assertEqual(products["healthy"]["data_errors"], [])
        self.assertIn("Не загружены данные 1С для товара ЯМ", products["affected"]["data_errors"])
        self.assertIn("Цена ЯМ: Не получена цена этого товара", products["affected"]["data_errors"])
        self.assertFalse(
            any(
                "другого товара" in message
                for product in products.values()
                for message in product["data_errors"]
            )
        )

    def test_missing_period_day_is_visible_for_every_product_it_affects(self):
        products = self.load(missing_ad_day=True)
        self.assertTrue(
            all("Не все дни рекламы загружены: 2026-09-30" in product["data_errors"] for product in products)
        )


if __name__ == "__main__":
    unittest.main()
