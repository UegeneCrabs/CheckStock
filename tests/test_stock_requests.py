"""Synthetic HTTP-handler and database integration checks; no app startup or external services."""

import asyncio
import importlib
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable
from test_ff_import import StockTestCase


class Actor(dict):
    __getattr__ = dict.__getitem__


class StockRequestTests(StockTestCase):
    def setUp(self):
        super().setUp()
        self.dto = importlib.import_module("app.dto.stock")
        self.actor = Actor(id=1, full_name="Synthetic operator")
        permission = patch.object(self.routes, "has_action_permission", return_value=True)
        permission.start()
        self.addCleanup(permission.stop)
        logger = patch.object(self.routes, "logger")
        logger.start()
        self.addCleanup(logger.stop)
        self.seed_stock(50)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO unit_economics_1c_source_values "
                "(stock_item_id, purchase_price, source_sheet_id, source_sheet_title, source_row, synced_at) "
                "VALUES (1, 12.5, 1, 'Synthetic', 1, 'test')"
            )
            conn.commit()

    def request(self, key, actor=None):
        return Request(
            {
                "type": "http",
                "headers": [(b"idempotency-key", key.encode())],
                "state": {"user": actor or self.actor},
            }
        )

    def call(
        self, action, key=None, *, actor=None, slug="rimili", quantity=3, transfer_id=None, content=None
    ):
        request = self.request(key if key is not None else uuid4().hex, actor)
        if action == "manual":
            future = self.routes.add_ff_items(
                request,
                slug,
                self.dto.AddFulfillmentItemsRequest(
                    fulfillment="Test FF",
                    marketplace=self.marketplace,
                    confirmed=True,
                    note="Synthetic movement",
                    items=[{"code": "A", "quantity": quantity}],
                ),
                self.stock,
            )
        elif action == "transfer":
            future = self.routes.transfer_ff_stock(
                request,
                slug,
                self.stock,
                from_fulfillment="Test FF",
                from_marketplace=self.marketplace,
                to_fulfillment="Destination",
                to_marketplace=self.marketplace,
                note="Synthetic movement",
                items=json.dumps([{"code": "A", "quantity": quantity}]),
                sheet_url="",
                file=UploadFile(io.BytesIO(content), filename="movement.xlsx") if content else None,
            )
        elif action in ("shipment", "trash", "surplus", "fbs"):
            future = self.routes.ship_ff_stock(
                request,
                slug,
                self.stock,
                fulfillment="Test FF",
                marketplace=self.marketplace,
                note="Synthetic movement",
                to_fbs="1" if action == "fbs" else "",
                to_trash="1" if action in ("trash", "surplus") else "",
                items=json.dumps([{"code": "A", "quantity": -quantity if action == "surplus" else quantity}]),
                sheet_url="",
                file=UploadFile(io.BytesIO(content), filename="movement.xlsx") if content else None,
            )
        else:
            payload = {"reason": "Synthetic reason"}
            if action == "receive":
                item = self.rows(f"SELECT id FROM ff_transit_items WHERE batch_id={int(transfer_id)}")[0]
                payload = {
                    "items": [{"item_id": item["id"], "quantity": quantity}],
                    "note": "Synthetic receipt",
                }
            function, model = {
                "receive": (self.routes.receive_in_transit_transfer, self.dto.ReceiveTransitRequest),
                "reopen": (self.routes.reopen_received_transfer, self.dto.ReopenTransitRequest),
                "cancel": (self.routes.cancel_in_transit_transfer, self.dto.CancelTransitRequest),
            }[action]
            future = function(request, slug, transfer_id, model(**payload), self.stock)
        response = asyncio.run(future)
        return response.status_code, json.loads(response.body)

    def snapshot(self):
        tables = (
            "ff_stock",
            "ff_stock_deliveries",
            "stock_operations",
            "stock_operation_items",
            "activity_log",
            "ff_transfers",
            "ff_transit_batches",
            "ff_transit_items",
            "ff_transit_receipts",
            "ff_transit_receipt_items",
            "trash_stock",
            "used_sources",
            "stock_mutation_requests",
        )
        return {table: self.rows(f"SELECT * FROM {table} ORDER BY 1") for table in tables}

    def assert_success(self, result):
        self.assertEqual(result[0], 200, result)
        self.assertTrue(result[1]["ok"])
        return result[1]

    def prepare_action(self, action):
        if action not in ("receive", "reopen", "cancel"):
            return {}
        transfer = self.assert_success(self.call("transfer", quantity=6))["transfer_id"]
        if action == "reopen":
            self.assert_success(self.call("receive", transfer_id=transfer, quantity=6))
        return {"transfer_id": transfer}

    def test_all_movement_paths_rollback_history_failure_then_retry_and_replay(self):
        # Compare every affected table, including receipts deleted by reopen and pending request claims.
        for action in (
            "manual",
            "transfer",
            "shipment",
            "fbs",
            "trash",
            "surplus",
            "receive",
            "reopen",
            "cancel",
        ):
            with self.subTest(action=action):
                kwargs = self.prepare_action(action)
                before = self.snapshot()
                key = uuid4().hex
                original = self.routes.db.log_action_for_operation

                def fail_after_log(*args, original=original, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("Synthetic failure after stock, operation items and activity write")

                with patch.object(self.routes.db, "log_action_for_operation", side_effect=fail_after_log):
                    self.assertEqual(self.call(action, key, **kwargs)[0], 500)
                self.assertEqual(self.snapshot(), before)
                first = self.call(action, key, **kwargs)
                self.assert_success(first)
                after = self.snapshot()
                self.assertEqual(self.call(action, key, **kwargs), first)
                self.assertEqual(self.snapshot(), after)
                self.assertEqual(len(after["stock_operations"]), len(before["stock_operations"]) + 1)

    def test_import_history_failure_is_atomic_and_same_key_can_retry(self):
        content = self.xlsx([["123", "A", 10]])
        _, preview = self.upload(content, preview=True)
        token = preview["preview"]["confirmation_token"]
        key = uuid4().hex
        before = self.snapshot()
        # A real DB error at each history stage rolls back the already applied delivery/stock.
        for table in ("stock_operations", "stock_operation_items", "activity_log"):
            with self.subTest(table=table):
                with self.database.connect() as conn:
                    conn.execute(
                        f"CREATE TRIGGER fail_history BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'injected'); END"
                    )
                    conn.commit()
                self.assertEqual(self.upload(content, token=token, key=key)[0], 500)
                self.assertEqual(self.snapshot(), before)
                with self.database.connect() as conn:
                    conn.execute("DROP TRIGGER fail_history")
                    conn.commit()
        first = self.upload(content, token=token, key=key)
        self.assert_success(first)
        after = self.snapshot()
        self.assertEqual(self.upload(content, token=token, key=key), first)
        self.assertEqual(self.snapshot(), after)
        self.assertEqual(self.quantity(), 60)
        self.assertEqual(after["stock_operation_items"][0]["purchase_price"], 12.5)
        self.assertEqual(after["stock_operations"][0]["user_id"], 1)

    def test_source_mark_failure_rolls_back_dispatch_batch_stock_and_audit(self):
        content = self.xlsx([["123", "A", 3]])
        for action in ("transfer", "shipment"):
            with self.subTest(action=action):
                key = uuid4().hex
                before = self.snapshot()
                original = self.routes.db.record_used_source

                def fail_after_source(*args, original=original, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("Synthetic failure after source mark")

                with patch.object(self.routes.db, "record_used_source", side_effect=fail_after_source):
                    self.assertEqual(self.call(action, key, content=content)[0], 500)
                self.assertEqual(self.snapshot(), before)
                result = self.call(action, key, content=content)
                self.assert_success(result)
                after = self.snapshot()
                self.assertEqual(self.call(action, key, content=content), result)
                self.assertEqual(self.snapshot(), after)
                # Existing business restriction for outbound sources is retained.
                self.assertEqual(self.call(action, content=content)[0], 400)
                self.assertEqual(self.snapshot(), after)

    def test_key_with_different_content_or_action_conflicts(self):
        key = uuid4().hex
        self.assert_success(self.call("manual", key))
        before = self.snapshot()
        self.assertEqual(self.call("manual", key, quantity=4)[0], 409)
        self.assertEqual(self.call("shipment", key)[0], 409)
        self.assertEqual(self.snapshot(), before)

    def test_file_change_with_same_key_conflicts_before_reimport(self):
        key = uuid4().hex
        first = self.xlsx([["123", "A", 10]])
        _, data = self.upload(first, preview=True)
        token = data["preview"]["confirmation_token"]
        self.assert_success(self.upload(first, token=token, key=key))
        self.assertEqual(self.upload(self.xlsx([["123", "A", 11]]), token=token, key=key)[0], 409)
        self.assertEqual(self.quantity(), 60)

    def test_concurrent_duplicate_imports_commit_once(self):
        key = uuid4().hex
        content = self.xlsx([["123", "A", 10]])
        _, data = self.upload(content, preview=True)
        token = data["preview"]["confirmation_token"]
        barrier = threading.Barrier(5)

        def upload(_):
            barrier.wait(timeout=10)
            return self.upload(content, token=token, key=key)

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(upload, range(5)))
        self.assert_success(results[0])
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(self.quantity(), 60)
        self.assertEqual(len(self.rows("SELECT * FROM stock_mutation_requests")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM stock_operations")), 1)

    def test_concurrent_conflicting_payloads_allow_one_winner(self):
        key = uuid4().hex
        barrier = threading.Barrier(2)

        def move(quantity):
            barrier.wait(timeout=10)
            return self.call("manual", key, quantity=quantity)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(move, (3, 4)))
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        winner = 3 if results[0][0] == 200 else 4
        self.assertEqual(self.quantity(), 50 + winner)

    def test_failure_before_and_after_commit(self):
        key = uuid4().hex
        before = self.snapshot()
        commit = self.stock_uow.commit
        with patch.object(self.stock_uow, "commit", side_effect=RuntimeError("before commit")):
            self.assertEqual(self.call("manual", key)[0], 500)
        self.assertEqual(self.snapshot(), before)

        def lost_response(uow):
            commit(uow)
            raise RuntimeError("after commit, before response delivery")

        with patch.object(self.stock_uow, "commit", lost_response):
            self.assertEqual(self.call("manual", key)[0], 500)
        after = self.snapshot()
        self.assertEqual(self.quantity(), 53)
        self.assert_success(self.call("manual", key))
        self.assertEqual(self.snapshot(), after)

    def test_concurrent_retry_takes_over_after_first_transaction_rolls_back(self):
        key = uuid4().hex
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        attempts = []
        original = self.routes.db.log_action_for_operation

        def fail_once(*args, **kwargs):
            original(*args, **kwargs)
            with lock:
                attempts.append(1)
                if len(attempts) == 1:
                    raise RuntimeError("First transaction fails; waiting retry must take over")

        def submit(_):
            barrier.wait(timeout=10)
            return self.call("manual", key)

        with patch.object(self.routes.db, "log_action_for_operation", side_effect=fail_once):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(result[0] for result in results), [200, 500])
        self.assertEqual(self.quantity(), 53)
        self.assertEqual(len(self.rows("SELECT * FROM stock_operations")), 1)
        self.assert_success(self.call("manual", key))
        self.assertEqual(self.quantity(), 53)

    def test_concurrent_new_import_keys_each_add_full_delivery(self):
        content = self.xlsx([["123", "A", 10]])
        _, data = self.upload(content, preview=True)
        token = data["preview"]["confirmation_token"]
        barrier = threading.Barrier(3)

        def submit(_):
            barrier.wait(timeout=10)
            return self.upload(content, token=token, key=uuid4().hex)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(submit, range(3)))
        for result in results:
            self.assert_success(result)
        self.assertEqual(self.quantity(), 80)
        self.assertEqual(len(self.rows("SELECT * FROM stock_operations")), 3)

    def test_concurrent_receipt_retries_share_single_receipt_and_audit(self):
        batch = self.assert_success(self.call("transfer", quantity=3))["transfer_id"]
        key = uuid4().hex
        barrier = threading.Barrier(3)

        def submit(_):
            barrier.wait(timeout=10)
            return self.call("receive", key, transfer_id=batch)

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(submit, range(3)))
        for result in results:
            self.assert_success(result)
            self.assertEqual(result, results[0])
        self.assertEqual(len(self.rows("SELECT * FROM ff_transit_receipts")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM stock_operations")), 2)
        self.assertEqual(self.quantity(), 50)

    def test_replay_does_not_refetch_mutable_google_source(self):
        key = uuid4().hex
        url = "https://docs.google.com/spreadsheets/d/synthetic/edit#gid=0"
        with (
            patch.object(
                self.importer,
                "fetch_google_sheet_rows",
                return_value=[["BARCODE", "ARTICLE", "КОЛИЧЕСТВО"], ["123", "A", 10]],
            ),
            patch.object(self.importer, "_fetch_public_sheet_title", return_value="Synthetic"),
        ):
            _, data = self.upload(preview=True, sheet_url=url)
            token = data["preview"]["confirmation_token"]
            result = self.upload(sheet_url=url, token=token, key=key)
        self.assert_success(result)
        with patch.object(
            self.importer, "fetch_google_sheet_rows", side_effect=AssertionError("must not refetch")
        ):
            self.assertEqual(self.upload(sheet_url=url, token=token, key=key), result)
        self.assertEqual(self.quantity(), 60)

    def test_invalid_key_and_unauthenticated_actor_do_not_write(self):
        before = self.snapshot()
        for key in ("", "short", "x" * 129, "!" * 32):
            self.assertEqual(self.call("manual", key)[0], 400)
        self.assertEqual(self.call("manual", actor=Actor(id=None, full_name="No actor"))[0], 400)
        self.assertEqual(self.snapshot(), before)

    def test_replay_rechecks_permissions_and_store_ownership(self):
        key = uuid4().hex
        self.assert_success(self.call("manual", key))
        before = self.snapshot()
        with patch.object(
            self.routes, "_guard_stock_action", return_value=JSONResponse({"ok": False}, status_code=403)
        ):
            self.assertEqual(self.call("manual", key)[0], 403)
        self.assertEqual(self.snapshot(), before)
        transfer = self.assert_success(self.call("transfer"))["transfer_id"]
        receipt_key = uuid4().hex
        self.assert_success(self.call("receive", receipt_key, transfer_id=transfer))
        with patch.object(self.routes, "has_action_permission", return_value=False):
            self.assertEqual(self.call("receive", receipt_key, transfer_id=transfer)[0], 403)
        self.assertEqual(self.call("receive", receipt_key, transfer_id=transfer, slug="other-store")[0], 404)

    def test_key_is_scoped_by_user_and_store(self):
        key = uuid4().hex
        self.assert_success(self.call("manual", key))
        self.assert_success(self.call("manual", key, actor=Actor(id=2, full_name="Other operator")))
        with patch.dict(self.routes.STORES, {"other": self.routes.STORES["rimili"]}):
            with self.database.connect() as conn:
                conn.execute(
                    "INSERT INTO stock_items (store_slug, marketplace, article, barcode, name) VALUES ('other', 'WB', 'A', '123', 'Other product')"
                )
                conn.commit()
            self.assert_success(self.call("manual", key, slug="other"))
        self.assertEqual(len(self.rows("SELECT * FROM stock_mutation_requests")), 3)
        self.assertEqual(self.quantity(), 59)

    def test_transit_receipt_retains_dispatch_cost_including_missing_cost(self):
        for price in (12.5, None):
            with self.subTest(price=price):
                with self.database.connect() as conn:
                    conn.execute("UPDATE unit_economics_1c_source_values SET purchase_price=?", (price,))
                    conn.commit()
                batch = self.assert_success(self.call("transfer"))["transfer_id"]
                with self.database.connect() as conn:
                    conn.execute("UPDATE unit_economics_1c_source_values SET purchase_price=99")
                    conn.commit()
                self.assert_success(self.call("receive", transfer_id=batch))
                history = self.rows(
                    f"SELECT i.purchase_price FROM stock_operation_items i JOIN stock_operations o ON o.id=i.operation_id WHERE o.transit_batch_id={batch}"
                )
                self.assertEqual(history, [{"purchase_price": price}, {"purchase_price": price}])

    def test_api_contract_requires_header_and_replays_json(self):
        # Minimal router app: deliberately does not import app.main or run its startup jobs/migrations.
        app = FastAPI()
        app.include_router(self.routes.router)
        dependencies = importlib.import_module("app.web.dependencies")
        app.dependency_overrides[dependencies.get_stock_movement_service] = lambda: self.stock

        @app.middleware("http")
        async def actor_middleware(request, call_next):
            request.state.user = self.actor
            return await call_next(request)

        body = {
            "fulfillment": "Test FF",
            "marketplace": "WB",
            "note": "Synthetic",
            "confirmed": True,
            "items": [{"code": "A", "quantity": 3}],
        }
        with TestClient(app) as client:
            self.assertEqual(client.post("/stock/rimili/add-ff-items", json=body).status_code, 400)
            headers = {"Idempotency-Key": uuid4().hex}
            first = client.post("/stock/rimili/add-ff-items", json=body, headers=headers)
            second = client.post("/stock/rimili/add-ff-items", json=body, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self.quantity(), 53)

    def test_new_table_ddl_compiles_for_postgresql_and_sqlite(self):
        table = self.orm.StockMutationRequestRecord.__table__
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            ddl = str(CreateTable(table).compile(dialect=dialect))
            self.assertIn("PRIMARY KEY (store_slug, user_id, request_key)", ddl)

    def test_legacy_sql_repositories_share_orm_connection_and_single_commit(self):
        commits = []
        event.listen(self.database.engine, "commit", lambda conn: commits.append(conn))
        self.assert_success(self.call("manual"))
        self.assertEqual(len(commits), 1)
