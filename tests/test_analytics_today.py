"""Today's ranking, shared weekly reads and lossless compact ledger loading."""

import json
import os
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import dotenv

with (
    patch.object(dotenv, "dotenv_values", return_value={}),
    patch.dict(os.environ, {"CHECKSTOCK_DATABASE_URL": ""}),
):
    from app import db
    from app.analytics import analyzer, ephemerides, sales_api
    from app.dto.identity import User
    from app.infrastructure.database import Database
    from app.infrastructure.orm import OrmBase
    from app.repositories import core, daily_economics


class AnalyticsTodayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="analytics-today-")
        self.database = Database(path=Path(self.directory.name) / "test.sqlite")
        OrmBase.metadata.create_all(self.database.engine)
        self.patch = patch.object(core, "database_for_path", return_value=self.database)
        self.patch.start()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.database.dispose)
        self.addCleanup(self.patch.stop)
        self.user = User(
            id=1, login="test", full_name="Test", role="superadmin", created_at=datetime.now(UTC)
        )
        self.today = date(2026, 10, 7)
        with self.database.connect() as conn:
            for store, article in (("rimili", "1"), ("rimili", "2"), ("rimili", "3"), ("tris", "1")):
                conn.execute(
                    "INSERT INTO stock_items (store_slug,marketplace,article,name,barcode) VALUES (?,'WB',?,?,?)",
                    (store, article, article, article),
                )
            # The older period's leader must not dictate today's ranking.
            for article, day, amount in (
                ("1", "2026-09-21", 9000),
                ("2", "2026-09-21", 1),
                ("1", "2026-10-07", 100),
                ("2", "2026-10-07", 500),
            ):
                conn.execute(
                    "INSERT INTO wb_funnel_daily_orders (store_slug,article,day,orders_count,orders_amount,cancel_count,source_version,updated_at) VALUES ('rimili',?,?,10,?,9,4,'2026-10-07')",
                    (article, day, amount),
                )
            conn.commit()

    def test_all_reports_rank_by_today_even_for_old_period(self):
        stores, week = ("rimili", "tris"), date(2026, 9, 21)
        reports = [
            (
                sales_api.load(stores, self.user, week, date(2026, 9, 27), today=self.today),
                lambda r: r["today_turnover"],
            ),
            (analyzer.load(stores, week, self.user, today=self.today), lambda r: r["fact"]),
            (ephemerides.load(stores, week, self.user, today=self.today), lambda r: r["dayTurnover"]["fact"]),
        ]
        for report, value in reports:
            self.assertEqual(
                [r["id"] for r in report["rows"]], ["rimili:2", "rimili:1", "rimili:3", "tris:1"]
            )
            self.assertEqual([value(r) for r in report["rows"]], [500, 100, 0, None])

    def test_today_inside_sales_period_has_same_rank_as_outside(self):
        for start in (date(2026, 9, 21), self.today):
            result = sales_api.load(("rimili", "tris"), self.user, start, start, today=self.today)
            self.assertEqual([r["today_turnover"] for r in result["rows"]], [500, 100, 0, None])

    def test_ephemerides_load_common_sources_once(self):
        with (
            patch.object(
                db,
                "get_unit_economics_1c_daily_margin_snapshots",
                wraps=db.get_unit_economics_1c_daily_margin_snapshots,
            ) as snapshots,
            patch.object(db, "get_stock_items", wraps=db.get_stock_items) as products,
        ):
            ephemerides.load(("rimili",), date(2026, 10, 5), self.user, today=self.today)
        self.assertEqual(snapshots.call_count, 1)
        self.assertEqual(products.call_count, 1)

    def test_streamed_compact_payload_retains_version_overlays_and_reference(self):
        saved = {
            "source": {
                "values": {"purchase_price": 10},
                "version": 3,
                "extra": [1, "я"],
                "raw": {"reference": {"goal_week": 70}, "audit": "x" * 10000},
            },
            "original": {"immutable": True},
            "overrides": {"purchase_price": 15},
        }
        serialized = json.dumps(saved, ensure_ascii=False)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO economics_daily (marketplace,store_slug,article,day,revision,payload_json,updated_at) VALUES ('WB','rimili','1','2026-10-05',7,?,'2026-10-07')",
                (serialized,),
            )
            conn.commit()
        row = daily_economics.records(
            "WB",
            ("rimili",),
            "2026-10-05",
            "2026-10-05",
            compact=True,
            inputs_only=True,
            include_reference=True,
        )[0]
        self.assertEqual(row["values"], {"purchase_price": 15})
        self.assertEqual(row["version"], 3)
        self.assertEqual(row["revision"], 7)
        self.assertEqual(row["source"]["extra"], [1, "я"])
        self.assertNotIn("raw", row["source"])
        self.assertEqual(json.loads(row["reference_json"]), {"goal_week": 70})
        with self.database.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT payload_json FROM economics_daily").fetchone()[0], serialized
            )
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM economics_daily_events").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
