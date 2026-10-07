"""Daily chart prices must come from the selected day's saved observations."""

import importlib
import os
import unittest
from datetime import date
from unittest.mock import patch


class PriceHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(
            os.environ, {"CHECKSTOCK_DATABASE_URL": ""}
        ):
            cls.wb = importlib.import_module("app.web.routers.unit_economics")
            cls.ym = importlib.import_module("app.yandex.economics_history")

    def wb_history(self, prices):
        metrics = self.wb.unit_economics_1c.empty_product_metrics(
            today=date(2026, 10, 7), period_days=3
        )
        product = self.wb._unit_economics_1c_mock_product(
            "rimili",
            {"article": "123 / M", "name": "Товар"},
            price_snapshot={
                "day": "2026-10-07",
                "customer_price_with_spp": 999,
                "retail_price": 1200,
                "customer_price_with_wallet": 900,
            },
            product_metrics=metrics,
            history_product_metrics=metrics,
            history_prices_by_day=prices,
            history_days=3,
        )
        return {row["date"]: row for row in product["history"]}

    def test_wb_prices_are_daily_spp_not_current_retail_or_wallet(self):
        rows = self.wb_history({
            "2026-10-05": {"customer_price_with_spp": 712.35},
            "2026-10-07": {"customer_price_with_spp": 699.99},
        })
        self.assertEqual(rows["2026-10-05"]["price_with_spp_rub"], 712.35)
        self.assertEqual(rows["2026-10-07"]["price_with_spp_rub"], 699.99)
        self.assertIsNone(rows["2026-10-06"]["price_with_spp_rub"])

    def test_wb_missing_or_invalid_day_price_does_not_fall_back(self):
        rows = self.wb_history({
            "2026-10-05": {"retail_price": 1000},
            "2026-10-06": {"customer_price_with_spp": float("nan")},
            "2026-10-07": {"customer_price_with_spp": None},
        })
        self.assertTrue(all(row["price_with_spp_rub"] is None for row in rows.values()))

    def ym_history(self, history=(), sources=None, *, estimated=False):
        with (
            patch.object(self.ym.repository, "history", return_value=list(history)),
            patch.object(self.ym.metrics, "get_history", return_value=([], set())),
            patch.object(self.ym.stock_history, "get_daily_stock_history", return_value=[]),
            patch.object(self.ym.catalog, "get_stock_items", return_value=[]),
            patch.object(self.ym.economics, "context", return_value={"sources": sources or {}}),
            patch.object(self.ym.economics, "current_inputs", return_value={
                "values": {"seller_price": 1100, "buyer_price": 930, "pay_price": 900},
                "pricing": {"buyer_estimated": estimated},
            }),
        ):
            result = self.ym.product_history("rimili", "SKU", "FBY", today=date(2026, 10, 7))
        return {row["date"]: row for row in result["chart"]}

    def test_ym_saved_and_current_buyer_prices_without_filling_missing_days(self):
        history = [{"article": "SKU", "day": "2026-10-05", "scheme": "COMMON", "data": {
            "day": "2026-10-05", "inputs": {"seller_price": 1000, "buyer_price": 870, "pay_price": 850},
        }}]
        rows = self.ym_history(history)
        self.assertEqual(rows["2026-10-05"]["price_with_spp_rub"], 870)
        self.assertIsNone(rows["2026-10-06"]["price_with_spp_rub"])
        self.assertEqual(rows["2026-10-07"]["price_with_spp_rub"], 930)
        self.assertFalse(rows["2026-10-07"]["price_with_spp_estimated"])
        self.assertIsNone(rows["2026-10-05"]["margin_rub"])

    def test_ym_saved_estimates_are_identified_instead_of_claiming_observed_prices(self):
        history = [{"article": "SKU", "day": "2026-10-05", "scheme": "COMMON", "data": {
            "day": "2026-10-05", "inputs": {"buyer_price": 870},
            "origins": {"buyer_price": "Расчётная: по последнему СПП"},
        }}]
        rows = self.ym_history(history, estimated=True)
        self.assertTrue(rows["2026-10-05"]["price_with_spp_estimated"])
        self.assertTrue(rows["2026-10-07"]["price_with_spp_estimated"])

    def test_ym_daily_baseline_before_day_close_and_other_product_scope(self):
        sources = {("SKU", "day-input:2026-10-06:COMMON"): {"values": {
            "basis": "today_prices", "values": {"buyer_price": 901},
            "pricing": {"buyer_estimated": True},
        }}}
        history = [{"article": "OTHER", "day": "2026-10-05", "scheme": "COMMON", "data": {
            "day": "2026-10-05", "inputs": {"buyer_price": 99999},
        }}]
        rows = self.ym_history(history, sources)
        self.assertIsNone(rows["2026-10-05"]["price_with_spp_rub"])
        self.assertEqual(rows["2026-10-06"]["price_with_spp_rub"], 901)
        self.assertTrue(rows["2026-10-06"]["price_with_spp_estimated"])

    @staticmethod
    def legacy_price(scheme, value, *, estimated=False):
        return {"article": "SKU", "day": "2026-10-05", "scheme": scheme, "data": {
            "day": "2026-10-05", "inputs": {"buyer_price": value},
            "origins": {"buyer_price": "Расчётная: по последнему СПП" if estimated else "Витрина: цена с СПП"},
        }}

    def test_ym_price_survives_single_legacy_scheme_without_common_profit(self):
        for scheme in ("FBY", "FBS"):
            with self.subTest(scheme=scheme):
                rows = self.ym_history([self.legacy_price(scheme, 870, estimated=True)])
                self.assertEqual(rows["2026-10-05"]["price_with_spp_rub"], 870)
                self.assertTrue(rows["2026-10-05"]["price_with_spp_estimated"])
                self.assertIsNone(rows["2026-10-05"]["margin_rub"])

    def test_ym_equal_legacy_prices_are_one_price_not_sum_and_keep_estimate_provenance(self):
        rows = self.ym_history([
            self.legacy_price("FBY", 870),
            self.legacy_price("FBS", 870, estimated=True),
        ])
        self.assertEqual(rows["2026-10-05"]["price_with_spp_rub"], 870)
        self.assertTrue(rows["2026-10-05"]["price_with_spp_estimated"])

    def test_ym_conflicting_legacy_prices_stay_unknown_even_with_baseline(self):
        sources = {("SKU", "day-input:2026-10-05:COMMON"): {"values": {
            "basis": "today_prices", "values": {"buyer_price": 901},
        }}}
        rows = self.ym_history([
            self.legacy_price("FBY", 870),
            self.legacy_price("FBS", 880),
        ], sources)
        self.assertIsNone(rows["2026-10-05"]["price_with_spp_rub"])
        self.assertFalse(rows["2026-10-05"]["price_with_spp_estimated"])

    def test_ym_common_daily_price_has_priority_over_legacy_models(self):
        rows = self.ym_history([
            self.legacy_price("FBY", 870, estimated=True),
            self.legacy_price("FBS", 880),
            self.legacy_price("COMMON", 890),
        ])
        self.assertEqual(rows["2026-10-05"]["price_with_spp_rub"], 890)
        self.assertFalse(rows["2026-10-05"]["price_with_spp_estimated"])


if __name__ == "__main__":
    unittest.main()
