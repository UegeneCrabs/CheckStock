import unittest
from datetime import date

from app import rnp


class RnpMetricTests(unittest.TestCase):
    def test_sales_lookback_is_bounded_by_marketplace(self) -> None:
        self.assertEqual(rnp.sales_lookback_days("bad", "WB", date(2026, 8, 12)), 8)
        self.assertEqual(rnp.sales_lookback_days("2025-01", "OZON", date(2026, 8, 12)), 35)

    def test_derived_metrics_include_ads_margin_and_roi(self) -> None:
        item = rnp._empty_day()
        item.update(
            {
                "orders_amount": 2000,
                "orders_count": 10,
                "sales_amount": 1500,
                "sales_count": 6,
                "gross_profit": 600,
                "traffic_clicks": 100,
                "traffic_carts": 20,
                "traffic_orders": 10,
                "buyout_count": 6,
                "ad_impressions": 1000,
                "ad_clicks": 50,
                "ad_spend": 150,
                "plan_orders_amount": 2500,
            }
        )

        result = rnp._derive_metrics(item)

        self.assertEqual(result["traffic_cr_cart"], 20)
        self.assertEqual(result["traffic_cr_total"], 10)
        self.assertEqual(result["buyout_percent"], 60)
        self.assertEqual(result["ad_ctr"], 5)
        self.assertEqual(result["profit_after_ads"], 450)
        self.assertEqual(result["margin_after_ads"], 30)
        self.assertEqual(result["roi"], 50)
        self.assertEqual(result["plan_completion"], 80)

    def test_api_daily_aggregates_sums_averages_and_regions(self) -> None:
        rows = [
            {
                "day": "2026-08-10",
                "traffic_clicks": 10,
                "rating": 4,
                "stock_regions": '{"Москва": 2}',
            },
            {
                "day": "2026-08-10",
                "traffic_clicks": 15,
                "rating": 5,
                "stock_regions": '{"Москва": 3, "Казань": 1}',
            },
        ]

        daily = rnp._api_daily(rows)["2026-08-10"]

        self.assertEqual(daily["traffic_clicks"], 25)
        self.assertEqual(daily["rating"], 4.5)
        self.assertEqual(daily["stock_regions"], '{"Казань": 1, "Москва": 5}')

    def test_daily_sales_are_normalized(self) -> None:
        rows = [
            {
                "day": "2026-08-10",
                "orders_amount": "1200.50",
                "orders_count": 3,
                "sales_amount": 800,
                "sales_count": 2,
                "gross_profit": 300,
                "costed_sales_count": 2,
            }
        ]

        daily = rnp._normalize_daily(rows)["2026-08-10"]

        self.assertEqual(daily["orders_amount"], 1200.5)
        self.assertEqual(daily["average_order"], 400.17)
        self.assertEqual(daily["gross_profit"], 300)

    def test_invalid_marketplace_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Неизвестный маркетплейс"):
            rnp._normalize_marketplace("unknown")


if __name__ == "__main__":
    unittest.main()
