import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import db, unit_economics_1c_history
from app.dto.unit_economics_1c import UnitEconomics1CProductSettings
from app.repositories import core


class UnitEconomics1CHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path_patch = mock.patch.object(core, "DB_PATH", Path(self.temp.name) / "history.sqlite3")
        self.path_patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp.cleanup()

    def test_daily_margin_snapshot_is_immutable_without_explicit_repair(self) -> None:
        row = {
            "store_slug": "rimili",
            "article": "1001",
            "day": "2026-08-27",
            "marketplace": "WB",
            "unit_margin": 125.5,
            "purchase_price": 50,
            "price_day": "2026-08-27",
            "calculation_version": 1,
            "inputs_json": "{}",
            "result_json": "{}",
            "captured_at": "2026-08-28T00:00:00+00:00",
        }

        self.assertEqual(db.save_unit_economics_1c_daily_margin_snapshots([row]), 1)
        changed = {**row, "unit_margin": 999}
        self.assertEqual(db.save_unit_economics_1c_daily_margin_snapshots([changed]), 0)
        saved = db.get_unit_economics_1c_daily_margin_snapshots(
            ("rimili",), "2026-08-27", "2026-08-27"
        )
        self.assertEqual(saved[0]["unit_margin"], 125.5)

        self.assertEqual(
            db.save_unit_economics_1c_daily_margin_snapshots([changed], overwrite=True),
            1,
        )
        repaired = db.get_unit_economics_1c_daily_margin_snapshots(
            ("rimili",), "2026-08-27", "2026-08-27"
        )
        self.assertEqual(repaired[0]["unit_margin"], 999)

    def test_snapshot_keeps_resolved_inputs_and_formula_version(self) -> None:
        row = unit_economics_1c_history.calculate_snapshot_row(
            snapshot_day=date(2026, 8, 27),
            store_slug="rimili",
            article="1001",
            price_snapshot={
                "day": "2026-08-27",
                "retail_price": 1000,
                "customer_price_with_spp": 900,
                "updated_at": "price-sync",
            },
            product_metrics={
                "orders_count": 10,
                "orders_amount": 9000,
                "spend": 500,
                "buyout_percent": 80,
                "buyout_period_from": "2026-08-21",
                "buyout_period_to": "2026-08-27",
            },
            product_settings=UnitEconomics1CProductSettings(
                store_slug="rimili",
                article="1001",
                delivery_wb_rub=50,
                return_cost_rub=25,
                volume_l=2,
                storage_wb_rub=1,
            ),
            product_reference={
                "purchase_price": 300,
                "fulfillment_cost": 40,
                "team_commission_percent": 2,
                "turnover_days": 10,
                "subject_commission_percent": 15,
                "source_synced_at": "source-sync",
            },
            cabinet=SimpleNamespace(
                buyout_period_days=21,
                acceptance_coefficient=1,
                wb_extra_tariff_percent=1,
                acquiring_percent=3,
                team_commission_percent=5,
                vat_percent=7,
                usn_percent=6,
                osno_percent=0,
                tax_system="usn",
                updated_at="cabinet-update",
            ),
            captured_at="2026-08-28T00:00:00+00:00",
        )

        self.assertIsNotNone(row)
        assert row is not None
        inputs = json.loads(row["inputs_json"])
        result = json.loads(row["result_json"])
        self.assertEqual(row["calculation_version"], 3)
        self.assertEqual(row["price_day"], "2026-08-27")
        self.assertEqual(inputs["purchase_price"], 300)
        self.assertEqual(inputs["fulfillment_cost"], 40)
        self.assertEqual(inputs["buyout_percent"], 80)
        self.assertEqual(inputs["buyout_period_days"], 21)
        self.assertFalse(inputs["advertising_included_in_unit_margin"])
        self.assertEqual(inputs["advertising_per_unit"], 62.5)
        self.assertEqual(result["advertising"], 0)
        self.assertEqual(inputs["source_synced_at"], "source-sync")
        self.assertIsInstance(row["unit_margin"], float)

    def test_legacy_snapshot_advertising_is_removed_before_period_calculation(self) -> None:
        margin = unit_economics_1c_history.unit_margin_without_advertising(
            {
                "unit_margin": 503.18,
                "calculation_version": 1,
                "inputs_json": "{}",
                "result_json": json.dumps({"advertising": 304.88}),
            },
            buyout_percent=80,
        )

        self.assertEqual(margin, 808.06)

    def test_snapshot_buyout_percent_keeps_valid_zero(self) -> None:
        self.assertEqual(
            unit_economics_1c_history.snapshot_buyout_percent(
                {"inputs_json": json.dumps({"buyout_percent": 0})}
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
