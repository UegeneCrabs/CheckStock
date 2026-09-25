"""Run with: python -m unittest discover -s tests -v.

Imports use default settings without reading .env. Every repository connection
is redirected to a temporary SQLite database; Google responses are mocked.
"""

import asyncio
import hashlib
import importlib
import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi import Request, UploadFile
from openpyxl import Workbook
from sqlalchemy.exc import IntegrityError


class StockTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.importer = importlib.import_module("app.ff_import.importer")
            cls.core = importlib.import_module("app.repositories.core")
            cls.database_module = importlib.import_module("app.infrastructure.database")
            cls.orm = importlib.import_module("app.infrastructure.orm")
            cls.routes = importlib.import_module("app.web.routers.stock_mutations")
            cls.marketplace = importlib.import_module("app.dto.marketplace").Marketplace.WB
            cls.stock_service = importlib.import_module("app.application.stock").StockMovementService
            cls.stock_uow = importlib.import_module(
                "app.infrastructure.stock_repository"
            ).SqlAlchemyStockUnitOfWork

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="checkstock-import-test-")
        self.addCleanup(directory.cleanup)
        self.database = self.database_module.Database(Path(directory.name) / "test.sqlite3")
        self.addCleanup(self.database.dispose)
        self.orm.OrmBase.metadata.create_all(self.database.engine)
        self.stock = self.stock_service(
            lambda: self.stock_uow(self.database.session_factory), lambda: datetime.now(UTC)
        )
        redirect = patch.object(self.core, "database_for_path", return_value=self.database)
        redirect.start()
        self.addCleanup(redirect.stop)
        guard = patch.object(self.routes, "_guard_stock_action", return_value=None)
        guard.start()
        self.addCleanup(guard.stop)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO stock_items (store_slug, marketplace, article, barcode, name) "
                "VALUES ('rimili', 'WB', 'A', '123', 'Test product')"
            )
            conn.commit()

    @staticmethod
    def xlsx(rows):
        workbook = Workbook()
        workbook.active.append(["BARCODE", "ARTICLE", "КОЛИЧЕСТВО"])
        for row in rows:
            workbook.active.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        workbook.close()
        return stream.getvalue()

    def upload(self, content=None, *, preview=False, token="", fulfillment="Test FF", sheet_url="", key=None):
        request = Request(
            {
                "type": "http",
                "headers": [(b"idempotency-key", (key or uuid4().hex).encode())],
                "state": {"user": {"id": 1, "full_name": "Import test"}},
            }
        )
        file = UploadFile(io.BytesIO(content), filename="delivery.xlsx") if content is not None else None
        response = asyncio.run(
            self.routes.upload_ff_stock(
                request,
                "rimili",
                stock=self.stock,
                fulfillment=fulfillment,
                marketplace=self.marketplace,
                note="Test delivery",
                preview="1" if preview else "",
                confirmation_token=token,
                sheet_url=sheet_url,
                file=file,
            )
        )
        return response.status_code, json.loads(response.body)

    def confirmed_upload(self, content=None, **kwargs):
        status, data = self.upload(content, preview=True, **kwargs)
        self.assertEqual(status, 200, data)
        preview = data["preview"]
        status, data = self.upload(content, token=preview["confirmation_token"], **kwargs)
        self.assertEqual(status, 200, data)
        self.assertEqual(preview["added_quantity"], data["report"]["added_quantity"])
        return data["report"]

    def rows(self, statement):
        with self.database.connect() as conn:
            return [dict(row) for row in conn.execute(statement).fetchall()]

    def quantity(self):
        return self.rows("SELECT COALESCE(SUM(quantity), 0) AS quantity FROM ff_stock")[0]["quantity"]

    def seed_stock(self, quantity):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO ff_stock (store_slug, article, fulfillment, marketplace, quantity) "
                "VALUES ('rimili', 'A', 'Test FF', 'WB', ?)",
                (quantity,),
            )
            conn.commit()


class FulfillmentImportTests(StockTestCase):
    def test_different_deliveries_with_same_filename_add_full_quantities_to_stock_and_history(self):
        self.seed_stock(100)
        for quantity, expected_stock in [(10, 110), (5, 115), (10, 125)]:
            report = self.confirmed_upload(self.xlsx([["123", "A", quantity]]))
            self.assertEqual(report["added_quantity"], quantity)
            self.assertEqual(self.quantity(), expected_stock)
        self.assertEqual(
            self.rows("SELECT quantity FROM stock_operation_items ORDER BY id"),
            [{"quantity": 10}, {"quantity": 5}, {"quantity": 10}],
        )
        self.assertEqual(len(self.rows("SELECT id FROM ff_stock_deliveries")), 3)
        self.assertEqual(len(self.rows("SELECT id FROM stock_operations")), 3)
        self.assertEqual(len(self.rows("SELECT id FROM activity_log")), 3)

    def test_identical_file_can_be_imported_three_times(self):
        content = self.xlsx([["123", "A", 10]])
        for expected in [10, 20, 30]:
            self.confirmed_upload(content)
            self.assertEqual(self.quantity(), expected)
        self.assertEqual(len(self.rows("SELECT id FROM stock_operation_items")), 3)

    def test_preview_does_not_write_and_legacy_baseline_does_not_reduce_addition(self):
        self.seed_stock(100)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO ff_import_snapshots "
                "(store_slug, fulfillment, marketplace, source_type, source_key, article, quantity, updated_at) "
                "VALUES ('rimili', 'Test FF', 'WB', 'file', 'delivery.xlsx', 'A', 500, 'old')"
            )
            conn.commit()
        content = self.xlsx([["123", "A", 10]])
        for _ in range(2):
            status, data = self.upload(content, preview=True)
            self.assertEqual(status, 200)
            self.assertEqual(data["preview"]["added_quantity"], 10)
        self.assertEqual(self.quantity(), 100)
        self.assertEqual(self.rows("SELECT id FROM ff_stock_deliveries"), [])
        self.assertEqual(self.rows("SELECT id FROM stock_operations"), [])
        self.confirmed_upload(content)
        self.assertEqual(self.quantity(), 110)
        self.assertEqual(self.rows("SELECT quantity FROM ff_import_snapshots"), [{"quantity": 500}])

    def test_repeated_rows_sum_and_unmatched_negative_and_zero_rows_do_not_add(self):
        content = self.xlsx(
            [["123", "A", 2], ["123", "A", 3], ["999", "unknown", 7], ["123", "A", -4], ["123", "A", 0]]
        )
        for expected in [5, 10]:
            report = self.confirmed_upload(content)
            self.assertEqual(self.quantity(), expected)
            self.assertEqual(report["added_quantity"], 5)
            self.assertEqual(report["applied"], 1)
            self.assertEqual(report["source_quantity"], 12)
            self.assertEqual(report["unmatched_quantity"], 7)
            self.assertEqual(report["negative_skipped"], [{"article": "A", "quantity": -4}])
            self.assertEqual(report["items"][0]["quantity"], 5)

    def test_changed_file_or_destination_requires_new_confirmation(self):
        content = self.xlsx([["123", "A", 10]])
        _, data = self.upload(content, preview=True)
        token = data["preview"]["confirmation_token"]
        status, _ = self.upload(self.xlsx([["123", "A", 20]]), token=token)
        self.assertEqual(status, 409)
        status, _ = self.upload(content, token=token, fulfillment="Other FF")
        self.assertEqual(status, 409)
        status, _ = self.upload(content)
        self.assertEqual(status, 400)
        self.assertEqual(self.quantity(), 0)
        self.assertEqual(self.rows("SELECT id FROM ff_stock_deliveries"), [])
        self.assertEqual(self.rows("SELECT id FROM stock_operations"), [])

    def test_confirmation_from_old_delta_mode_is_rejected(self):
        payload = json.dumps(
            {
                "target": ["rimili", "Test FF", "WB", "file", "delivery.xlsx"],
                "resolved": [["A", 10]],
                "previous": [],
                "source_quantity": 10,
                "unmatched_quantity": 0,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        token = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        status, _ = self.upload(self.xlsx([["123", "A", 10]]), token=token)
        self.assertEqual(status, 409)
        self.assertEqual(self.quantity(), 0)

    def test_same_google_sheet_adds_full_quantity_each_time(self):
        url = "https://docs.google.com/spreadsheets/d/test-import-sheet/edit#gid=0"
        for quantity in [10, 5, 10]:
            with (
                patch.object(
                    self.importer,
                    "fetch_google_sheet_rows",
                    return_value=[["BARCODE", "ARTICLE", "КОЛИЧЕСТВО"], ["123", "A", quantity]],
                ),
                patch.object(self.importer, "_fetch_public_sheet_title", return_value="Delivery"),
            ):
                self.confirmed_upload(sheet_url=url)
        self.assertEqual(self.quantity(), 25)
        self.assertEqual(
            self.rows("SELECT quantity FROM stock_operation_items ORDER BY id"),
            [{"quantity": 10}, {"quantity": 5}, {"quantity": 10}],
        )

    def test_delivery_record_failure_rolls_back_stock_addition(self):
        self.seed_stock(100)
        with self.database.connect() as conn:
            conn.execute(
                "CREATE TRIGGER fail_delivery BEFORE INSERT ON ff_stock_deliveries "
                "BEGIN SELECT RAISE(ABORT, 'simulated delivery failure'); END"
            )
            conn.commit()
        content = self.xlsx([["123", "A", 10]])
        preview = self.importer.import_ff_stock_from_xlsx(
            "rimili", "Test FF", content, "delivery.xlsx", preview=True
        )
        with self.assertRaises(IntegrityError):
            self.importer.import_ff_stock_from_xlsx(
                "rimili",
                "Test FF",
                content,
                "delivery.xlsx",
                confirmation_token=preview["confirmation_token"],
            )
        self.assertEqual(self.quantity(), 100)
        self.assertEqual(self.rows("SELECT id FROM ff_stock_deliveries"), [])


if __name__ == "__main__":
    unittest.main()
