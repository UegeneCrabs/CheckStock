import unittest

from app import unit_economics_1c


class UnitEconomics1CCalculationTests(unittest.TestCase):
    def test_vat_is_extracted_from_customer_price_and_usn_uses_price_without_vat(self) -> None:
        taxes = unit_economics_1c.calculate_tax_components(2522, 7, 6, 0, "usn")

        self.assertEqual(round(taxes["vat"], 2), 164.99)
        self.assertEqual(round(taxes["usn"], 2), 141.42)
        self.assertEqual(round(taxes["total"], 2), 306.41)

    def test_profit_margin_and_roi_remain_consistent_with_included_vat(self) -> None:
        result = unit_economics_1c.calculate_unit_profit(
            retail_price=2522,
            customer_price=2522,
            acquiring_percent=2,
            delivery_with_returns=100,
            storage_wb_rub=2,
            turnover_days=10,
            wb_commission_percent=20,
            drr_percent=10,
            purchase_price=700,
            fulfillment_cost=40,
            team_commission_percent=3,
            vat_percent=7,
            usn_percent=6,
            osno_percent=0,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["vat"], 164.99)
        self.assertEqual(result["usn"], 141.42)
        self.assertEqual(result["margin"], 472.89)
        self.assertEqual(round(result["margin"] / 700 * 100, 2), 67.56)

    def test_stock_risk_uses_internal_status_and_twenty_one_day_coverage(self) -> None:
        inactive = unit_economics_1c.classify_stock_state(5, 0, "")
        low = unit_economics_1c.classify_stock_state(20, 42, "")
        overstock = unit_economics_1c.classify_stock_state(200, 1, "")
        internal = unit_economics_1c.classify_stock_state(200, 0, "Дефицит")

        self.assertEqual(inactive, {"is_low": False, "is_risk": False, "coverage_days": 0.0, "reason": None})
        self.assertTrue(low["is_low"])
        self.assertTrue(low["is_risk"])
        self.assertEqual(low["coverage_days"], 10)
        self.assertFalse(overstock["is_low"])
        self.assertTrue(overstock["is_risk"])
        self.assertTrue(internal["is_low"])
        self.assertEqual(internal["reason"], "internal")


if __name__ == "__main__":
    unittest.main()
