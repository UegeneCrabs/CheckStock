"""Local acceptance cases for the isolated finance ledger (never real credentials)."""

import copy
import json
import os
import tempfile
import threading
import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import dotenv

with (
    patch.object(dotenv, "dotenv_values", return_value={}),
    patch.dict(os.environ, {"CHECKSTOCK_DATABASE_URL": ""}),
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.access.sections import has_access
    from app.application.finance import FinanceService, months, period
    from app.dto.finance import ConnectionUpdate, CostUpdate, FinanceEvent, SourceBatch
    from app.dto.identity import Role, SectionAccessLevel, SectionName, User
    from app.finance.calculation import SOURCES, calculate
    from app.infrastructure.finance_repository import FinanceRepository
    from app.infrastructure.orm import OrmBase
    from app.jobs import finance as finance_job
    from app.jobs import locks, tracking
    from app.web.routers import finance as routes
    from app.yandex.finance_mapping import PARSERS, FinanceSourceError, event_day
    from app.yandex.finance_provider import FinanceProvider, FinanceSecrets

START, END = date(2026, 8, 1), date(2026, 8, 31)
FIXTURE = Path(__file__).parent / "fixtures" / "yandex_finance" / "month.json"


def sale(key="s", day=date(2026, 8, 4), quantity=1, seller="100", buyer="90", cost="40", **kwargs):
    return FinanceEvent(
        key=key,
        day=day,
        kind="sale",
        source="realization",
        campaign_id=101,
        order_id="9001",
        article="SKU-A",
        quantity=quantity,
        seller=seller,
        buyer=buyer,
        cost=cost,
        **kwargs,
    )


class FinanceCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        tables = [t for name, t in OrmBase.metadata.tables.items() if name.startswith("finance_yandex_")]
        OrmBase.metadata.create_all(self.engine, tables=tables)
        self.repo = FinanceRepository(sessionmaker(self.engine, expire_on_commit=False))
        self.service = FinanceService(self.repo)
        self.conn = self.connect()
        self.raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.clock_patch = patch("app.application.finance.today", return_value=date(2026, 10, 7))
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.engine.dispose)

    def connect(self, store="rimili", business=11, campaigns=(101,), start=START):
        return self.repo.add_connection(
            ConnectionUpdate(
                store_slug=store, business_id=business, campaign_ids=list(campaigns), effective_from=start
            ),
            "test",
        )

    def batches(self, events=()):
        return [
            SourceBatch(source=s, start=START, end=END, events=tuple(e for e in events if e.source == s))
            for s in SOURCES
        ]

    def publish(self, events=(), conn=None):
        return self.repo.publish(conn or self.conn, self.batches(events), "test")

    def report(self, stores=("rimili",), start=START, end=END):
        return self.service.report(stores, start, end)

    def parsed(self):
        return [PARSERS[s](self.conn, self.raw[s], START, END) for s in SOURCES]

    def cost(self, price="400", article="SKU-A", day=START, origin="manual"):
        return self.repo.add_cost(
            CostUpdate(
                store_slug="rimili",
                article=article,
                effective_from=day,
                price=price,
                reason="invoice fixture",
            ),
            "author",
            origin,
        )


class FinanceCalculations(FinanceCase):
    def test_F01_return_day_can_be_negative(self):
        sold = sale()
        returned = sold.model_copy(update={"key": "r", "kind": "return", "day": date(2026, 8, 5)})
        self.publish([sold, returned])
        data = self.report()
        self.assertEqual(data["metrics"]["seller_turnover"]["value"], "0.00")
        self.assertEqual(data["daily"][4]["metrics"]["buyout_count"]["value"], "-1.00")

    def test_F02_F03_F04_F05_partial_return_and_sku_join(self):
        self.cost()
        batches = self.service.price_batches(self.conn, self.parsed())
        self.repo.publish(self.conn, batches, "test")
        totals = self.report()["metrics"]
        for key, expected in {
            "buyout_count": "2.00",
            "seller_turnover": "2000.00",
            "buyer_turnover": "1800.00",
            "net_cost": "800.00",
            "profit": "970.00",
            "payments": "1600.00",
        }.items():
            self.assertEqual(totals[key]["value"], expected, key)
        self.raw["realization"]["operational"]["orders_and_offers_transactions"].reverse()
        replay = self.service.price_batches(self.conn, self.parsed())
        self.repo.publish(self.conn, replay, "test")
        self.assertEqual(self.report()["metrics"], totals)

    def test_F06_F07_signed_storno_and_percentage_not_money(self):
        batch = PARSERS["services"](self.conn, self.raw["services"], START, END)
        metrics = calculate(list(batch.events), {})
        self.assertEqual(metrics["acquiring"]["value"], "0.00")
        self.assertEqual(metrics["commission"]["value"], "150.00")
        self.assertEqual(metrics["expenses"]["value"], "230.00")

    def test_F08_F14_empty_confirmed_sales_keep_advertising(self):
        e = FinanceEvent(
            key="ad",
            day=START,
            source="services",
            kind="expense",
            amount="80",
            category="advertising",
            campaign_id=101,
        )
        self.publish([e])
        values = self.report()["metrics"]
        self.assertEqual(values["net_cost"]["value"], "0.00")
        self.assertEqual(values["profit"]["value"], "-80.00")
        self.assertIsNone(values["margin_percent"]["value"])

    def test_F09_missing_buyer_is_not_zero(self):
        self.publish([sale(), sale("s2", buyer=None)])
        value = self.report()["metrics"]["buyer_turnover"]
        self.assertIsNone(value["value"])
        self.assertEqual(value["known_value"], "90.00")

    def test_F10_catalog_is_not_required(self):
        self.publish([sale()])
        self.assertEqual(self.report()["metrics"]["sold_count"]["value"], "1.00")

    def test_F11_F12_preserve_historical_price_and_manual_layer(self):
        self.cost("40")
        initial = self.service.price_batches(self.conn, self.batches([sale(cost=None)]))
        self.repo.publish(self.conn, initial, "test")
        self.cost("80")
        updated = self.service.price_batches(self.conn, self.batches([sale(cost=None)]))
        self.assertEqual(updated[0].events[0].cost, Decimal("40"))
        recalc = self.service.price_batches(self.conn, self.batches([sale(cost=None)]), recalculate=True)
        self.assertEqual(recalc[0].events[0].cost, Decimal("80"))
        self.repo.observe_costs("rimili", {})
        self.assertEqual(len(self.repo.costs("rimili")), 2)

    def test_F13_unknown_cost_blocks_profit_and_explains_article(self):
        self.publish([sale(cost=None)])
        metrics = self.report()["metrics"]
        self.assertIsNone(metrics["profit"]["value"])
        self.assertIn("SKU-A", " ".join(metrics["net_cost"]["reasons"]))
        self.assertEqual(metrics["seller_turnover"]["value"], "100.00")

    def test_F15_moscow_boundaries_and_month_chunks(self):
        self.assertEqual(event_day("2026-08-31T22:30:00Z"), date(2026, 9, 1))
        self.assertEqual(
            list(months(date(2026, 8, 31), date(2026, 9, 1))),
            [(START, END), (date(2026, 9, 1), date(2026, 9, 30))],
        )
        for start, end in ((END, START), (START, date(2026, 11, 1)), (date(2024, 1, 1), END)):
            with self.assertRaises(ValueError):
                period(start, end)

    def test_F16_displayed_days_sum_to_period_with_decimal_costs(self):
        self.publish(
            [
                sale("1", day=START, seller="100.005", cost="33.3333"),
                sale("2", day=END, seller="100.005", cost="33.3333"),
            ]
        )
        data = self.report()
        for key in ("seller_turnover", "net_cost", "profit"):
            self.assertEqual(
                sum(Decimal(d["metrics"][key]["value"]) for d in data["daily"]),
                Decimal(data["metrics"][key]["value"]),
            )

    def test_F17_payment_not_profit_and_cross_month_duplicate(self):
        raw = self.raw["payments"]
        raw["transaction_date"].append(copy.deepcopy(raw["transaction_date"][0]))
        payments = PARSERS["payments"](self.conn, raw, START, END)
        self.assertEqual(len(payments.events), 1)
        later = PARSERS["payments"](self.conn, raw, date(2026, 9, 1), date(2026, 9, 30))
        self.assertFalse(later.events)
        self.publish([sale(), *payments.events])
        values = self.report()["metrics"]
        self.assertEqual(values["profit"]["value"], "60.00")
        self.assertEqual(values["payments"]["value"], "1600.00")

    def test_F18_unknown_service_visible_as_incomplete(self):
        unknown = PARSERS["services"](
            self.conn, {"new_fee": [{"partnerId": 101, "weirdAmount": 500}]}, START, END
        )
        self.repo.publish(self.conn, [unknown], "test")
        values = self.report()["metrics"]
        self.assertIsNone(values["expenses"]["value"])
        self.assertTrue(unknown.issues)
        with self.assertRaises(FinanceSourceError):
            PARSERS["services"](self.conn, {"placement": {"wrong": []}}, START, END)

    def test_unknown_quantity_is_not_one(self):
        del self.raw["realization"]["campaigns"]["101"]["delivered"][0]["deliveredCount"]
        with self.assertRaises(FinanceSourceError):
            self.parsed()

    def test_orders_use_line_total_and_creation_date(self):
        batch = PARSERS["orders"](self.conn, self.raw["orders"], START, END)
        self.assertEqual(batch.events[0].amount, Decimal("3000"))
        self.assertEqual(batch.events[0].day, date(2026, 8, 3))

    def test_unloaded_differs_from_confirmed_empty(self):
        self.assertIsNone(self.report()["metrics"]["sold_count"]["value"])
        self.publish()
        self.assertEqual(self.report()["metrics"]["sold_count"]["value"], "0.00")


class FinancePersistence(FinanceCase):
    def test_I06_manual_and_scheduled_share_lock(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(locks.core, "DB_PATH", Path(directory) / "test.sqlite"),
            patch.object(locks, "database_for_path", return_value=SimpleNamespace(dialect_name="sqlite")),
            patch.object(tracking, "_start_run", return_value=("run-id", 0)),
            patch.object(tracking, "_finish_run", side_effect=lambda name, rid, clock, callback: callback()),
        ):
            started, release, finished = threading.Event(), threading.Event(), threading.Event()

            def callback():
                started.set()
                release.wait(5)
                finished.set()

            tracking.queue_tracked(finance_job.JOB, callback)
            self.assertTrue(started.wait(2))
            try:
                with self.assertRaises(locks.SyncJobBusyError):
                    tracking.run_tracked(finance_job.JOB, "scheduled", lambda: None)
            finally:
                release.set()
                self.assertTrue(finished.wait(2))

    def test_I08_common_sales_keys_are_untouched(self):
        with self.engine.begin() as db:
            db.exec_driver_sql(
                "CREATE TABLE sales_order_lines (order_key TEXT, line_key TEXT, quantity INTEGER)"
            )
            db.exec_driver_sql("INSERT INTO sales_order_lines VALUES ('9001','1',3)")
        self.repo.publish(self.conn, self.parsed(), "test")
        with self.engine.connect() as db:
            self.assertEqual(db.exec_driver_sql("SELECT * FROM sales_order_lines").all(), [("9001", "1", 3)])

    def test_no_coverage_regression(self):
        self.publish([sale()])
        batch = SourceBatch(source="realization", start=START, end=date(2026, 8, 10))
        with self.assertRaises(ValueError):
            self.repo.publish(self.conn, [batch], "test")
        self.assertEqual(self.report()["metrics"]["seller_turnover"]["value"], "100.00")

    def test_business_fee_stored_once_across_connections(self):
        other = self.connect("tris", 11, (202,))
        raw = {"personal_manager": [{"businessId": 11, "servicePrice": 1000, "serviceDate": str(START)}]}
        for conn in (self.conn, other):
            self.repo.publish(conn, [PARSERS["services"](conn, raw, START, END)], "test")
        self.assertEqual(len(self.repo.unallocated()), 1)
        self.assertEqual(self.repo.unallocated()[0]["amount"], "1000")
        self.assertFalse(self.report()["_events"])

    def test_unknown_settlement_blocks_full_income(self):
        self.raw["payments"]["transaction_date"][0]["transactionSource"] = "unmapped correction"
        self.repo.publish(self.conn, self.parsed(), "test")
        report = self.report()
        self.assertIsNone(report["metrics"]["income"]["value"])
        self.assertIsNone(report["metrics"]["profit"]["value"])
        details = self.service.details(report, version=report["version"], metric="income")
        self.assertEqual(details["unknown_count"], 1)
        self.assertIn("unmapped correction", str(details["rows"][0]["issues"]))

    def test_positive_return_is_not_misreported_as_a_positive_payout(self):
        self.raw["payments"]["transaction_date"][0]["transactionType"] = "Возврат"
        self.repo.publish(self.conn, self.parsed(), "test")
        metric = self.report()["metrics"]["payments"]
        self.assertIsNone(metric["value"])
        self.assertEqual(metric["known_value"], "0.00")

    def test_offline_reconciliation_detects_mismatch_and_missing_reference(self):
        from scripts.diagnostics.reconcile_yandex_finance import reconcile

        self.assertTrue(reconcile(self.raw)["matched"])
        self.raw["expected"]["profit"] = "999.00"
        result = reconcile(self.raw)
        self.assertFalse(result["matched"])
        self.assertEqual(
            result["differences"], [{"metric": "profit", "expected": "999.00", "actual": "970.00"}]
        )
        self.raw["expected"] = {}
        with self.assertRaises(ValueError):
            reconcile(self.raw)

    def test_A01_A02_scoped_queries(self):
        other = self.connect("tris", 12)
        self.publish([sale()])
        self.publish([sale(seller="900")], other)
        self.assertEqual(self.report()["metrics"]["seller_turnover"]["value"], "100.00")
        self.assertFalse(self.repo.read((), START, END)["events"])
        self.assertEqual(len(self.report()["_events"]), 1)

    def test_I01_partial_source_never_published(self):
        self.publish([sale()])
        provider = Mock()

        def load(c, s, start, end):
            if s == "services":
                raise FinanceSourceError("fixture source failed")
            return self.batches([sale(seller="900")])[SOURCES.index(s)]

        provider.load.side_effect = load
        result = self.service.synchronize(("rimili",), START, END, provider, "test")
        self.assertFalse(result["ok"])
        report = self.report()
        self.assertEqual(report["metrics"]["seller_turnover"]["value"], "100.00")
        self.assertTrue(report["stale"])

    def test_I02_transaction_rollback_including_heads(self):
        self.publish([sale()])
        before = self.report()["version"]

        def reject(conn, cursor, statement, parameters, context, many):
            if statement.startswith("INSERT INTO finance_yandex_operations"):
                raise RuntimeError("insert failure")

        event.listen(self.engine, "before_cursor_execute", reject)
        try:
            with self.assertRaises(RuntimeError):
                self.publish([sale(seller="900")])
        finally:
            event.remove(self.engine, "before_cursor_execute", reject)
        self.assertEqual(self.report()["version"], before)
        self.assertEqual(self.report()["metrics"]["seller_turnover"]["value"], "100.00")

    def test_I03_I05_snapshot_replacement_and_overlap(self):
        for _ in range(3):
            self.publish([sale()])
        self.assertEqual(len(self.report()["_events"]), 1)
        self.publish([sale(seller="110")])
        self.assertEqual(
            self.report(start=date(2026, 8, 4), end=date(2026, 8, 4))["metrics"]["seller_turnover"]["value"],
            "110.00",
        )

    def test_I04_identical_service_rows_preserve_multiplicity(self):
        row = {"partnerId": 101, "totalAmount": 50, "serviceDateTime": str(START)}
        b = PARSERS["services"](self.conn, {"placement": [row, row]}, START, END)
        self.assertEqual(len(b.events), 2)
        self.assertEqual(calculate(list(b.events), {})["expenses"]["value"], "100.00")

    def test_I07_C02_one_business_fails_other_succeeds(self):
        self.connect("rimili", 12, (202,))
        provider = Mock()

        def load(c, s, start, end):
            if c["business_id"] == 12:
                raise FinanceSourceError("second account failed")
            return self.batches([sale()])[SOURCES.index(s)]

        provider.load.side_effect = load
        self.service.synchronize(("rimili",), START, END, provider, "test")
        metric = self.report()["metrics"]["seller_turnover"]
        self.assertIsNone(metric["value"])
        self.assertEqual(metric["known_value"], "100.00")

    def test_C03_foreign_campaign_is_filtered_and_repo_rejects_it(self):
        raw = {"placement": [{"partnerId": 202, "totalAmount": 900, "serviceDateTime": str(START)}]}
        b = PARSERS["services"](self.conn, raw, START, END)
        self.assertFalse(b.events)
        with self.assertRaises(ValueError):
            self.publish([sale().model_copy(update={"campaign_id": 202})])

    def test_C05_disable_retains_history_and_conflict_is_rejected(self):
        self.publish([sale()])
        self.repo.disable(self.conn["id"], END)
        self.assertEqual(self.report()["metrics"]["seller_turnover"]["value"], "100.00")
        with self.assertRaises(ValueError):
            self.connect("tris", 11, (101,))
        self.connect("tris", 11, (101,), date(2026, 9, 1))

    def test_disabled_connection_can_be_reloaded_manually_but_not_by_schedule(self):
        self.repo.disable(self.conn["id"], END)
        provider = Mock()
        provider.load.side_effect = lambda c, s, start, end: self.batches([sale()])[SOURCES.index(s)]
        result = self.service.synchronize(("rimili",), START, END, provider, "scheduler", scheduled=True)
        self.assertFalse(result["ok"])
        provider.load.assert_not_called()
        result = self.service.synchronize(("rimili",), START, END, provider, "admin")
        self.assertTrue(result["ok"])
        self.assertEqual(self.report()["metrics"]["seller_turnover"]["value"], "100.00")
        self.assertFalse(self.repo.connections(("rimili",))[0]["active"])

    def test_unallocated_operation_cannot_escape_snapshot_period(self):
        raw = {"personal_manager": [{"businessId": 11, "servicePrice": 1000, "serviceDate": "2026-09-01"}]}
        with self.assertRaises(ValueError):
            PARSERS["services"](self.conn, raw, START, END)

    def test_current_month_archive_may_include_unrequested_today(self):
        batch = PARSERS["realization"](self.conn, self.raw["realization"], START, date(2026, 8, 4))
        self.assertEqual([e.kind for e in batch.events], ["sale"])
        self.assertEqual(batch.end, date(2026, 8, 4))

    def test_previous_store_sale_cannot_establish_new_store_return_cost(self):
        self.cost()
        connection = {**self.conn, "effective_from": date(2026, 8, 5)}
        batches = self.service.price_batches(connection, self.parsed())
        returned = batches[0].events
        self.assertEqual([e.kind for e in returned], ["return"])
        self.assertIsNone(returned[0].cost)

    def test_C06_business_fee_does_not_multiply_or_leak(self):
        connection = {**self.conn, "campaign_ids": [101, 102, 103]}
        b = PARSERS["services"](
            connection,
            {"personal_manager": [{"businessId": 11, "servicePrice": 1000, "serviceDate": str(START)}]},
            START,
            END,
        )
        self.assertFalse(b.events)
        self.assertIn("нераспредел", b.issues[0])
        self.assertNotIn("1000", str(b.issues))

    def test_U03_version_conflict_and_details_reconcile(self):
        self.publish([sale()])
        data = self.report()
        details = self.service.details(data, version=data["version"], metric="profit")
        self.assertEqual(details["known_sum"], data["metrics"]["profit"]["value"])
        self.publish([sale(seller="110")])
        with self.assertRaises(LookupError):
            self.service.details(self.report(), version=data["version"], metric="profit")

    def test_recalculate_uses_saved_raw_without_provider_requests(self):
        self.cost()
        self.repo.publish(self.conn, self.service.price_batches(self.conn, self.parsed()), "test")
        self.cost("450")
        provider = FinanceProvider(self.repo, Mock())
        with patch.object(provider, "load", side_effect=AssertionError("network not allowed")):
            result = self.service.synchronize(("rimili",), START, END, provider, "test", mode="recalculate")
        self.assertTrue(result["ok"])
        self.assertEqual(self.report()["metrics"]["net_cost"]["value"], "900.00")

    def test_C01_C04_legacy_key_and_secret_rotation_preserve_campaigns(self):
        with tempfile.TemporaryDirectory() as directory:
            secrets = FinanceSecrets(Path(directory))
            with patch("app.yandex.tokens.get_api_key", return_value="old-key"):
                self.assertEqual(secrets.get(self.conn), "old-key")
                secrets.set(self.conn["id"], "new-key")
                self.assertEqual(secrets.get(self.conn), "new-key")
        self.assertEqual(self.repo.connections(("rimili",))[0]["campaign_ids"], [101])

    def test_current_source_observation_not_applied_to_old_sale(self):
        self.repo.observe_costs(
            "rimili", {"SKU-A": {"purchase_price": 75, "synced_at": "2026-08-10T12:00:00+03:00"}}
        )
        batches = self.service.price_batches(self.conn, self.batches([sale(cost=None)]))
        self.assertIsNone(batches[0].events[0].cost)


class FinanceHttp(FinanceCase):
    def setUp(self):
        super().setUp()
        self.user = User(
            id=1,
            full_name="Finance tester",
            login="tester",
            role="admin",
            created_at=datetime.now(UTC),
            store_slugs=("rimili",),
            section_access={SectionName.FINANCE_YANDEX: SectionAccessLevel.READ},
        )
        app = FastAPI()
        app.state.container = SimpleNamespace(finance=self.service)

        @app.middleware("http")
        async def auth(request, call_next):
            request.state.user = self.user
            return await call_next(request)

        app.include_router(routes.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        running = patch.object(routes.locks, "is_running", return_value=False)
        running.start()
        self.addCleanup(running.stop)

    def test_A01_scope_for_every_endpoint(self):
        self.publish([sale()])
        other = self.connect("tris", 12)
        self.publish([sale(seller="900")], other)
        for path in ("", "/filters", "/status", "/details"):
            self.assertEqual(self.client.get(routes.API + path + "?store=tris").status_code, 404)
        with patch("app.yandex.api.request", side_effect=AssertionError("read contacted provider")):
            response = self.client.get(routes.API + "?date_from=2026-08-01&date_to=2026-08-31")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["metrics"]["seller_turnover"]["value"], "100.00")
        self.assertNotIn("_events", response.json())

    def test_A03_A04_restricted_and_ungranted_users(self):
        for user in (
            self.user.model_copy(update={"role": Role.USER}),
            self.user.model_copy(update={"section_access": {}}),
        ):
            self.user = user
            for path in ("", "/filters", "/status", "/details"):
                self.assertEqual(self.client.get(routes.API + path).status_code, 403)

    def test_default_denied_and_administration_not_granted_by_read(self):
        self.assertFalse(
            has_access(self.user.model_copy(update={"section_access": {}}), SectionName.FINANCE_YANDEX)
        )
        self.assertEqual(self.client.get(routes.ADMIN + "/connections").status_code, 403)

    def test_U04_invalid_period_rejected(self):
        self.assertEqual(
            self.client.get(routes.API + "?date_from=2026-08-31&date_to=2026-08-01").status_code, 422
        )

    def test_superadmin_run_returns_id_before_work_finishes(self):
        self.user = self.user.model_copy(update={"role": Role.SUPERADMIN})
        with patch.object(routes, "queue_tracked", return_value="background-id") as queued:
            response = self.client.post(
                routes.ADMIN + "/runs",
                json={"store_slug": "rimili", "date_from": str(START), "date_to": str(END), "mode": "import"},
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["run_id"], "background-id")
        self.assertEqual(queued.call_args.args[0], finance_job.JOB)


class FinanceProviderTests(FinanceCase):
    def test_api_filters_are_identical_across_sources(self):
        provider = FinanceProvider(self.repo, Mock())
        archive_by_name = {
            "goods-realization": self.raw["realization"]["campaigns"]["101"],
            "united-orders": self.raw["realization"]["operational"],
            "united-marketplace-services": self.raw["services"],
            "united-netting": self.raw["payments"],
        }
        captured = []

        def report(conn, name, payload):
            captured.append((name, payload))
            return archive_by_name[name], name

        with (
            patch.object(provider, "report", side_effect=report),
            patch(
                "app.yandex.finance_provider.api.request",
                return_value={"orders": self.raw["orders"]["orders"]},
            ) as orders_api,
        ):
            for source in SOURCES:
                provider.load(self.conn, source, START, date(2026, 8, 30))
        self.assertEqual(orders_api.call_args.kwargs["payload"]["campaignIds"], [101])
        for name, payload in captured:
            self.assertEqual(
                payload["campaignId"] if name == "goods-realization" else payload["campaignIds"],
                101 if name == "goods-realization" else [101],
            )

    def test_failed_second_page_does_not_return_partial_orders(self):
        provider = FinanceProvider(self.repo, Mock())
        first = {"orders": self.raw["orders"]["orders"], "paging": {"nextPageToken": "next"}}
        with patch(
            "app.yandex.finance_provider.api.request", side_effect=[first, RuntimeError("network failed")]
        ):
            with self.assertRaises(RuntimeError):
                provider.business_orders(self.conn, START, date(2026, 8, 30))

    def test_resume_ticket_after_poll_timeout_without_new_generation(self):
        provider = FinanceProvider(self.repo, Mock())
        from app.yandex import finance_provider as module

        with (
            patch.object(module.locks, "acquire", return_value=Mock()),
            patch.object(module.limits, "remaining", return_value=0),
            patch.object(module.limits, "defer"),
            patch.object(module.time, "sleep"),
        ):
            with patch.object(
                module.api,
                "request",
                side_effect=[{"reportId": "persistent-id"}] + [{"status": "PROCESSING"}] * 121,
            ):
                with self.assertRaises(FinanceSourceError):
                    provider.report(
                        self.conn, "goods-realization", {"campaignId": 101, "year": 2026, "month": 8}
                    )
            with (
                patch.object(
                    module.api,
                    "request",
                    return_value={"status": "DONE", "file": "https://example.invalid/report.zip"},
                ) as request,
                patch.object(module, "download_archive", return_value={"delivered": [], "returned": []}),
            ):
                _, identifier = provider.report(
                    self.conn, "goods-realization", {"campaignId": 101, "year": 2026, "month": 8}
                )
                self.assertEqual(identifier, "persistent-id")
                self.assertEqual(request.call_count, 1)
                self.assertIn("/info/", request.call_args.args[0])

    def test_R01_opt_in_and_completed_days(self):
        from app.jobs import catalog

        with patch.object(
            catalog,
            "settings",
            SimpleNamespace(
                **{
                    **catalog.settings.model_dump(),
                    "background_sync_enabled": True,
                    "yandex_finance_enabled": False,
                }
            ),
        ):
            definition = next(d for d in catalog.job_definitions() if d.name == finance_job.JOB)
            self.assertFalse(definition.enabled)
        self.assertEqual(list(months(date(2026, 10, 1), date(2026, 10, 6)))[0][1], date(2026, 10, 6))

    def test_schema_compiles_on_sqlite_and_postgresql(self):
        from sqlalchemy.dialects import postgresql, sqlite
        from sqlalchemy.schema import CreateTable

        for dialect in (postgresql.dialect(), sqlite.dialect()):
            for name, table in OrmBase.metadata.tables.items():
                if name.startswith("finance_yandex_"):
                    self.assertIn("CREATE TABLE", str(CreateTable(table).compile(dialect=dialect)))


if __name__ == "__main__":
    unittest.main()
