import math
import unittest
from datetime import date, timedelta
from unittest import mock

from app import unit_economics_1c as economics
from app import unit_economics_1c_target_prices as pricing
from app.dto.unit_economics_1c import UnitEconomics1CCabinetSettings, UnitEconomics1CProductSettings


class TargetPriceCalculationTests(unittest.TestCase):
    def setUp(self):
        self.days = [(date(2026, 8, 27) + timedelta(days=i)).isoformat() for i in range(7)]
        self.orders = {day: {"orders_count": 10, "orders_amount": 8000} for day in self.days}
        self.ads = {day: {"spend": 400} for day in self.days}
        self.cabinet = UnitEconomics1CCabinetSettings(store_slug="rimili", usn_percent=6)
        self.settings = UnitEconomics1CProductSettings(
            store_slug="rimili",
            article="123",
            delivery_wb_rub=45,
            return_cost_rub=20,
            storage_wb_rub=1,
        )
        self.reference = {
            "purchase_price": 300,
            "fulfillment_cost": 50,
            "subject_commission_percent": 10,
            "team_commission_percent": 4,
            "turnover_days": 21,
        }
        self.price = {
            "retail_price": 1000,
            "customer_price_with_spp": 800,
            "customer_price_with_wallet": 784,
            "day": self.days[-1],
        }

    def metrics(self, orders=None, ads=None, complete=None, raw=80, default=None):
        return pricing.weekly_metrics(
            self.days,
            self.orders if orders is None else orders,
            self.ads if ads is None else ads,
            set() if complete is None else complete,
            {"buyout_percent": raw},
            default,
        )

    def row(self, **changes):
        values = dict(
            price=self.price,
            reference=self.reference,
            product_settings=self.settings,
            cabinet=self.cabinet,
            weekly=self.metrics(),
        )
        values.update(changes)
        return pricing.calculate_row(**values)

    def test_closed_week_crosses_month_and_excludes_today(self):
        self.assertEqual(pricing.closed_week(date(2026, 9, 3)), (date(2026, 8, 27), date(2026, 9, 2)))
        self.assertEqual(pricing.closed_week(date(2026, 1, 1)), (date(2025, 12, 25), date(2025, 12, 31)))

    def test_seven_days_and_three_days_are_summed_not_averaged(self):
        full = self.metrics()
        self.assertEqual(full["drr"], 6.25)
        self.assertEqual(full["advertising_per_unit"], 50)
        self.assertEqual(full["warnings"], [])
        orders = {
            self.days[0]: {"orders_count": 1, "orders_amount": 100},
            self.days[1]: {"orders_count": 9, "orders_amount": 900},
            self.days[2]: {"orders_count": 10, "orders_amount": 2000},
        }
        ads = {self.days[0]: {"spend": 50}, self.days[1]: {"spend": 90}, self.days[2]: {"spend": 60}}
        result = self.metrics(orders, ads)
        self.assertEqual(result["days"], 3)
        self.assertEqual(result["missing_dates"], self.days[3:])
        self.assertEqual(result["orders_amount"], 3000)
        self.assertEqual(result["spend"], 200)
        self.assertEqual(result["drr"], 8.33)
        self.assertIn("3 из 7", result["warnings"][0])

    def test_asymmetric_gaps_use_same_dates_for_spend_and_orders(self):
        result = self.metrics({day: self.orders[day] for day in self.days[:3]})
        self.assertEqual(result["spend"], 1200)
        self.assertEqual(result["orders_amount"], 24000)
        self.assertEqual(result["drr"], 6.25)
        disjoint = self.metrics({self.days[0]: self.orders[self.days[0]]}, {self.days[1]: {"spend": 100}})
        self.assertEqual(disjoint["days"], 0)
        self.assertIsNone(disjoint["drr"])

    def test_successful_empty_ads_are_zero_not_missing(self):
        empty = self.metrics(ads={}, complete=set(self.days))
        self.assertEqual(empty["drr"], 0)
        self.assertTrue(empty["complete"])
        self.assertEqual(empty["warnings"], [])
        missing = self.metrics(ads={})
        self.assertIsNone(missing["drr"])
        self.assertEqual(missing["missing_advertising_dates"], self.days)

    def test_zero_turnover_is_undefined_even_with_spend(self):
        zero_orders = {day: {"orders_count": 0, "orders_amount": 0} for day in self.days}
        for ads in (self.ads, {day: {"spend": 0} for day in self.days}):
            result = self.metrics(zero_orders, ads)
            self.assertEqual(result["drr"], 0 if result["spend"] == 0 else None)
            if result["spend"] > 0:
                self.assertIsNone(result["advertising_per_unit"])
            else:
                self.assertEqual(result["advertising_per_unit"], 0)
            self.assertTrue(result["complete"])
            if result["spend"] > 0:
                self.assertIn("ДРР не определён", result["warnings"][-1])

    def test_buyout_default_and_missing_buyout(self):
        result = self.metrics(raw=0, default=50)
        self.assertEqual(result["drr"], 10)
        self.assertTrue(result["buyout_default_applied"])
        self.assertEqual(result["buyout_percent"], 50)
        self.assertIsNone(self.metrics(raw=0)["drr"])
        row = self.row(weekly=self.metrics(raw=0))
        self.assertIsNone(row["target_price"])
        self.assertIn("процент выкупа", " ".join(row["target_warnings"]))

    def test_observed_drr_can_exceed_one_hundred(self):
        result = self.metrics(ads={day: {"spend": 16000} for day in self.days})
        self.assertEqual(result["drr"], 250)

    def test_target_advertising_uses_retail_drr_and_buyout(self):
        self.assertEqual(pricing.calculate_target_advertising_rub(1000, 8, 80), 64)
        self.assertEqual(pricing.calculate_target_advertising_rub(1000, 10, 50), 50)

    def test_target_matches_calculator_and_preserves_discounts(self):
        row = self.row()
        self.assertEqual(row["target_drr"], 8)
        self.assertEqual(row["target_roi"], 50)
        self.assertEqual(
            row["target_advertising_rub"],
            pricing.calculate_target_advertising_rub(row["target_retail_price"], 8, 80),
        )
        self.assertEqual(row["current_roi"], 77.64)
        target_profit = economics.calculate_unit_profit(
            retail_price=row["target_retail_price"],
            customer_price=row["target_spp_price"],
            acquiring_percent=3.8,
            delivery_with_returns=58,
            storage_wb_rub=1,
            turnover_days=21,
            wb_commission_percent=10,
            advertising_rub=row["target_advertising_rub"],
            purchase_price=300,
            fulfillment_cost=50,
            team_commission_percent=4,
            vat_percent=9,
            usn_percent=6,
            osno_percent=0,
        )
        self.assertEqual(row["target_actual_roi"], round(target_profit["margin"] / 300 * 100, 2))
        self.assertLessEqual(abs(row["target_actual_roi"] - 50), 0.1)
        self.assertEqual(
            row["target_price"], row["target_spp_price"] - math.ceil(row["target_spp_price"] * 0.02)
        )
        self.assertEqual(row["target_warnings"], [])

    def test_goals_are_per_cabinet_and_zero_is_not_default(self):
        normal = self.row()
        zero = self.row(
            cabinet=self.cabinet.model_copy(update={"target_drr_percent": 0, "target_roi_percent": 0})
        )
        high = self.row(
            cabinet=self.cabinet.model_copy(update={"target_drr_percent": 20, "target_roi_percent": 150})
        )
        self.assertEqual(zero["target_advertising_rub"], 0)
        self.assertEqual(zero["target_drr"], 0)
        self.assertEqual(zero["target_roi"], 0)
        self.assertLess(zero["target_price"], normal["target_price"])
        self.assertGreater(high["target_price"], normal["target_price"])

    def test_product_goals_override_cabinet_and_are_exposed_to_calculator(self):
        custom_settings = self.settings.model_copy(
            update={"target_drr_percent": 3.5, "target_roi_percent": 20}
        )
        custom = self.row(product_settings=custom_settings)
        default = self.row()
        self.assertEqual(custom["target_drr"], 3.5)
        self.assertEqual(custom["target_roi"], 20)
        self.assertTrue(custom["target_overridden"])
        self.assertEqual(custom["cabinet_target_drr"], 8)
        self.assertEqual(custom["cabinet_target_roi"], 50)
        self.assertEqual(custom["calculator"]["target_roi"], 20)
        self.assertEqual(custom["calculator"]["drr"], 3.5)
        self.assertLess(custom["target_price"], default["target_price"])
        self.assertFalse(default["target_overridden"])

    def test_tax_system_and_turnover_zero_follow_calculator(self):
        gogol = self.cabinet.model_copy(
            update={"store_slug": "gogol", "tax_system": "osno", "osno_percent": 20}
        )
        self.assertGreater(self.row(cabinet=gogol)["target_price"], self.row()["target_price"])
        self.assertLess(
            self.row(reference={**self.reference, "turnover_days": 0})["target_price"],
            self.row()["target_price"],
        )

    def test_missing_inputs_and_unreachable_target_do_not_invent_prices(self):
        for changes in (
            {"reference": {**self.reference, "purchase_price": None}},
            {"reference": {**self.reference, "purchase_price": 0}},
            {"reference": {**self.reference, "subject_commission_percent": None}},
            {"price": {**self.price, "retail_price": None}},
        ):
            with self.subTest(changes=changes):
                row = self.row(**changes)
                self.assertIsNone(row["target_price"])
                self.assertIsNone(row["current_roi"])
                self.assertTrue(row["target_warnings"])
        unreachable = self.row(cabinet=self.cabinet.model_copy(update={"wb_extra_tariff_percent": 100}))
        self.assertIsNone(unreachable["target_price"])
        self.assertIn("недостижима", unreachable["target_warnings"][-1])

    def test_historical_current_roi_survives_target_price_blockers(self):
        row = self.row(
            reference={**self.reference, "purchase_price": None},
            current_roi=12.34,
        )
        self.assertEqual(row["current_roi"], 12.34)
        self.assertIsNone(row["target_price"])

    def test_unsaved_logistics_use_the_same_defaults_as_the_calculator(self):
        implicit = self.row(product_settings=None)
        explicit = self.row(product_settings=UnitEconomics1CProductSettings(store_slug="rimili", article="123"))
        self.assertEqual(implicit["current_roi"], explicit["current_roi"])
        self.assertEqual(implicit["target_price"], explicit["target_price"])
        self.assertIsNotNone(implicit["current_roi"])
        self.assertEqual(implicit["current_warnings"], [])
        self.assertIn("значения по умолчанию", " ".join(implicit["current_notes"]))
        self.assertIn("значения по умолчанию", " ".join(implicit["target_warnings"]))

    def test_roi_uses_spend_per_unit_even_when_turnover_and_drr_are_unknown(self):
        orders = {day: {"orders_count": 10, "orders_amount": 0} for day in self.days}
        weekly = self.metrics(orders=orders)
        self.assertIsNone(weekly["drr"])
        self.assertEqual(weekly["advertising_per_unit"], 50)
        row = self.row(weekly=weekly)
        self.assertEqual(row["current_roi"], self.row()["current_roi"])
        self.assertEqual(row["current_warnings"], [])
        self.assertTrue(row["current_drr_warnings"])

    def test_confirmed_no_advertising_allows_roi_without_orders_but_missing_data_does_not(self):
        orders = {day: {"orders_count": 0, "orders_amount": 0} for day in self.days}
        weekly = self.metrics(orders=orders, ads={}, complete=set(self.days))
        row = self.row(weekly=weekly)
        self.assertEqual(row["current_drr"], 0)
        self.assertIsNotNone(row["current_roi"])
        self.assertEqual(row["current_warnings"], [])
        for missing in (self.metrics(orders=orders, ads={}), self.metrics(orders=orders)):
            row = self.row(weekly=missing)
            self.assertIsNone(row["current_roi"])
            self.assertTrue(row["current_warnings"])

    def test_partial_week_and_target_only_gaps_do_not_flag_calculated_current_metrics(self):
        weekly = self.metrics(orders={day: self.orders[day] for day in self.days[:3]})
        row = self.row(weekly=weekly, price={**self.price, "customer_price_with_wallet": None})
        self.assertEqual(row["current_drr"], 6.25)
        self.assertIsNotNone(row["current_roi"])
        self.assertEqual(row["current_drr_warnings"], [])
        self.assertEqual(row["current_warnings"], [])
        self.assertIn("3 из 7", " ".join(row["current_drr_notes"]))
        self.assertIn("3 из 7", " ".join(row["current_notes"]))
        self.assertIn("кошельком", " ".join(row["target_warnings"]))
        self.assertNotIn("кошельком", " ".join(row["current_notes"]))

    def test_actual_zero_drr_is_preserved_without_warning(self):
        row = self.row(weekly=self.metrics(ads={}, complete=set(self.days)))
        self.assertEqual(row["current_drr"], 0)
        self.assertEqual(row["current_drr_warnings"], [])
        self.assertEqual(row["current_drr_notes"], [])

    def test_unknown_wallet_and_stale_prices_are_explicit(self):
        row = self.row(price={**self.price, "customer_price_with_wallet": None, "day": "2026-08-20"})
        self.assertIsNone(row["current_price"])
        self.assertIsNone(row["target_price"])
        self.assertIsNotNone(row["target_retail_price"])
        self.assertIn("2026-08-20", " ".join(row["target_warnings"]))

    def test_no_sales_can_still_get_target_from_price_and_goals(self):
        row = self.row(weekly=self.metrics(orders={}, ads={}))
        self.assertIsNone(row["current_drr"])
        self.assertIsNone(row["current_roi"])
        self.assertIsNotNone(row["target_price"])
        self.assertEqual(
            row["target_advertising_rub"],
            pricing.calculate_target_advertising_rub(row["target_retail_price"], 8, 80),
        )
        self.assertLessEqual(abs(row["target_actual_roi"] - 50), 0.1)

    def test_loader_queries_closed_dates_and_does_not_treat_failed_sync_as_zero(self):
        with mock.patch.multiple(
            pricing.db,
            get_unit_economics_1c_funnel_daily_order_rows=mock.DEFAULT,
            get_unit_economics_1c_daily_advertising=mock.DEFAULT,
            list_unit_economics_1c_advertising_sync_states=mock.DEFAULT,
            get_unit_economics_1c_funnel_product_metrics=mock.DEFAULT,
            list_unit_economics_1c_cabinet_settings=mock.DEFAULT,
            get_stock_items=mock.DEFAULT,
        ) as calls:
            calls["get_stock_items"].return_value = [{"article": "123"}]
            calls["list_unit_economics_1c_cabinet_settings"].return_value = [self.cabinet]
            calls["get_unit_economics_1c_funnel_product_metrics"].return_value = [
                {"store_slug": "rimili", "article": "123", "buyout_percent": 80}
            ]
            calls["get_unit_economics_1c_funnel_daily_order_rows"].return_value = [
                {"store_slug": "rimili", "article": "123", "day": day, **self.orders[day]}
                for day in self.days
            ]
            calls["get_unit_economics_1c_daily_advertising"].return_value = []
            state = {
                "store_slug": "rimili",
                "status": "ok",
                "period_from": self.days[0],
                "period_to": self.days[-1],
                "last_success_at": "2026-09-03T01:00:00+03:00",
            }
            calls["list_unit_economics_1c_advertising_sync_states"].return_value = [state]
            loaded = pricing.load_weekly_metrics(("rimili",), date(2026, 9, 3))[("rimili", "123")]
            self.assertEqual(loaded["drr"], 0)
            self.assertTrue(loaded["complete"])
            calls["get_unit_economics_1c_daily_advertising"].assert_called_once_with(
                ("rimili",), self.days[0], self.days[-1]
            )
            calls["list_unit_economics_1c_advertising_sync_states"].return_value = [
                {**state, "status": "error"}
            ]
            self.assertIsNone(
                pricing.load_weekly_metrics(("rimili",), date(2026, 9, 3))[("rimili", "123")]["drr"]
            )
