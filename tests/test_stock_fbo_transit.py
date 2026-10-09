"""Real HTTP/SQL regression coverage for FF transfers and shipments to FBO."""

import io
import json
import os
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import dotenv
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

with (
    patch.object(dotenv, "dotenv_values", return_value={}),
    patch.dict(os.environ, {"CHECKSTOCK_DATABASE_URL": ""}),
):
    from app import db
    from app.application.stock import StockMovementService
    from app.dto.identity import User
    from app.dto.stock import SignedStockEntries
    from app.exports import stock_sheet
    from app.infrastructure.database import Database
    from app.infrastructure.orm import OrmBase
    from app.infrastructure.stock_repository import SqlAlchemyStockUnitOfWork
    from app.repositories import core, schema, stock_total
    from app.stock import cost_report
    from app.web.dependencies import get_stock_movement_service
    from app.web.routers import stock_mutations, stock_operations


class StockFboTransitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="stock-fbo-transit-")
        self.addCleanup(self.directory.cleanup)
        self.database = Database(path=Path(self.directory.name) / "test.sqlite")
        self.addCleanup(self.database.dispose)
        OrmBase.metadata.create_all(self.database.engine)
        self.patch = patch.object(core, "database_for_path", return_value=self.database)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.now = datetime(2026, 10, 9, 12, tzinfo=UTC)
        self.user = User(id=1, login="test", full_name="Test", role="superadmin", created_at=self.now)
        self.service = StockMovementService(
            lambda: SqlAlchemyStockUnitOfWork(self.database.session_factory), lambda: self.now
        )
        application = FastAPI()

        @application.middleware("http")
        async def actor(request, call_next):
            request.state.user = self.user
            return await call_next(request)

        application.include_router(stock_mutations.router)
        application.dependency_overrides[get_stock_movement_service] = lambda: self.service
        self.client = TestClient(application)
        self.addCleanup(self.client.close)
        with self.database.connect() as conn:
            for store in ("rimili", "tris"):
                for marketplace in ("WB", "OZON", "YANDEX MARKET"):
                    conn.execute(
                        "INSERT INTO stock_items (store_slug,marketplace,article,name,barcode) "
                        "VALUES (?,?,'001','Test product','123456789')",
                        (store, marketplace),
                    )
                    conn.execute(
                        "INSERT INTO ff_stock (store_slug,marketplace,article,fulfillment,quantity,updated_at) "
                        "VALUES (?,?,'001','Source',100,?)",
                        (store, marketplace, self.now.isoformat()),
                    )
            conn.commit()

    def post(self, path, *, data=None, body=None, key=None, expected=200, files=None):
        response = self.client.post(
            path,
            data=data,
            json=body,
            files=files,
            headers={"Idempotency-Key": key or uuid4().hex},
        )
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    def shipment(self, quantity=30, marketplace="WB", **overrides):
        data = {
            "fulfillment": "Source",
            "marketplace": marketplace,
            "note": "FBO shipment",
            "to_fbo": "1",
            "items": json.dumps([{"code": "001", "quantity": quantity}]),
        }
        data.update(overrides.pop("data", {}))
        return self.post("/stock/rimili/shipment", data=data, **overrides)

    def transfer(self, quantity=30, marketplace="WB"):
        return self.post(
            "/stock/rimili/transfer",
            data={
                "from_fulfillment": "Source",
                "from_marketplace": "WB",
                "to_fulfillment": "Destination",
                "to_marketplace": marketplace,
                "note": "FF transfer",
                "items": json.dumps([{"code": "001", "quantity": quantity}]),
            },
        )

    def receive(self, batch_id, quantity, **kwargs):
        item_id = db.get_ff_transit_batch(batch_id)["items"][0]["id"]
        return self.post(
            f"/stock/rimili/transfers/{batch_id}/receive",
            body={"items": [{"item_id": item_id, "quantity": quantity}]},
            **kwargs,
        )

    def action(self, batch_id, action, **kwargs):
        return self.post(
            f"/stock/rimili/transfers/{batch_id}/{action}",
            body={"reason": "Correction"},
            **kwargs,
        )

    def set_purchase_price(self, price, article="001", store_slug="rimili"):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO unit_economics_1c_source_values "
                "(stock_item_id,purchase_price,source_sheet_id,source_sheet_title,source_row,synced_at) "
                "SELECT id,?,1,'test',1,? FROM stock_items "
                "WHERE store_slug=? AND marketplace='WB' AND article=? "
                "ON CONFLICT(stock_item_id) DO UPDATE SET purchase_price=excluded.purchase_price",
                (price, self.now.isoformat(), store_slug, article),
            )
            conn.commit()

    def rename_article(self, marketplace, article):
        with self.database.connect() as conn:
            for table in ("stock_items", "ff_stock"):
                conn.execute(
                    f"UPDATE {table} SET article=? WHERE store_slug='rimili' AND marketplace=?",
                    (article, marketplace),
                )
            conn.commit()

    def assert_stock(self, source, destination=0, transit=0, marketplace="WB"):
        self.assertEqual(db.get_ff_available_totals("rimili", "Source", marketplace).get("001", 0), source)
        self.assertEqual(
            db.get_ff_available_totals("rimili", "Destination", marketplace).get("001", 0), destination
        )
        self.assertEqual(
            db.get_ff_available_totals("rimili", None, marketplace).get("001", 0), source + destination
        )
        self.assertEqual(db.get_ff_transit_totals("rimili", marketplace).get("001", 0), transit)
        with self.database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM mp_stock").fetchone()[0], 0)

    def test_fbo_dispatch_partial_and_full_receipt_never_credit_ff(self):
        for marketplace in ("WB", "OZON", "YANDEX MARKET"):
            with self.subTest(marketplace=marketplace):
                batch_id = self.shipment(marketplace=marketplace)["transfer_id"]
                self.assert_stock(70, transit=30, marketplace=marketplace)
                self.assertEqual(db.get_ff_transit_batch(batch_id)["kind"], "fbo_shipment")
                self.assertEqual(db.get_ff_transit_totals("rimili", marketplace, "Source"), {"001": 30})
                self.assertEqual(self.receive(batch_id, 10)["status"], "partial")
                self.assert_stock(70, transit=20, marketplace=marketplace)
                self.assertEqual(self.receive(batch_id, 20)["status"], "received")
                self.assert_stock(70, marketplace=marketplace)
                self.assertEqual(db.get_ff_available_totals("tris", None, marketplace), {"001": 100})

    def test_ff_transfer_keeps_receipt_and_reopen_semantics(self):
        batch_id = self.transfer()["transfer_id"]
        self.assert_stock(70, transit=30)
        self.assertEqual(db.get_ff_transit_totals("rimili", "WB", "Destination"), {"001": 30})
        self.receive(batch_id, 10)
        self.assert_stock(70, destination=10, transit=20)
        self.action(batch_id, "reopen")
        self.assert_stock(70, transit=30)
        self.receive(batch_id, 30)
        self.assert_stock(70, destination=30)

    def test_fbo_reopen_only_restores_transit(self):
        batch_id = self.shipment()["transfer_id"]
        self.receive(batch_id, 10)
        self.action(batch_id, "reopen")
        self.assert_stock(70, transit=30)
        self.receive(batch_id, 30)
        self.action(batch_id, "reopen")
        self.assert_stock(70, transit=30)
        self.assertEqual(db.get_ff_transit_batch(batch_id)["receipts"], [])
        self.receive(batch_id, 30)
        self.assert_stock(70)

    def test_cancel_returns_only_unreceived_remainder(self):
        batch_id = self.shipment()["transfer_id"]
        self.receive(batch_id, 10)
        self.action(batch_id, "cancel")
        self.assert_stock(90)
        batch = db.get_ff_transit_batch(batch_id)
        self.assertEqual((batch["received_units"], batch["cancelled_units"]), (10, 20))
        self.action(batch_id, "cancel", expected=400)
        self.action(batch_id, "reopen", expected=400)
        self.receive(batch_id, 1, expected=400)
        self.assert_stock(90)

    def test_ff_and_fbo_batches_are_independent_and_sum_once(self):
        ff_id = self.transfer(20)["transfer_id"]
        fbo_id = self.shipment(30)["transfer_id"]
        self.assert_stock(50, transit=50)
        rows = stock_total.get_source_rows(("rimili",))[-1]
        self.assertEqual([row["quantity"] for row in rows if row["marketplace"] == "WB"], [50])
        self.receive(ff_id, 20)
        self.assert_stock(50, destination=20, transit=30)
        self.receive(fbo_id, 30)
        self.assert_stock(50, destination=20)

    def test_cross_marketplace_transfer_still_credits_destination(self):
        batch_id = self.transfer(marketplace="OZON")["transfer_id"]
        self.assert_stock(70)
        self.assert_stock(100, transit=30, marketplace="OZON")
        self.receive(batch_id, 30)
        self.assert_stock(100, destination=30, marketplace="OZON")

    def test_ordinary_shipment_does_not_create_transit(self):
        result = self.shipment(data={"to_fbo": ""})
        self.assertNotIn("transfer_id", result)
        self.assert_stock(70)
        self.assertEqual(db.get_ff_transit_batches("rimili"), [])

    def test_legacy_fbs_replay_does_not_repeat_stock_deduction(self):
        key = uuid4().hex
        legacy = {"to_fbo": "", "to_fbs": "1"}
        result = self.shipment(key=key, data=legacy)
        self.assertEqual(self.shipment(key=key, data=legacy), result)
        self.assert_stock(70)
        self.assertEqual(db.get_ff_transit_batches("rimili"), [])
        self.assertEqual([op["kind"] for op in db.get_store_operations("rimili")], ["fbs_transfer"])

    def test_fbo_barcode_price_is_frozen_for_receipts_reopen_and_cancel(self):
        self.set_purchase_price(999, store_slug="tris")
        for marketplace in ("OZON", "YANDEX MARKET"):
            with self.subTest(marketplace=marketplace):
                self.set_purchase_price(100)
                self.rename_article(marketplace, "MP-001")
                with patch.object(stock_mutations, "_now_iso", return_value=self.now.isoformat()):
                    batch_id = self.shipment(
                        marketplace=marketplace,
                        data={"items": json.dumps([{"code": "MP-001", "quantity": 30}])},
                    )["transfer_id"]
                    self.assertEqual(db.get_ff_transit_batch(batch_id)["items"][0]["purchase_price"], 100)
                    self.set_purchase_price(900)
                    self.receive(batch_id, 10)
                    self.action(batch_id, "reopen")
                    self.receive(batch_id, 10)
                    self.action(batch_id, "cancel")
                report = cost_report.build_report(
                    ("rimili",), date(2026, 10, 9), date(2026, 10, 9), (marketplace,)
                )
                operations = [op for op in report["operations"] if op["transit_batch_id"] == batch_id]
                self.assertEqual(len(operations), 5)
                for operation in operations:
                    self.assertEqual(operation["items"][0]["purchase_price"], 100)
                    self.assertTrue(operation["items"][0]["purchase_price_recorded"])
                shipped = report["summary"][0]["shipped"]
                self.assertEqual((shipped["units"], shipped["cost"], shipped["missing_units"]), (10, 1000, 0))

    def test_fbo_barcode_price_includes_wb_barcode_aliases(self):
        self.set_purchase_price(100)
        self.rename_article("OZON", "OZ-001")
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO catalog_barcodes (stock_item_id,barcode) "
                "SELECT id,barcode FROM stock_items WHERE store_slug='rimili' AND marketplace='WB'"
            )
            conn.execute(
                "UPDATE stock_items SET barcode='other' WHERE store_slug='rimili' AND marketplace='WB'"
            )
            conn.commit()
        result = self.shipment(
            marketplace="OZON", data={"items": json.dumps([{"code": "OZ-001", "quantity": 5}])}
        )
        self.assertEqual(db.get_ff_transit_batch(result["transfer_id"])["items"][0]["purchase_price"], 100)

    def test_fbo_article_price_takes_precedence_including_zero(self):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO stock_items (store_slug,marketplace,article,name,barcode) "
                "VALUES ('rimili','WB','OTHER','Other product','123456789')"
            )
            conn.commit()
        self.set_purchase_price(250, article="OTHER")
        for price in (100, 0):
            with self.subTest(price=price):
                self.set_purchase_price(price)
                result = self.shipment(5, marketplace="OZON")
                self.assertEqual(
                    db.get_ff_transit_batch(result["transfer_id"])["items"][0]["purchase_price"], price
                )
        self.rename_article("OZON", "OZ-001")
        result = self.shipment(
            marketplace="OZON", data={"items": json.dumps([{"code": "OZ-001", "quantity": 5}])}
        )
        self.assertIsNone(db.get_ff_transit_batch(result["transfer_id"])["items"][0]["purchase_price"])
        # The barcode is still ambiguous when only one of its products has a price row.
        with self.database.connect() as conn:
            conn.execute(
                "DELETE FROM unit_economics_1c_source_values WHERE stock_item_id IN "
                "(SELECT id FROM stock_items WHERE store_slug='rimili' AND article='OTHER')"
            )
            conn.commit()
        result = self.shipment(
            marketplace="OZON", data={"items": json.dumps([{"code": "OZ-001", "quantity": 5}])}
        )
        self.assertIsNone(db.get_ff_transit_batch(result["transfer_id"])["items"][0]["purchase_price"])

    def test_missing_fbo_price_is_not_replaced_when_price_arrives_later(self):
        self.rename_article("OZON", "OZ-001")
        result = self.shipment(
            marketplace="OZON", data={"items": json.dumps([{"code": "OZ-001", "quantity": 30}])}
        )
        self.set_purchase_price(100)
        self.receive(result["transfer_id"], 10)
        self.action(result["transfer_id"], "cancel")
        operations = db.get_store_operations("rimili")
        self.assertEqual(len(operations), 3)
        for operation in operations:
            item = db.get_operation_items(operation["id"])[0]
            self.assertIsNone(item["purchase_price"])
            self.assertEqual(item["purchase_price_recorded"], 1)

    def test_replay_and_conflict_do_not_repeat_dispatch_or_receipt(self):
        key = uuid4().hex
        result = self.shipment(key=key)
        self.assertEqual(self.shipment(key=key), result)
        self.shipment(31, key=key, expected=409)
        self.shipment(key=key, data={"to_fbo": ""}, expected=409)
        batch_id = result["transfer_id"]
        receipt_key = uuid4().hex
        receipt = self.receive(batch_id, 10, key=receipt_key)
        self.assertEqual(self.receive(batch_id, 10, key=receipt_key), receipt)
        self.assert_stock(70, transit=20)
        self.assertEqual(len(db.get_store_operations("rimili")), 2)

    def test_invalid_quantities_and_conflicting_modes_leave_stock_unchanged(self):
        self.shipment(101, expected=400)
        self.shipment(-1, expected=400)
        self.shipment(data={"to_trash": "1"}, expected=400)
        self.shipment(data={"to_fbs": "1"}, expected=400)
        self.assert_stock(100)
        batch_id = self.shipment()["transfer_id"]
        self.receive(batch_id, 31, expected=400)
        self.assert_stock(70, transit=30)
        self.assertEqual(db.get_ff_transit_batch(batch_id)["receipts"], [])

    def test_failure_in_audit_rolls_back_stock_batch_and_request_key(self):
        key = uuid4().hex
        with patch.object(db, "log_action_for_operation", side_effect=RuntimeError("injected")):
            with self.assertLogs(stock_mutations.logger, level="ERROR"):
                self.shipment(key=key, expected=500)
        self.assert_stock(100)
        self.assertEqual(db.get_ff_transit_batches("rimili"), [])
        self.assertEqual(db.get_store_operations("rimili"), [])
        self.shipment(key=key)
        self.assert_stock(70, transit=30)

    def test_history_and_cost_report_distinguish_fbo_from_ff_receipts(self):
        with patch.object(stock_mutations, "_now_iso", return_value=self.now.isoformat()):
            batch_id = self.shipment()["transfer_id"]
            self.receive(batch_id, 10)
            self.action(batch_id, "reopen")
            self.receive(batch_id, 10)
            self.action(batch_id, "cancel")
        operations = db.get_store_operations("rimili", stock_operations._history_kinds("shipment"))
        self.assertEqual(
            {op["kind"] for op in operations},
            {
                "fbo_dispatch",
                "fbo_receive",
                "fbo_receive_revert",
                "fbo_cancel",
            },
        )
        self.assertTrue(all(op["transit_batch_id"] == batch_id for op in operations))
        report = cost_report.build_report(("rimili",), date(2026, 10, 9), date(2026, 10, 9), ("WB",))
        self.assertEqual(report["summary"][0]["shipped"]["units"], 10)
        self.assertEqual(report["summary"][0]["shipped"]["missing_units"], 10)
        self.assertEqual(report["summary"][0]["moved_in"]["units"], 0)
        self.assertEqual(len(cost_report.operations_for_view(report, "shipments")), 5)

    def test_fbo_cancellation_nets_missing_prices_and_known_costs(self):
        with patch.object(stock_mutations, "_now_iso", return_value=self.now.isoformat()):
            cancelled_id = self.shipment(30)["transfer_id"]
            self.action(cancelled_id, "cancel")
            report = cost_report.build_report(("rimili",), date(2026, 10, 9), date(2026, 10, 9), ("WB",))
            shipped = report["summary"][0]["shipped"]
            self.assertEqual((shipped["units"], shipped["cost"], shipped["missing_units"]), (0, 0, 0))

            unknown_id = self.shipment(30)["transfer_id"]
            self.receive(unknown_id, 10)
            self.action(unknown_id, "cancel")
            self.set_purchase_price(100)
            priced_id = self.shipment(20)["transfer_id"]
            self.receive(priced_id, 5)
            self.action(priced_id, "cancel")
            self.set_purchase_price(0)
            zero_id = self.shipment(12)["transfer_id"]
            self.receive(zero_id, 2)
            self.action(zero_id, "cancel")

        report = cost_report.build_report(("rimili",), date(2026, 10, 9), date(2026, 10, 9), ("WB",))
        shipped = report["summary"][0]["shipped"]
        self.assertEqual((shipped["units"], shipped["cost"], shipped["missing_units"]), (17, 500, 10))

    def test_fbo_missing_price_cancellation_respects_report_period(self):
        with patch.object(stock_mutations, "_now_iso", return_value="2026-10-08T12:00:00+00:00"):
            batch_id = self.shipment(30)["transfer_id"]
            self.receive(batch_id, 10)
        with patch.object(stock_mutations, "_now_iso", return_value=self.now.isoformat()):
            self.action(batch_id, "cancel")
        for first_day, last_day, expected in ((8, 8, 30), (9, 9, -20), (8, 9, 10)):
            with self.subTest(first_day=first_day, last_day=last_day):
                report = cost_report.build_report(
                    ("rimili",), date(2026, 10, first_day), date(2026, 10, last_day), ("WB",)
                )
                shipped = report["summary"][0]["shipped"]
                self.assertEqual((shipped["units"], shipped["missing_units"]), (expected, expected))

    def test_active_and_closed_lists_and_access_checks(self):
        batch_id = self.shipment()["transfer_id"]
        batches = self.client.get("/stock/rimili/transfers/in-transit?mp=WB").json()["batches"]
        self.assertEqual(batches[0]["kind"], "fbo_shipment")
        self.assertTrue(batches[0]["can_receive"])
        self.post(
            f"/stock/tris/transfers/{batch_id}/receive",
            body={"items": [{"item_id": 1, "quantity": 1}]},
            expected=404,
        )
        with patch.object(stock_mutations, "has_action_permission", return_value=False):
            self.receive(batch_id, 30, expected=403)
        self.assert_stock(70, transit=30)
        self.receive(batch_id, 30)
        self.assertEqual(self.client.get("/stock/rimili/transfers/in-transit?mp=WB").json()["batches"], [])
        closed = self.client.get("/stock/rimili/transfers/in-transit?mp=WB&view=history").json()["batches"]
        self.assertTrue(closed[0]["can_reopen"])
        self.assertFalse(closed[0]["can_receive"])

    def test_file_and_sheet_dispatch_use_the_same_fbo_flow(self):
        workbook = Workbook()
        workbook.active.append(["ARTICLE", "КОЛИЧЕСТВО"])
        workbook.active.append(["001", 12])
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()
        self.shipment(data={"items": ""}, files={"file": ("shipment.xlsx", content.getvalue())})
        entries = SignedStockEntries.model_validate([{"code": "001", "quantity": 8}])
        with patch.object(stock_mutations.ff_shipment, "entries_from_sheet", return_value=entries):
            self.shipment(data={"items": "", "sheet_url": "https://docs.google.com/spreadsheets/d/test"})
        self.assert_stock(80, transit=20)
        self.assertEqual(len(db.get_ff_transit_batches("rimili")), 2)

    def test_existing_transit_schema_migrates_without_changing_rows(self):
        legacy = Database(path=Path(self.directory.name) / "legacy.sqlite")
        self.addCleanup(legacy.dispose)
        with legacy.connect() as conn:
            conn.execute("CREATE TABLE ff_transit_batches (id INTEGER PRIMARY KEY, status VARCHAR)")
            conn.execute("INSERT INTO ff_transit_batches VALUES (7,'partial')")
            conn.commit()
        schema._migrate_stock_transit_kind(legacy)
        schema._migrate_stock_transit_kind(legacy)
        with legacy.connect() as conn:
            row = dict(conn.execute("SELECT * FROM ff_transit_batches").fetchone())
        self.assertEqual(row, {"id": 7, "status": "partial", "kind": "ff_transfer"})

    def test_google_export_keeps_manual_fbo_out_of_ff_transit_to_avoid_duplicate_inbound(self):
        self.transfer(20)
        self.shipment(30)
        self.assertEqual(db.get_ff_transit_totals("rimili", "WB"), {"001": 50})
        values = stock_sheet._metric_values(
            "rimili",
            "WB",
            [{"article": "001", "barcode": "123456789"}],
            ("ff_stock", "ff_transit"),
        )
        self.assertEqual(values["ff_stock"], {"001": 50})
        self.assertEqual(values["ff_transit"], {"001": 20})

    def test_receipt_and_reopen_do_not_change_preexisting_marketplace_stock(self):
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO mp_stock (store_slug,article,marketplace,scheme,quantity) "
                "VALUES ('rimili','001','WB','fbo',45)"
            )
            conn.commit()
        batch_id = self.shipment()["transfer_id"]
        self.receive(batch_id, 30)
        self.action(batch_id, "reopen")
        self.receive(batch_id, 30)
        with self.database.connect() as conn:
            self.assertEqual(conn.execute("SELECT quantity FROM mp_stock").fetchone()[0], 45)
        self.assertEqual(db.get_ff_available_totals("rimili", None, "WB"), {"001": 70})

    def test_receipt_audit_failure_is_atomic_and_retry_can_succeed(self):
        batch_id = self.shipment()["transfer_id"]
        key = uuid4().hex
        with patch.object(db, "record_operation", side_effect=RuntimeError("injected receipt failure")):
            with self.assertLogs(stock_mutations.logger, level="ERROR"):
                self.receive(batch_id, 10, key=key, expected=500)
        self.assert_stock(70, transit=30)
        self.assertEqual(db.get_ff_transit_batch(batch_id)["receipts"], [])
        self.receive(batch_id, 10, key=key)
        self.assert_stock(70, transit=20)


if __name__ == "__main__":
    unittest.main()
