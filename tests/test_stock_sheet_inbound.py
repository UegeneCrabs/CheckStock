"""Inbound fallback checks use synthetic DTOs and isolated SQLite without external APIs."""

import importlib
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class StockSheetInboundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.export = importlib.import_module("app.exports.stock_sheet_inbound")
            cls.dto = importlib.import_module("app.dto.inbound_supplies")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.repository_module = importlib.import_module("app.infrastructure.inbound_repository")
            cls.orm = importlib.import_module("app.infrastructure.orm")

    def setUp(self):
        self.now = datetime(2026, 10, 5, 9, tzinfo=UTC)
        self.old_success = (self.now - timedelta(days=5)).isoformat()
        self.catalog = [{"article": "SKU-A", "name": "Product", "barcode": "barcode-a"}]
        patcher = patch.object(self.export, "settings", SimpleNamespace(inbound_sync_interval_seconds=900))
        patcher.start()
        self.addCleanup(patcher.stop)

    def supply(self, *, article="SKU-A", quantity=7, **changes):
        values = {
            "key": "supply-a",
            "supply_id": "1",
            "status": "IN_TRANSIT",
            "status_label": "В пути",
            "stage": "transit",
            "items": (self.dto.InboundItem(article=article, quantity=quantity),),
        }
        return self.dto.InboundSupply(**{**values, **changes})

    def snapshot(self, *, marketplace="WB", **changes):
        values = {
            "store_slug": "rimili",
            "marketplace": marketplace,
            "status": "ok",
            "last_success": self.old_success,
            "supplies": (self.supply(),),
        }
        return self.dto.InboundSnapshot(**{**values, **changes})

    def summarize(self, snapshot, *, saved=True, catalog=None, **kwargs):
        return self.export.summarize(
            snapshot,
            self.catalog if catalog is None else catalog,
            self.now,
            allow_saved_snapshot=saved,
            **kwargs,
        )

    def test_stale_success_reuses_confirmed_values_for_each_supported_marketplace(self):
        for marketplace in ("WB", "OZON", "YANDEX MARKET"):
            with self.subTest(marketplace=marketplace):
                result = self.summarize(self.snapshot(marketplace=marketplace))
                self.assertTrue(result.available)
                self.assertEqual(result.quantities, {"SKU-A": 7})
                self.assertEqual(result.confirmed_quantities, {"SKU-A": 7})
                self.assertEqual(len(result.warnings), 1)
                self.assertIn("30.09.2026 12:00:00 МСК", result.warnings[0])
                self.assertIn("последний сохранённый", result.warnings[0])

    def test_failed_or_unconfigured_refresh_keeps_last_successful_payload(self):
        for marketplace in ("WB", "OZON", "YANDEX MARKET"):
            for status in ("error", "not_configured", "running"):
                with self.subTest(marketplace=marketplace, status=status):
                    result = self.summarize(
                        self.snapshot(marketplace=marketplace, status=status, error="Refresh unavailable")
                    )
                    self.assertTrue(result.available)
                    self.assertEqual(result.quantities["SKU-A"], 7)
                    self.assertIn("Свежие данные пока не подтверждены", result.warnings[0])

    def test_recent_running_snapshot_is_explicitly_labelled_as_saved(self):
        result = self.summarize(
            self.snapshot(status="running", last_success=(self.now - timedelta(minutes=5)).isoformat())
        )
        self.assertEqual(result.quantities["SKU-A"], 7)
        self.assertIn("05.10.2026 11:55:00 МСК", result.warnings[0])

    def test_fresh_success_keeps_existing_results_without_fallback_warning(self):
        snapshot = self.snapshot(last_success=(self.now - timedelta(minutes=5)).isoformat())
        strict = self.summarize(snapshot, saved=False)
        saved = self.summarize(snapshot)
        self.assertEqual(saved, strict)
        self.assertTrue(saved.available)
        self.assertEqual(saved.quantities, {"SKU-A": 7})
        self.assertFalse(saved.warnings)

    def test_default_remains_strict_for_pricing_consumers(self):
        for changes in ({}, {"status": "error", "last_success": self.now.isoformat(), "error": "Failed"}):
            with self.subTest(changes=changes):
                result = self.export.summarize(self.snapshot(**changes), self.catalog, self.now)
                self.assertFalse(result.available)
                self.assertEqual(result.quantities, {"SKU-A": None})
                self.assertFalse(result.confirmed_quantities)
                self.assertIn("Нет полного свежего снимка", result.warnings[0])

    def test_missing_or_invalid_last_success_never_confirms_zero_or_payload(self):
        for timestamp in (None, "", "not-a-date"):
            for supplies in ((), (self.supply(),)):
                with self.subTest(timestamp=timestamp, supplies=bool(supplies)):
                    result = self.summarize(self.snapshot(last_success=timestamp, supplies=supplies))
                    self.assertFalse(result.available)
                    self.assertEqual(result.quantities, {"SKU-A": None})
                    self.assertFalse(result.confirmed_quantities)
                    self.assertIn("пустыми", result.warnings[0])

    def test_successful_empty_snapshot_remains_a_confirmed_zero_after_refresh_failure(self):
        for marketplace in ("WB", "OZON", "YANDEX MARKET"):
            with self.subTest(marketplace=marketplace):
                result = self.summarize(
                    self.snapshot(marketplace=marketplace, status="error", error="Failed", supplies=())
                )
                self.assertTrue(result.available)
                self.assertEqual(result.quantities, {"SKU-A": 0})
                self.assertIn("последний сохранённый", result.warnings[0])

    def test_unknown_quantity_stays_blank_without_hiding_another_confirmed_article(self):
        catalog = [*self.catalog, {"article": "SKU-B", "name": "Second"}]
        for changes in (
            {"stage": "unknown"},
            {"stage": "discrepancy"},
            {"unavailable": True},
            {"stage": "placement"},
        ):
            with self.subTest(changes=changes):
                snapshot = self.snapshot(
                    supplies=(self.supply(**changes), self.supply(article="SKU-B", quantity=4))
                )
                result = self.summarize(snapshot, catalog=catalog)
                self.assertTrue(result.available)
                self.assertEqual(result.quantities, {"SKU-A": None, "SKU-B": 4})
                self.assertEqual(result.confirmed_quantities, {"SKU-B": 4})
                self.assertEqual(len(result.warnings), 2)
                self.assertIn("не подтверждена", result.warnings[1])

    def test_incomplete_or_unidentifiable_supply_never_becomes_zero(self):
        for incomplete in (self.supply(items=()), self.supply(article="")):
            with self.subTest(incomplete=incomplete):
                result = self.summarize(self.snapshot(supplies=(incomplete,)))
                self.assertFalse(result.available)
                self.assertEqual(result.quantities, {"SKU-A": None})
                self.assertFalse(result.confirmed_quantities)
                self.assertIn("пустыми", result.warnings[0])
                self.assertNotIn("Использован", result.warnings[0])

    def test_ambiguous_alias_does_not_duplicate_or_zero_saved_quantity(self):
        catalog = [{"article": "SKU-A", "mp_sku": "shared"}, {"article": "SKU-B", "mp_sku": "shared"}]
        result = self.summarize(self.snapshot(supplies=(self.supply(article="shared"),)), catalog=catalog)
        self.assertEqual(result.quantities, {"SKU-A": None, "SKU-B": None})
        self.assertFalse(result.confirmed_quantities)

    def test_saved_payload_still_uses_marketplace_receipt_rules(self):
        wb_item = self.dto.InboundItem(article="SKU-A", quantity=10, accepted_quantity=8, ready_quantity=3)
        wb = self.snapshot(supplies=(self.supply(status="5", stage="completed", items=(wb_item,)),))
        self.assertEqual(self.summarize(wb).quantities["SKU-A"], 5)
        for marketplace, status in (("OZON", "COMPLETED"), ("YANDEX MARKET", "FINISHED")):
            with self.subTest(marketplace=marketplace):
                snapshot = self.snapshot(
                    marketplace=marketplace,
                    supplies=(self.supply(status=status, stage="placement"),),
                )
                self.assertEqual(self.summarize(snapshot).quantities["SKU-A"], 0)

    def test_yandex_planned_supply_keeps_existing_opt_in(self):
        snapshot = self.snapshot(
            marketplace="YANDEX MARKET",
            supplies=(self.supply(status="ACCEPTED_BY_WAREHOUSE_SYSTEM", stage="planned"),),
        )
        self.assertEqual(self.summarize(snapshot).quantities["SKU-A"], 0)
        self.assertEqual(self.summarize(snapshot, include_yandex_approved=True).quantities["SKU-A"], 7)

    def test_load_reads_snapshot_and_forwards_saved_opt_in_without_refreshing(self):
        service = MagicMock()
        service.report.return_value = [self.snapshot(status="error", error="Latest refresh failed")]
        with patch.object(self.export.inbound_supplies, "build_service", return_value=service):
            saved = self.export.load("rimili", "WB", self.catalog, now=self.now, allow_saved_snapshot=True)
            strict = self.export.load("rimili", "WB", self.catalog, now=self.now)
        service.report.assert_called_with((("rimili", "WB"),))
        service.sync.assert_not_called()
        self.assertEqual(saved.quantities["SKU-A"], 7)
        self.assertIsNone(strict.quantities["SKU-A"])

    def test_failed_repository_refresh_retains_payload_for_saved_export(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-inbound-fallback-")
        self.addCleanup(directory.cleanup)
        database = self.database_module.Database(Path(directory.name) / "snapshot.sqlite3")
        self.addCleanup(database.dispose)
        self.orm.InboundSupplySnapshotRecord.__table__.create(database.engine)
        repository = self.repository_module.SqlAlchemyInboundRepository(database.session_factory)
        target = ("rimili", "WB")
        saved_at = datetime.fromisoformat(self.old_success)
        self.assertTrue(repository.claim(target, "successful-run", saved_at))
        self.assertTrue(repository.finish(target, "successful-run", saved_at, supplies=(self.supply(),)))
        before = repository.read((target,))[0]
        self.assertTrue(repository.claim(target, "failed-run", self.now))
        self.assertTrue(repository.finish(target, "failed-run", self.now, status="error", error="API failed"))
        after = repository.read((target,))[0]
        self.assertEqual(after.status, "error")
        self.assertEqual(after.last_success, before.last_success)
        self.assertEqual(after.supplies, before.supplies)
        exported = self.summarize(after)
        self.assertEqual(exported.quantities["SKU-A"], 7)
        self.assertIn("30.09.2026 12:00:00 МСК", exported.warnings[0])


if __name__ == "__main__":
    unittest.main()
