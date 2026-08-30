import unittest

from app import unit_economics_1c


class UnitEconomics1CCalculationTests(unittest.TestCase):
    def test_advertising_per_unit_is_adjusted_by_buyout_percent(self) -> None:
        self.assertEqual(unit_economics_1c.calculate_advertising_per_unit(800, 10, 80), 100)
        self.assertEqual(unit_economics_1c.calculate_advertising_per_unit(800, 10, 0), 80)
        self.assertEqual(unit_economics_1c.calculate_advertising_per_unit(800, 0, 80), 0)

    def test_drr_is_adjusted_by_buyout_percent(self) -> None:
        self.assertEqual(unit_economics_1c.calculate_drr_percent(800, 10_000, 80), 10)
        self.assertEqual(unit_economics_1c.calculate_drr_percent(800, 10_000, 0), 8)
        self.assertEqual(unit_economics_1c.calculate_drr_percent(800, 0, 80), 100)

    def test_vat_is_extracted_from_spp_price_and_usn_excludes_vat(self) -> None:
        taxes = unit_economics_1c.calculate_tax_components(2522, 7, 6, 0, "usn")

        self.assertEqual(round(taxes["vat"], 2), 164.99)
        self.assertEqual(round(taxes["usn"], 2), 141.42)
        self.assertEqual(round(taxes["total"], 2), 306.41)

    def test_profit_uses_per_order_advertising_rubles_instead_of_retail_drr(self) -> None:
        result = unit_economics_1c.calculate_unit_profit(
            retail_price=2522,
            customer_price=2522,
            acquiring_percent=2,
            delivery_with_returns=100,
            storage_wb_rub=2,
            turnover_days=10,
            wb_commission_percent=20,
            advertising_rub=252.2,
            purchase_price=700,
            fulfillment_cost=40,
            team_commission_percent=3,
            vat_percent=7,
            usn_percent=6,
            osno_percent=0,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["advertising"], 252.2)
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
