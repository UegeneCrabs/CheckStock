"""Protect existing WB arithmetic while changing how the target ROI is selected."""

import unittest
from datetime import date, timedelta

from app import unit_economics_1c_target_prices as pricing
from app.dto.unit_economics_1c import UnitEconomics1CCabinetSettings, UnitEconomics1CProductSettings


class TargetPriceCalculationInvariantTests(unittest.TestCase):
    def setUp(self):
        days = [(date(2026, 8, 27) + timedelta(days=offset)).isoformat() for offset in range(7)]
        self.weekly = pricing.weekly_metrics(
            days,
            {day: {"orders_count": 10, "orders_amount": 8000} for day in days},
            {day: {"spend": 400} for day in days},
            set(),
            {"buyout_percent": 80},
            None,
        )
        self.cabinet = UnitEconomics1CCabinetSettings(store_slug="rimili", usn_percent=6)
        self.settings = UnitEconomics1CProductSettings(
            store_slug="rimili", article="123", delivery_wb_rub=45,
            return_cost_rub=20, storage_wb_rub=1,
        )
        self.reference = {
            "purchase_price": 300, "fulfillment_cost": 50, "subject_commission_percent": 10,
            "team_commission_percent": 4, "turnover_days": 21,
        }
        self.price = {
            "retail_price": 1000, "customer_price_with_spp": 800,
            "customer_price_with_wallet": 784, "day": days[-1],
        }

    def row(self, *, cabinet=None, code=None, settings=None, **extra):
        return pricing.calculate_row(
            price=self.price,
            reference={**self.reference, "abc_code": code},
            product_settings=self.settings if settings is None else settings,
            cabinet=self.cabinet if cabinet is None else cabinet,
            weekly=self.weekly,
            **extra,
        )

    def target_values(self, row):
        return tuple(row[key] for key in (
            "target_price", "target_retail_price", "target_spp_price",
            "target_actual_roi", "target_advertising_rub",
        ))

    def current_values(self, row):
        return {key: value for key, value in row.items() if key.startswith("current_") or key == "weekly"}

    def test_code_defaults_preserve_recorded_prechange_prices_and_current_metrics(self):
        # Recorded before adding per-code goals, using the legacy scalar ROI.
        golden = {
            "usn": {
                0: (519, 662.17, 530, 0, 42.38),
                20: (591, 754.75, 604, 20, 48.3),
                30: (628, 801.06, 641, 30, 51.27),
                50: (700, 893.65, 715, 50, 57.19),
            },
            "osno": {
                0: (632, 806.42, 645, 0, 51.61),
                20: (720, 919.12, 735, 20, 58.82),
                30: (765, 975.82, 781, 30, 62.45),
                50: (853, 1088.54, 871, 50, 69.67),
            },
        }
        expected_goals = {"A": 20, "B": 30, "C": 50, "D": 0, "F": 50, "NEW": 50, "U": 20}
        for tax_system, expected_current_roi in (("usn", 77.64), ("osno", 38.98)):
            cabinet = self.cabinet.model_copy(update={
                "store_slug": "gogol" if tax_system == "osno" else "rimili",
                "tax_system": tax_system,
                "osno_percent": 20 if tax_system == "osno" else 0,
            })
            current_baseline = self.current_values(self.row(cabinet=cabinet))
            for code, goal in expected_goals.items():
                with self.subTest(tax_system=tax_system, code=code):
                    selected = self.row(cabinet=cabinet, code=code)
                    legacy = self.row(cabinet=cabinet.model_copy(update={"target_roi_percent": goal}))
                    self.assertEqual(selected["target_roi"], goal)
                    self.assertEqual(self.target_values(selected), golden[tax_system][goal])
                    self.assertEqual(self.target_values(selected), self.target_values(legacy))
                    self.assertEqual(self.current_values(selected), current_baseline)
                    self.assertEqual(selected["current_roi"], expected_current_roi)
                    self.assertEqual(selected["calculator"], legacy["calculator"])

    def test_custom_code_goal_changes_only_target_economics_and_resets_to_code_goal(self):
        cabinet = self.cabinet.model_copy(update={
            "target_roi_by_code": {**self.cabinet.target_roi_by_code, "A": 73.5, "D": 12},
        })
        custom = self.row(cabinet=cabinet, code=" a ")
        legacy = self.row(cabinet=self.cabinet.model_copy(update={"target_roi_percent": 73.5}))
        self.assertEqual(custom, legacy)

        for override in (0, 20):
            with self.subTest(override=override):
                settings = self.settings.model_copy(update={"target_roi_percent": override, "target_drr_percent": 0})
                overridden = self.row(cabinet=cabinet, code="A", settings=settings)
                legacy_override = self.row(
                    cabinet=self.cabinet.model_copy(update={"target_roi_percent": override, "target_drr_percent": 0})
                )
                self.assertEqual(overridden["target_roi"], override)
                self.assertEqual(overridden["cabinet_target_roi"], 73.5)
                self.assertEqual(overridden["target_drr"], 0)
                self.assertEqual(self.target_values(overridden), self.target_values(legacy_override))
                self.assertEqual(self.current_values(overridden), self.current_values(custom))
                reset = self.row(cabinet=cabinet, code="A", settings=settings.model_copy(update={
                    "target_roi_percent": None, "target_drr_percent": None,
                }))
                self.assertEqual(reset, custom)

    def test_absent_and_unknown_codes_keep_legacy_fallback(self):
        cabinet = self.cabinet.model_copy(update={"target_roi_percent": 67.5})
        expected = self.row(cabinet=cabinet)
        for code in (None, "", "  ", "UNKNOWN", "-", "0"):
            with self.subTest(code=code):
                actual = self.row(cabinet=cabinet, code=code)
                self.assertEqual(actual, expected)
                self.assertEqual(actual["target_roi"], 67.5)
                self.assertEqual(actual["calculator"]["cabinet_target_roi"], 67.5)

    def test_historical_current_roi_is_preserved_including_zero(self):
        for code in ("A", "D", "NEW"):
            for current_roi in (None, 0, -8.25, 16.5):
                with self.subTest(code=code, current_roi=current_roi):
                    row = self.row(code=code, current_roi=current_roi)
                    self.assertEqual(row["current_roi"], current_roi)
                    self.assertIsNotNone(row["target_price"])
