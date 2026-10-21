import copy
import unittest

from app.dto.wb_standard_costs import CalculatorProduct, SourceBundle
from app.economics.wb.standard_costs import build as build_report


def build(raw):
    return build_report(SourceBundle.model_validate(raw))


def fixture():
    tariff = {"warehouseName": "Склад WB РФ", "boxDeliveryBase": "60", "boxDeliveryLiter": "10"}
    sale = dict(
        rrdId=1,
        rrDate="2026-10-12",
        sellerOperName="Продажа",
        officeName="Склад WB",
        nmId=1,
        quantity=2,
        retailPriceWithDisc="1000",
        currency="RUB",
    )
    storage = dict(
        date="2026-10-12",
        nmId=1,
        warehouse="Склад WB РФ",
        volume=2,
        barcodesCount=3,
        warehousePrice=1.5,
        loyaltyDiscount=0,
        calcType="короба: товары свыше базы",
    )
    return dict(
        date_from="2026-10-12",
        date_to="2026-10-18",
        updated_at="2026-10-19",
        commission_date="2026-10-19",
        sources=[],
        cards=[dict(nmID=1, subjectID=5, dimensions=dict(length=10, width=10, height=20))],
        commissions={"report": [dict(subjectID=5, paidStorageKgvp=20, kgvpMarketplace=25)]},
        tariffs={
            f"2026-10-{d}": {"response": {"data": {"currency": "RUB", "warehouseList": [tariff]}}}
            for d in range(12, 19)
        },
        finance=[sale, dict(sale, rrdId=2, sellerOperName="Возврат", quantity=1)],
        storage=[
            storage,
            dict(storage, barcodesCount=0, warehousePrice=-1.5, calcType="скидка по спецусловиям"),
        ],
    )


class CostsTests(unittest.TestCase):
    def test_pallet_base_charge_is_counted_once_before_discount(self):
        raw = fixture()
        raw["storage"][0].update(calcType="паллеты: основные", palletCount=1, warehousePrice=42.5)
        row = build(raw)["rows"][0]
        self.assertEqual(row["storage"], 42.5)
        self.assertAlmostEqual(row["storage_unit_day"], 42.5 / 3)
        self.assertIsNotNone(row["logistics"])

    def test_no_storage_records_is_not_confirmed_zero(self):
        raw = fixture()
        raw["storage"] = []
        row = build(raw)["rows"][0]
        self.assertIsNone(row["storage"])
        self.assertIsNone(row["storage_unit_day"])

    def test_volume_precision_survives_build_and_return_calculation(self):
        from app.economics.wb.standard_logistics import logistics_with_returns

        for dimensions, volume in [((1, 1, 1), 0.001), ((10, 10, 22.05), 2.205)]:
            with self.subTest(volume=volume):
                raw = fixture()
                raw["cards"][0]["dimensions"] = dict(
                    zip(("length", "width", "height"), dimensions, strict=True)
                )
                row = build(raw)["rows"][0]
                self.assertAlmostEqual(row["volume"], volume)
                result = logistics_with_returns(
                    row, CalculatorProduct.model_validate({"details": {"buyout_percent": 80}})
                )
                self.assertEqual(result["volume_source"], "stock")
                self.assertAlmostEqual(result["reverse"], 60)
                self.assertIsNotNone(result["total"])

    def test_costs_per_unit_and_storage_per_unit_day(self):
        row = build(fixture())["rows"][0]
        self.assertEqual(row["unit_price"], 1000)
        self.assertEqual(row["commission_unit"], 200)
        self.assertEqual(row["storage_unit_day"], 0.5)

    def test_returns_do_not_change_average_sale_price(self):
        raw = fixture()
        raw["finance"][1]["retailPriceWithDisc"] = "4000"
        row = build(raw)["rows"][0]
        self.assertEqual(row["unit_price"], 1000)
        self.assertEqual(row["commission_unit"], 200)

    def test_no_sales_means_no_unit_commission(self):
        raw = fixture()
        raw["finance"] = []
        row = build(raw)["rows"][0]
        self.assertIsNone(row["unit_price"])
        self.assertIsNone(row["commission_unit"])

    def test_real_charge_before_discount_and_signed_commission(self):
        row = build(fixture())["rows"][0]
        self.assertEqual(row["storage"], 1.5)
        self.assertEqual(row["stock_days"], 3)
        self.assertEqual(row["turnover"], 1000)
        self.assertEqual(row["commission"], 200)
        self.assertEqual(row["logistics"], 70)

    def test_missing_end_tariff_is_not_zero(self):
        raw = fixture()
        raw["tariffs"]["2026-10-18"]["response"]["data"]["warehouseList"] = []
        row = build(raw)["rows"][0]
        self.assertIsNone(row["logistics"])
        self.assertEqual(row["storage"], 1.5)

    def test_weighted_volume(self):
        raw = fixture()
        row = copy.deepcopy(raw["storage"][0])
        row.update(volume=5, barcodesCount=1, warehousePrice=0.6)
        raw["storage"].append(row)
        self.assertEqual(build(raw)["rows"][0]["logistics"], 77.5)

    def test_duplicate_finance_and_out_of_period_rejected(self):
        raw = fixture()
        raw["finance"].append(raw["finance"][0])
        with self.assertRaises(ValueError):
            build(raw)
        raw = fixture()
        raw["storage"][0]["date"] = "2026-10-19"
        with self.assertRaises(ValueError):
            build(raw)

    def test_unsupported_storage_remains_unknown(self):
        raw = fixture()
        raw["storage"][0]["calcType"] = "монопаллета"
        row = build(raw)["rows"][0]
        self.assertIsNone(row["storage"])
        self.assertIsNone(row["logistics"])

    def test_known_zero_cost_preserved(self):
        raw = fixture()
        raw["storage"][0]["warehousePrice"] = 0
        self.assertEqual(build(raw)["rows"][0]["storage"], 0)

    def test_unknown_scheme_does_not_get_fbw_rate(self):
        raw = fixture()
        raw["finance"][0]["officeName"] = "Неизвестный склад"
        row = next(r for r in build(raw)["rows"] if r["scheme"] == "Не определена")
        self.assertIsNone(row["commission"])


if __name__ == "__main__":
    unittest.main()
