import asyncio
import logging
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from fastapi import FastAPI

from app import background, health, unit_costs


class HealthTests(unittest.TestCase):
    def test_credentials_dates_tokens_and_problems(self) -> None:
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.json"
            path.write_text("{}", encoding="utf-8")
            with mock.patch.object(health.wb_tokens, "TOKENS_PATH", path):
                self.assertIsNotNone(health._credentials_updated_at("WB"))
            with mock.patch.object(health.ozon_tokens, "TOKENS_PATH", path):
                self.assertIsNotNone(health._credentials_updated_at("OZON"))
            with mock.patch.object(health.ya_tokens, "SECRETS_PATH", path):
                self.assertIsNotNone(health._credentials_updated_at("YANDEX MARKET"))
        self.assertIsNone(health._credentials_updated_at("wrong"))
        self.assertIsNone(health._checked_at({}))
        self.assertIsNotNone(health._checked_at({"checked_at": now.replace(tzinfo=None).isoformat()}))
        self.assertIsNotNone(health._checked_at({"checked_at": now.isoformat()}))

        rows = [
            {"checked_at": (now - timedelta(days=1)).isoformat()},
            {"checked_at": (now + timedelta(days=1)).isoformat()},
            {"checked_at": "bad"},
        ]
        with mock.patch.object(health, "_credentials_updated_at", return_value=now):
            self.assertEqual(len(health._fresh_health_rows("WB", rows)), 2)
        with mock.patch.object(health, "_credentials_updated_at", return_value=None):
            self.assertIs(health._fresh_health_rows("WB", rows), rows)

        token_modules = [
            ("WB", health.wb_tokens, "is_listed", "has_token"),
            ("OZON", health.ozon_tokens, "is_listed", "has_credentials"),
            ("YANDEX MARKET", health.ya_tokens, "is_listed", "has_credentials"),
        ]
        for marketplace, module, listed, present in token_modules:
            with mock.patch.object(module, listed, return_value=True):
                self.assertTrue(health.is_listed(marketplace, "store"))
            with mock.patch.object(module, present, return_value=True):
                self.assertTrue(health.has_token(marketplace, "store"))
        self.assertFalse(health.is_listed("wrong", "store"))
        self.assertFalse(health.has_token("wrong", "store"))

        health_rows = [
            {"marketplace": "WB", "scope": "catalog", "checked_at": now.isoformat()},
            {"marketplace": "OZON", "scope": "catalog", "checked_at": now.isoformat()},
            {"marketplace": "OZON", "scope": "stocks", "checked_at": now.isoformat()},
        ]
        with (
            mock.patch.object(health.db, "get_sync_health", return_value=health_rows),
            mock.patch.object(health, "has_token", return_value=True),
            mock.patch.object(health, "_credentials_updated_at", return_value=None),
        ):
            problems = health.store_problems("store")
        self.assertEqual(len(problems), 2)
        self.assertNotEqual(problems[0]["status"], problems[1]["status"])
        with (
            mock.patch("app.stores.STORES", {"a": {}, "b": {}}),
            mock.patch.object(health, "store_problems", side_effect=lambda slug: [slug]),
        ):
            self.assertEqual(health.stores_with_problems(), {"a": ["a"], "b": ["b"]})


class UnitCostsTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_parsing_and_sync(self) -> None:
        headers = [["АртикулВБ", "Себес, руб", "Проч. затр, руб"], ["1001.0", "1 234,5", "10"]]
        parsed = unit_costs.parse_cost_rows(headers)
        self.assertEqual(parsed[0]["article"], "1001")
        self.assertEqual(parsed[0]["purchase_price"], 1234.5)
        self.assertEqual(unit_costs._parse_decimal("bad"), None)
        self.assertEqual(unit_costs._normalize_article(" 100.0 "), "100")
        self.assertIn("gid=1", unit_costs._sheet_url("1"))
        with self.assertRaises(unit_costs.UnitCostSyncError):
            unit_costs.parse_cost_rows([["wrong"]])
        with self.assertRaises(unit_costs.UnitCostSyncError):
            unit_costs.parse_cost_rows([["АртикулВБ", "Себес, руб"], ["", ""]])

        with (
            mock.patch.object(unit_costs, "WB_COST_SHEETS", (("1", ("good",)), ("2", ("bad",)))),
            mock.patch.object(
                unit_costs.importer,
                "fetch_google_sheet_rows",
                side_effect=[headers, ValueError("boom")],
            ),
            mock.patch.object(unit_costs.db, "replace_unit_costs", return_value=1),
            mock.patch.object(unit_costs.db, "record_sync_health") as health_record,
        ):
            report = unit_costs.sync_all()
        self.assertTrue(report["good"]["ok"])
        self.assertFalse(report["bad"]["ok"])
        self.assertEqual(health_record.call_count, 2)


class BackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        logging.disable(logging.CRITICAL)

    async def asyncTearDown(self) -> None:
        logging.disable(logging.NOTSET)

    async def test_groups_jobs_initialization_and_lifespan(self) -> None:
        report = background._run_sync_group(
            "group",
            (("good", lambda: {"ok": True}), ("bad", lambda: (_ for _ in ()).throw(ValueError("boom")))),
        )
        self.assertEqual(report.succeeded, ("good",))
        self.assertEqual(report.failed[0].target, "bad")
        with (
            mock.patch.object(background.wb_catalog, "sync_all", return_value={}),
            mock.patch.object(background.ozon_catalog, "sync_all", return_value={}),
            mock.patch.object(background.ya_catalog, "sync_all", return_value={}),
        ):
            self.assertEqual(set(background._sync_catalogs().succeeded), {"WB", "OZON", "YANDEX MARKET"})
        with (
            mock.patch.object(background.wb_sync, "sync_all", return_value={}),
            mock.patch.object(background.ozon_sync, "sync_all", return_value={}),
            mock.patch.object(background.ya_sync, "sync_all", return_value={}),
        ):
            self.assertEqual(set(background._sync_stocks().succeeded), {"WB", "OZON", "YANDEX MARKET"})
        with (
            mock.patch.object(background.db, "get_last_token_check", return_value="now"),
            mock.patch.object(background.token_watch, "should_refresh", return_value=False),
        ):
            self.assertFalse(background._refresh_token_info().refreshed)
        with (
            mock.patch.object(background.db, "get_last_token_check", return_value=None),
            mock.patch.object(background.token_watch, "should_refresh", return_value=True),
            mock.patch.object(background.token_watch, "refresh_token_info") as refresh,
        ):
            self.assertTrue(background._refresh_token_info().refreshed)
        refresh.assert_called_once()
        self.assertEqual(background._fixed_delay(5)(), 5)
        self.assertGreater(background._daily_delay(3)(), 0)
        self.assertEqual(len(background._jobs(asyncio.Event())), 9)

        with (
            mock.patch.object(background.db, "init_db") as init_db,
            mock.patch.object(background.decision_service, "init_schema") as decision_schema,
            mock.patch.object(background.rnp_analytics, "init_schema") as rnp_schema,
            mock.patch.object(background.db, "seed_defaults") as seed,
            mock.patch.object(background.auth, "seed_superadmin") as seed_admin,
        ):
            background._initialize_application()
        for called in (init_db, decision_schema, rnp_schema, seed, seed_admin):
            called.assert_called_once()

        app = FastAPI()
        lifespan_settings = mock.Mock(background_sync_enabled=False, database_path="test.sqlite3")
        with (
            mock.patch.object(background, "_initialize_application"),
            mock.patch.object(background, "settings", lifespan_settings),
        ):
            async with background.lifespan(app):
                self.assertEqual(app.state.background_tasks, [])


if __name__ == "__main__":
    unittest.main()
