import unittest

from app.economics.wb.standard_profit import compare


class ProfitTest(unittest.TestCase):
    def test_fbs_without_sales_still_has_zero_model_storage(self):
        standard, _, _ = compare({**self.row, "scheme": "FBS", "sales": 0}, self.product)
        self.assertEqual(standard["storage_total"], 0)

    def test_no_sales_uses_ninety_days(self):
        standard, _, _ = compare({**self.row, "scheme": "FBW", "sales": 0}, self.product)
        self.assertEqual(standard["storage_total"], 90)

    def test_no_sales_without_daily_rate_remains_unknown(self):
        standard, _, _ = compare(
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
        standard, current, delta = compare(self.row, self.product)
        self.assertEqual((standard["margin"], standard["roi"]), (240, 60))
        self.assertEqual((current["margin"], current["roi"]), (315, 78.75))
        self.assertEqual(delta, dict(margin=-75, roi=-18.75))
        for key in ("purchase", "fulfillment", "advertising", "acquiring", "team", "vat", "tax", "retail"):
            self.assertEqual(standard[key], current[key])

    def test_changed_database_purchase_changes_both(self):
        self.product["details"]["purchase_cost"] = 500
        standard, current, _ = compare(self.row, self.product)
        self.assertEqual((standard["margin"], current["margin"]), (140, 215))

    def test_missing_product_no_screenshot_fallback(self):
        self.row["calculator_inputs"] = {"purchase": 400}
        standard, current, delta = compare(self.row, None)
        self.assertIsNone(standard["margin"])
        self.assertIsNone(current["roi"])
        self.assertIsNone(delta["margin"])

    def test_missing_standard_tariff_does_not_hide_current(self):
        standard, current, delta = compare({**self.row, "logistics": None}, self.product)
        self.assertIsNone(standard["margin"])
        self.assertEqual(current["margin"], 315)
        self.assertIsNone(delta["margin"])

    def test_incomplete_advertising_is_not_zero(self):
        self.product["advertising"]["coverage"]["complete"] = False
        standard, current, _ = compare(self.row, self.product)
        self.assertIsNone(standard["margin"])
        self.assertIsNone(current["margin"])

    def test_zero_purchase_has_no_roi(self):
        self.product["details"]["purchase_cost"] = 0
        standard, current, delta = compare(self.row, self.product)
        self.assertIsNotNone(standard["margin"])
        self.assertIsNone(current["roi"])
        self.assertIsNone(delta["roi"])

    def test_returns_and_repeat_delivery_per_buyout(self):
        self.product["details"]["buyout_percent"] = 80
        standard, current, _ = compare(self.row, self.product)
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
                standard, current, _ = compare(self.row, self.product)
                self.assertIsNone(standard["logistics"])
                self.assertIsNone(standard["margin"])
                self.assertEqual(current["margin"], 315)

    def test_missing_volume_requires_return_tariff_unless_full_buyout(self):
        self.row["volume"] = None
        self.product["details"]["buyout_percent"] = 80
        self.assertIsNone(compare(self.row, self.product)[0]["logistics"])
        self.product["details"]["buyout_percent"] = 100
        self.assertEqual(compare(self.row, self.product)[0]["logistics"], 40)

    def test_fractional_volume_and_repeated_returns(self):
        self.product["details"]["buyout_percent"] = 50
        self.row["volume"] = 2.5
        self.assertEqual(compare(self.row, self.product)[0]["logistics"], 147)
        self.row["volume"] = 0.5
        self.assertEqual(compare(self.row, self.product)[0]["logistics"], 126)
