import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.dto.wb_standard_costs import ReportRow, Snapshot
from app.economics.wb.standard_profit import compare, enrich
from app.economics.wb.standard_sources import calculator_product


class ProfitTest(unittest.TestCase):
    def test_compare_does_not_mutate_input_and_returns_calculation_details(self):
        row = ReportRow.model_validate(self.row)
        before = row.model_dump_json()
        result = compare(row, calculator_product(self.product))
        self.assertEqual(row.model_dump_json(), before)
        self.assertIsNotNone(result.logistics_returns.total)
        self.assertIsNotNone(result.storage_calculation.value)

    def test_enrichment_keeps_calculator_match_for_inactive_product(self):
        row = ReportRow.model_validate(self.row)
        snapshot = Snapshot(date_from="2026-10-12", date_to="2026-10-18", rows=(row,))
        product = self.product | {"article": row.article}

        def catalogue(*args, include_inactive=False, **kwargs):
            return ([product] if include_inactive else []), []

        with (
            patch("app.economics.wb.catalogue.load_wb_calculator_products", side_effect=catalogue),
            patch("app.economics.wb.standard_profit.wb_standard_costs.photos", return_value=[]),
        ):
            result = enrich("tris", snapshot)
        expected = compare(row, calculator_product(product))
        self.assertEqual(result.rows[0].current_profit, expected.current)
        self.assertIsNotNone(result.rows[0].current_profit.margin)
        self.assertIsNone(snapshot.rows[0].current_profit.margin)

    def compare(self, row, product):
        typed_row = ReportRow.model_validate(row)
        result = compare(typed_row, calculator_product(product) if product is not None else None)
        row["logistics_returns"] = result.logistics_returns.model_dump(mode="json")
        row["storage_calculation"] = result.storage_calculation.model_dump(mode="json")
        return result.standard, result.current, result.delta

    def test_missing_api_storage_uses_marked_zero_and_allows_profit(self):
        row = {
            **self.row,
            "scheme": "FBW",
            "storage": None,
            "storage_unit_day": None,
            "missing": ["В отчёте WB нет базовых строк хранения товара за неделю"],
        }
        standard, _, _ = self.compare(row, self.product)
        self.assertEqual(standard["storage_total"], 0)
        self.assertTrue(standard["complete"])
        self.assertEqual(row["storage_calculation"]["basis"], "missing_api_zero")
        self.assertIn("API WB", row["storage_calculation"]["warning"])

    def test_invalid_storage_is_not_treated_as_missing_api_rows(self):
        row = {
            **self.row,
            "scheme": "FBW",
            "storage": None,
            "storage_unit_day": None,
            "missing": ["Нет подтверждённой суммы хранения до скидок"],
        }
        standard, _, _ = self.compare(row, self.product)
        self.assertIsNone(standard["storage_total"])

    def test_invalid_storage_with_empty_details_is_not_assumed_zero(self):
        row = {
            **self.row,
            "scheme": "FBW",
            "storage": None,
            "storage_unit_day": None,
            "missing": [
                "В отчёте WB нет базовых строк хранения товара за неделю",
                "Начисление хранения без количества товара",
            ],
        }
        standard, _, _ = self.compare(row, self.product)
        self.assertIsNone(standard["storage_total"])

    def test_fbs_without_sales_still_has_zero_model_storage(self):
        standard, _, _ = self.compare({**self.row, "scheme": "FBS", "sales": 0}, self.product)
        self.assertEqual(standard["storage_total"], 0)

    def test_no_sales_uses_ninety_days(self):
        standard, _, _ = self.compare({**self.row, "scheme": "FBW", "sales": 0}, self.product)
        self.assertEqual(standard["storage_total"], 90)

    def test_no_sales_without_daily_rate_remains_unknown(self):
        standard, _, _ = self.compare(
            {**self.row, "scheme": "FBW", "sales": 0, "storage_unit_day": None}, self.product
        )
        self.assertIsNone(standard["storage_total"])

    def setUp(self):
        self.product = dict(
            price=dict(current=1000, with_spp=900),
            advertising=dict(spend_per_order=30, coverage=dict(complete=True)),
            details=dict(
                purchase_cost=400,
                buyout_percent=100,
                fulfillment_cost=20,
                acquiring=1,
                team_commission_percent=5,
                vat_value=40,
                usn_value=10,
                commission_value=100,
                delivery_with_returns=20,
                storage_sum=5,
            ),
        )
        self.row = dict(volume=2, sales=10, storage=100, storage_unit_day=1, logistics=40, commission_rate=15)

    def test_same_inputs_only_three_costs_change(self):
        standard, current, delta = self.compare(self.row, self.product)
        self.assertEqual((standard["margin"], standard["roi"]), (240, 60))
        self.assertEqual((current["margin"], current["roi"]), (315, 78.75))
        self.assertEqual(delta.model_dump(), dict(margin=-75, roi=-18.75))
        for key in ("purchase", "fulfillment", "advertising", "acquiring", "team", "vat", "tax", "retail"):
            self.assertEqual(standard[key], current[key])

    def test_changed_database_purchase_changes_both(self):
        self.product["details"]["purchase_cost"] = 500
        standard, current, _ = self.compare(self.row, self.product)
        self.assertEqual((standard["margin"], current["margin"]), (140, 215))

    def test_missing_product_no_screenshot_fallback(self):
        standard, current, delta = self.compare(self.row, None)
        self.assertIsNone(standard["margin"])
        self.assertIsNone(current["roi"])
        self.assertIsNone(delta["margin"])

    def test_missing_standard_tariff_does_not_hide_current(self):
        standard, current, delta = self.compare({**self.row, "logistics": None}, self.product)
        self.assertIsNone(standard["margin"])
        self.assertEqual(current["margin"], 315)
        self.assertIsNone(delta["margin"])

    def test_advertising_matches_shared_browser_calculator_cases(self):
        cases = json.loads((Path(__file__).parent / "fixtures/wb_advertising_parity.json").read_text())
        for case in cases:
            with self.subTest(case=case["name"]):
                standard, current, delta = self.compare(self.row, case["product"])
                for scenario, prefix in ((standard, "standard"), (current, "current")):
                    self.assertEqual(scenario.advertising, case["advertising"])
                    self.assertEqual(scenario.margin, case[prefix + "_margin"])
                    self.assertEqual(scenario.roi, case[prefix + "_roi"])
                    self.assertTrue(scenario.complete)
                self.assertEqual(delta.margin, -75)
                self.assertEqual(delta.roi, -18.75)

    def test_missing_advertising_does_not_hide_missing_purchase(self):
        self.product["advertising"] = {}
        self.product["details"]["purchase_cost"] = None
        standard, current, _ = self.compare(self.row, self.product)
        for scenario in (standard, current):
            self.assertEqual(scenario.advertising, 0)
            self.assertIsNone(scenario.margin)
            self.assertIsNone(scenario.roi)
            self.assertIn("Закупка", scenario.missing)

    def test_zero_purchase_has_no_roi(self):
        self.product["details"]["purchase_cost"] = 0
        standard, current, delta = self.compare(self.row, self.product)
        self.assertIsNotNone(standard["margin"])
        self.assertIsNone(current["roi"])
        self.assertIsNone(delta["roi"])

    def test_returns_and_repeat_delivery_per_buyout(self):
        self.product["details"]["buyout_percent"] = 80
        standard, current, _ = self.compare(self.row, self.product)
        # Reverse 46 + 14 = 60; 0.25 failed attempts per buyout.
        self.assertEqual(standard["logistics"], 65)
        self.assertEqual(standard["margin"], 215)
        self.assertEqual(current["logistics"], 20)
        self.assertEqual(self.row["logistics_returns"]["extra_forward"], 10)
        self.assertEqual(self.row["logistics_returns"]["reverse_per_buyout"], 15)

    def test_invalid_buyout_is_not_free_shipping(self):
        for value in (None, 0, -1, 101):
            with self.subTest(value=value):
                self.product["details"]["buyout_percent"] = value
                standard, current, _ = self.compare(self.row, self.product)
                self.assertIsNone(standard["logistics"])
                self.assertIsNone(standard["margin"])
                self.assertEqual(current["margin"], 315)

    def test_missing_volume_requires_return_tariff_unless_full_buyout(self):
        self.row["volume"] = None
        self.product["details"]["buyout_percent"] = 80
        self.assertIsNone(self.compare(self.row, self.product)[0]["logistics"])
        self.product["details"]["buyout_percent"] = 100
        self.assertEqual(self.compare(self.row, self.product)[0]["logistics"], 40)

    def test_fractional_volume_and_repeated_returns(self):
        self.product["details"]["buyout_percent"] = 50
        self.row["volume"] = 2.5
        self.assertEqual(self.compare(self.row, self.product)[0]["logistics"], 147)
        self.row["volume"] = 0.5
        self.assertEqual(self.compare(self.row, self.product)[0]["logistics"], 126)
