import unittest

from app.dto.wb_standard_costs import CalculatorProduct, ReportRow
from app.economics.wb.standard_costs import cost
from app.economics.wb.standard_delivery_tariffs import volume_tariff
from app.economics.wb.standard_logistics import logistics_with_returns
from app.wb.standard_costs_sources import WarehouseTariff


class VolumeTariffTests(unittest.TestCase):
    def test_all_volume_band_edges(self):
        for volume, expected in (
            (0.001, 23),
            (0.2, 23),
            (0.201, 26),
            (0.4, 26),
            (0.401, 29),
            (0.6, 29),
            (0.601, 30),
            (0.665, 30),
            (0.8, 30),
            (0.801, 32),
            (1, 32),
            (1.001, 46.014),
            (1.8, 57.2),
        ):
            with self.subTest(volume=volume):
                self.assertAlmostEqual(volume_tariff(volume), expected)

    def test_invalid_volume_is_unknown(self):
        for value in (None, 0, -1, float("nan"), float("inf")):
            self.assertIsNone(volume_tariff(value))

    def test_scheme_coefficient_and_no_double_application_above_liter(self):
        tariff = WarehouseTariff(
            fbw_base=78.2, fbw_extra=23.8, fbw_coefficient=170, fbs_base=92, fbs_extra=28, fbs_coefficient=200
        )
        self.assertEqual(cost(tariff, "FBW", 0.665), 51)
        self.assertEqual(cost(tariff, "FBS", 0.665), 60)
        self.assertAlmostEqual(cost(tariff, "FBW", 1.8), 97.24)
        self.assertEqual(cost(tariff, "FBW", 1), 54.4)
        self.assertIsNone(cost(WarehouseTariff(fbw_base=78.2), "FBW", 0.665))

    def test_rimili_article_total_with_returns(self):
        row = ReportRow(article="1029387433", scheme="FBW", volume=0.665, logistics=51)
        product = CalculatorProduct.model_validate({"details": {"buyout_percent": 90}})
        result = logistics_with_returns(row, product)
        self.assertEqual(result.reverse, 30)
        self.assertEqual(result.total, 60)
        self.assertAlmostEqual(result.extra_forward, 51 / 9)
        self.assertAlmostEqual(result.reverse_per_buyout, 30 / 9)

    def test_storage_volumes_override_card_and_average_prices_not_liters(self):
        from test_wb_standard_costs import build, fixture

        raw = fixture()
        raw["cards"][0]["dimensions"] = {"length": 10, "width": 10, "height": 5.95}
        for day in raw["tariffs"].values():
            day["response"]["data"]["warehouseList"][0].update(
                boxDeliveryBase="78.2", boxDeliveryLiter="23.8", boxDeliveryCoefExpr="170"
            )
        storage = raw["storage"][0]
        raw["storage"] = [
            dict(storage, volume=0.665, barcodesCount=1),
            dict(storage, volume=1.035, barcodesCount=3),
        ]
        row = build(raw).rows[0]
        result = logistics_with_returns(
            row, CalculatorProduct.model_validate({"details": {"buyout_percent": 80}})
        )
        self.assertEqual(result.volume_source, "stock")
        self.assertIsNone(result.volume)
        self.assertEqual([t.volume for t in result.volume_terms], [0.665, 1.035])
        self.assertAlmostEqual(result.reverse, (30 + 46.49 * 3) / 4)
        forward = (51 + (78.2 + 0.035 * 23.8) * 3) / 4
        self.assertAlmostEqual(result.forward, forward)
        self.assertAlmostEqual(result.total, forward + (forward + (30 + 46.49 * 3) / 4) / 4)

    def test_no_storage_uses_card_for_both_trips(self):
        from test_wb_standard_costs import build, fixture

        raw = fixture()
        raw["storage"] = []
        raw["cards"][0]["dimensions"] = {"length": 10, "width": 10, "height": 6.65}
        for day in raw["tariffs"].values():
            day["response"]["data"]["warehouseList"][0].update(boxDeliveryCoefExpr="170")
        row = build(raw).rows[0]
        result = logistics_with_returns(
            row, CalculatorProduct.model_validate({"details": {"buyout_percent": 90}})
        )
        self.assertEqual(result.volume_source, "card")
        self.assertAlmostEqual(result.volume, 0.665)
        self.assertEqual(result.total, 60)

    def test_unknown_storage_volume_does_not_silently_use_card(self):
        from test_wb_standard_costs import build, fixture

        raw = fixture()
        raw["storage"][0]["volume"] = None
        result = logistics_with_returns(
            build(raw).rows[0], CalculatorProduct.model_validate({"details": {"buyout_percent": 90}})
        )
        self.assertEqual(result.volume_source, "stock")
        self.assertIsNone(result.reverse)
        self.assertIsNone(result.total)
