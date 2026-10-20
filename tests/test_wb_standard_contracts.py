"""Regression checks for typed boundaries, persistence and job result serialization."""

import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import event
from test_wb_standard_costs import fixture

from app.application.wb_standard_costs import WbStandardCostsService
from app.container import ApplicationContainer
from app.dto.wb_standard_costs import (
    CalculatorValues,
    ExportRow,
    PublishSnapshot,
    Reports,
    Snapshot,
    SourceBundle,
    StoreReport,
    StoreSyncResult,
    SyncResult,
)
from app.economics.wb import calculator, standard_sync
from app.economics.wb.standard_costs import build
from app.economics.wb.standard_sources import calculator_product
from app.infrastructure.database import Database
from app.infrastructure.wb_standard_costs_repository import SqlAlchemyWbStandardCostsUnitOfWork
from app.jobs.tracking import result_error
from app.repositories import wb_standard_costs as repository
from app.web.routers import wb_standard_costs as web


class ContractTests(unittest.TestCase):
    def test_completed_snapshot_and_rows_are_immutable(self):
        snapshot = build(SourceBundle.model_validate(fixture()))
        with self.assertRaises(ValidationError):
            snapshot.rows[0].logistics = 0
        with self.assertRaises(ValidationError):
            snapshot.rows = ()
        self.assertIsInstance(snapshot.rows, tuple)
        self.assertIsInstance(snapshot.rows[0].missing, tuple)

    def test_export_rejects_bad_dates_periods_and_schemes(self):
        row = dict(
            store_slug="tris", article="1", scheme="FBW", period_from="2026-10-12", period_to="2026-10-18"
        )
        for overrides in (
            {"period_from": "not a date"},
            {"period_to": "2026-10-11"},
            {"scheme": "unknown"},
            {"article": ""},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                ExportRow.model_validate(row | overrides)

    def report_database(self, directory):
        database = Database(Path(directory) / "report.sqlite")
        self.addCleanup(database.dispose)
        with database.connect() as conn:
            for table in ("wb_standard_costs_snapshots", "wb_standard_costs_sources"):
                conn.execute(
                    f"CREATE TABLE {table} (store_slug TEXT PRIMARY KEY, payload TEXT, updated_at TEXT)"
                )
            conn.commit()
        service = WbStandardCostsService(
            lambda: SqlAlchemyWbStandardCostsUnitOfWork(database.session_factory)
        )
        return database, service

    def test_snapshot_update_rolls_back_when_source_write_fails(self):
        with TemporaryDirectory() as directory:
            database, service = self.report_database(directory)
            raw = SourceBundle.model_validate(fixture())
            snapshot = build(raw)
            service.publish(PublishSnapshot(store_slug="tris", snapshot=snapshot, raw=raw))
            with database.connect() as conn:
                original = conn.execute("SELECT payload FROM wb_standard_costs_sources").fetchone()[0]

            def fail_second_write(conn, cursor, statement, parameters, context, executemany):
                if statement.startswith("INSERT INTO wb_standard_costs_sources"):
                    raise RuntimeError("source write failed")

            event.listen(database.engine, "before_cursor_execute", fail_second_write)
            try:
                changed = snapshot.model_copy(update={"updated_at": "changed"})
                with self.assertRaisesRegex(RuntimeError, "source write failed"):
                    service.publish(PublishSnapshot(store_slug="tris", snapshot=changed, raw=raw))
            finally:
                event.remove(database.engine, "before_cursor_execute", fail_second_write)
            with patch.object(repository, "get_connection", database.connect):
                self.assertEqual(repository.load("tris"), snapshot)
            with database.connect() as conn:
                self.assertEqual(
                    conn.execute("SELECT payload FROM wb_standard_costs_sources").fetchone()[0], original
                )

    def test_repository_does_not_commit_without_service(self):
        with TemporaryDirectory() as directory:
            database, _ = self.report_database(directory)
            raw = SourceBundle.model_validate(fixture())
            with SqlAlchemyWbStandardCostsUnitOfWork(database.session_factory) as uow:
                uow.repository.save(PublishSnapshot(store_slug="tris", snapshot=build(raw), raw=raw))
            with patch.object(repository, "get_connection", database.connect):
                self.assertIsNone(repository.load("tris"))
            with database.connect() as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM wb_standard_costs_sources").fetchone()[0], 0
                )

    def test_publish_replaces_snapshot_and_sources_and_preserves_other_store(self):
        with TemporaryDirectory() as directory:
            database, service = self.report_database(directory)
            raw = SourceBundle.model_validate(fixture())
            snapshot = build(raw)
            for store in ("tris", "rimili"):
                service.publish(PublishSnapshot(store_slug=store, snapshot=snapshot, raw=raw))
            changed_raw = raw.model_copy(update={"cards": []})
            changed = snapshot.model_copy(update={"updated_at": "new"})
            service.publish(PublishSnapshot(store_slug="tris", snapshot=changed, raw=changed_raw))
            with patch.object(repository, "get_connection", database.connect):
                self.assertEqual(repository.load("tris"), changed)
                self.assertEqual(repository.load("rimili"), snapshot)
            with database.connect() as conn:
                rows = conn.execute(
                    "SELECT store_slug,payload FROM wb_standard_costs_sources ORDER BY store_slug"
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(json.loads(rows[0]["payload"]), raw.model_dump())
                self.assertEqual(json.loads(rows[1]["payload"]), changed_raw.model_dump())

    def test_source_and_snapshot_json_round_trip_preserves_precision_and_unknowns(self):
        raw = fixture()
        raw["cards"][0]["dimensions"]["height"] = 22.05
        raw["cards"][0]["unknown_wb_field"] = {"text": "  unchanged  ", "value": None}
        source = SourceBundle.model_validate(raw)
        self.assertEqual(json.loads(source.model_dump_json()), raw)
        snapshot = build(source)
        restored = Snapshot.model_validate_json(snapshot.model_dump_json(exclude_unset=True))
        self.assertEqual(snapshot.model_dump(exclude_unset=True), restored.model_dump(exclude_unset=True))
        self.assertEqual(restored.rows[0].volume, 2.205)
        self.assertEqual(restored.rows[0].logistics, 70)

    def test_save_load_uses_existing_sqlite_json_schema(self):
        with TemporaryDirectory() as directory:
            database, _ = self.report_database(directory)
            # Exercise the production composition root with an isolated database path.
            service = ApplicationContainer(database_path=lambda: database.path).wb_standard_costs
            raw = SourceBundle.model_validate(fixture())
            snapshot = build(raw)
            service.publish(PublishSnapshot(store_slug="tris", snapshot=snapshot, raw=raw))
            with patch.object(repository, "get_connection", database.connect):
                loaded = repository.load("tris")
                self.assertEqual(
                    loaded.model_dump(exclude_unset=True), snapshot.model_dump(exclude_unset=True)
                )
                self.assertEqual(repository.photos("tris"), [])
            with database.connect() as conn:
                self.assertEqual(
                    json.loads(conn.execute("SELECT payload FROM wb_standard_costs_sources").fetchone()[0]),
                    fixture(),
                )

    def test_json_response_preserves_missing_error_and_explicit_null(self):
        reports = Reports(stores=[StoreReport(slug="tris", name="TRIS", report=None)])
        request = SimpleNamespace(state=SimpleNamespace(user=object()))
        with (
            patch.object(web, "accessible_stores", return_value=("tris",)),
            patch.object(web, "load_reports", return_value=reports),
        ):
            response = asyncio.run(web.data(request))
        self.assertEqual(
            json.loads(response.body), {"stores": [{"slug": "tris", "name": "TRIS", "report": None}]}
        )

    def test_legacy_catalogue_is_adapted_once_without_filling_missing_costs(self):
        product = calculator_product(
            {
                "article": "123",
                "unrelated": True,
                "details": {"purchase_cost": "1 234,50", "fulfillment_cost": "", "unrelated": 1},
                "advertising": {"spend_per_order": 0},
            }
        )
        self.assertEqual(product.details.purchase_cost, 1234.5)
        self.assertIsNone(product.details.fulfillment_cost)
        self.assertEqual(product.advertising.spend_per_order, 0)
        self.assertIsNone(product.price.current)

    def test_shared_calculator_keeps_percentage_fallbacks_and_rounding(self):
        result = calculator.calculate(
            CalculatorValues(
                retail=1000,
                client=900,
                purchase=400,
                fulfillment=20,
                advertisingRub=30,
                acquiringPercent=1,
                commission=10,
                team=5,
                vatRub=40,
                secondaryTaxRub=10,
                logistics=20,
                storageTotal=5,
            ),
            tax_system="usn",
            turnover_days=None,
            acquiring_percent=None,
            purchase_cost=None,
        )
        self.assertEqual(result.margin, 315)
        self.assertEqual(result.roi, 78.75)

    def test_failed_fetch_does_not_publish_snapshot(self):
        with (
            patch.object(standard_sync.tokens, "get_token", return_value="test"),
            patch.object(standard_sync, "fetch_sources", side_effect=TimeoutError),
            patch.object(WbStandardCostsService, "publish") as save,
        ):
            with self.assertRaises(TimeoutError):
                standard_sync.sync_store("tris")
        save.assert_not_called()

    def test_job_boundary_preserves_failure_detection_and_skips(self):
        results = [
            StoreSyncResult("tris", SyncResult(ok=False, error="failed")),
            StoreSyncResult("rimili", SyncResult(ok=True, skipped=True, reason="Нет токена WB")),
        ]
        with patch.object(standard_sync, "sync_all", return_value=results):
            payload = standard_sync.sync_job_payload()
        self.assertEqual(payload["rimili"], {"ok": True, "skipped": True, "reason": "Нет токена WB"})
        self.assertIn("failed", result_error(payload))

    def test_sync_publishes_named_snapshot_after_complete_source_fetch(self):
        raw = SourceBundle.model_validate(fixture())
        with (
            patch.object(standard_sync.tokens, "get_token", return_value="test"),
            patch.object(standard_sync, "fetch_sources", return_value=raw),
            patch.object(WbStandardCostsService, "publish") as save,
        ):
            result = standard_sync.sync_store("tris")
        self.assertTrue(result.ok)
        self.assertEqual(result.rows, 1)
        self.assertIsInstance(save.call_args.args[0].snapshot, Snapshot)
        self.assertIs(save.call_args.args[0].raw, raw)
        self.assertEqual(save.call_args.args[0].store_slug, "tris")
