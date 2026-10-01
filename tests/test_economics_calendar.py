"""Isolated regression tests. No app lifespan, .env, credentials, jobs or real DB.

Run: python -m unittest discover -s tests -p test_economics_calendar.py -v
"""

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import dotenv

# Imports below must not pick up the developer's .env, even in the main checkout.
dotenv.dotenv_values = lambda *_args, **_kwargs: {}
os.environ["CHECKSTOCK_DATABASE_URL"] = ""

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.dialects import postgresql, sqlite  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.schema import CreateTable  # noqa: E402

from app.access import economics_preview  # noqa: E402
from app.core.domain import MOSCOW_TIMEZONE  # noqa: E402
from app.dto.identity import User  # noqa: E402
from app.economics.daily_calculation import calculate  # noqa: E402
from app.infrastructure import daily_economics_orm, yandex_economics_orm  # noqa: E402,F401
from app.infrastructure.database import Database  # noqa: E402
from app.infrastructure.orm import OrmBase  # noqa: E402
from app.repositories import daily_economics as repo  # noqa: E402
from app.repositories import unit_economics_1c, yandex_economics  # noqa: E402
from app.web.routers import economics_calendar as api  # noqa: E402


def inputs(market="WB"):
    common = dict(
        purchase_price=250,
        fulfillment_cost=20,
        buyout_percent=90,
        vat_percent=5,
        usn_percent=6,
        acquiring_percent=1.6,
        orders_count=10,
        advertising_spend=150,
    )
    if market == "WB":
        return {
            **common,
            "retail_price": 1000,
            "customer_price": 900,
            "subject_commission_percent": 15,
            "wb_extra_tariff_percent": 0,
            "delivery_wb_rub": 50,
            "return_cost_rub": 25,
            "volume_l": 2,
            "acceptance_coefficient": 0,
            "storage_wb_rub": 0,
            "turnover_days": 21,
            "team_commission_percent": 0,
            "tax_system": "usn",
            "osno_percent": 0,
        }
    return {
        **common,
        "seller_price": 1000,
        "buyer_price": 900,
        "commission_percent": 15,
        "payment_acceptance": 10,
        "delivery_cost": 60,
        "middle_mile": 40,
        "transit_cost": 0,
        "length": 10,
        "width": 10,
        "height": 20,
        "company_commission_percent": 0,
        "loss_percent": 1,
        "disposal_cost": 5,
    }


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="checkstock-calendar-")
        self.database = Database(path=Path(self.directory.name) / "isolated.sqlite")
        OrmBase.metadata.create_all(self.database.engine)
        self.patches = [
            patch.object(module, "get_connection", self.database.connect)
            for module in (repo, unit_economics_1c, yandex_economics)
        ]
        self.patches.append(patch.object(
            economics_preview, "settings",
            economics_preview.settings.model_copy(update={"economics_preview_user_id": 1}),
        ))
        for item in self.patches:
            item.start()
        self.today = datetime.now(MOSCOW_TIMEZONE).date()
        self.day = (self.today - timedelta(days=1)).isoformat()
        self.key = ("WB", "rimili", "sku-1", self.day)
        self.user = self.make_user()
        application = FastAPI()

        @application.middleware("http")
        async def identify(request, call_next):
            request.state.user = self.user
            return await call_next(request)

        application.include_router(api.router)
        self.client = TestClient(application)
        self.catalog = [
            {
                "article": "sku-1",
                "name": "Тестовый товар",
                "barcode": "123",
                "barcodes": ["123"],
                "image_url": "",
            }
        ]
        self.patches += [
            patch.object(api.db, "get_catalog_items", return_value=self.catalog),
            patch.object(
                api.db,
                "get_unit_economics_1c_product_reference_rows",
                return_value=[{"article": "sku-1", "manager": "Менеджер Один"}],
            ),
            patch.object(
                api,
                "yandex_catalog",
                side_effect=lambda _stores, user: (
                    self.catalog if user.full_name == "Менеджер Один" or user.role == "superadmin" else []
                ),
            ),
        ]
        for item in self.patches[-3:]:
            item.start()

    def tearDown(self):
        self.client.close()
        for item in reversed(self.patches):
            item.stop()
        self.database.dispose()
        self.directory.cleanup()

    def make_user(self, **changes):
        return User(
            id=1,
            login="calendar",
            full_name=changes.pop("full_name", "Менеджер Один"),
            created_at=datetime.now(UTC),
            role=changes.pop("role", "superadmin"),
            **changes,
        )

    def capture(self, key=None, values=None):
        key = key or self.key
        repo.capture(
            key,
            repo.observation(
                inputs(key[0]) if values is None else values,
                raw={"received_response": {"zero": 0, "missing": None}},
                version=3 if key[0] == "WB" else 15,
                captured_at="2026-09-24T12:00:00+00:00",
            ),
        )
        return repo.get(key)

    def correction(self, key, field, value, *, undo=False, token=None):
        data = repo.get(key)
        preview, _ = repo.proposal(key, data, field, value, undo)
        return repo.correct(
            key,
            token=token or data["token"],
            preview_token=preview["preview_token"],
            field=field,
            value=value,
            undo=undo,
            reason="Документ поставщика",
            actor="1: Менеджер Один",
        )

    def test_both_marketplaces_roundtrip_correction_undo_and_neighbour(self):
        for market in ("WB", "YANDEX MARKET"):
            with self.subTest(market=market):
                key = (market, *self.key[1:])
                original = self.capture(key)
                neighbour = (market, "rimili", "sku-1", self.today.isoformat())
                self.capture(neighbour)
                next_before = repo.get(neighbour)
                updated = self.correction(key, "purchase_price", 350)
                self.assertEqual(updated["original"], original["original"])
                self.assertEqual(updated["values"]["purchase_price"], 350)
                self.assertAlmostEqual(
                    updated["result"]["margin"],
                    original["result"]["margin"] - (101 if market == "YANDEX MARKET" else 100),
                    places=2,
                )
                self.assertEqual(repo.get(neighbour), next_before)
                undone = self.correction(key, "purchase_price", None, undo=True)
                self.assertEqual(undone["values"]["purchase_price"], 250)
                self.assertEqual(undone["result"], original["result"])
                events = repo.get(key, events=True)["events"]
                self.assertEqual([e["kind"] for e in events], ["source", "correction", "undo"])
                self.assertEqual(events[1]["actor"], "1: Менеджер Один")

    def test_source_refresh_preserves_overlay_original_and_invalidates_preview(self):
        original = self.capture()
        self.correction(self.key, "purchase_price", 300)
        before = repo.get(self.key)
        preview, _ = repo.proposal(self.key, before, "fulfillment_cost", 30)
        self.capture(values={**inputs(), "purchase_price": 280})
        after = repo.get(self.key)
        self.assertEqual(after["values"]["purchase_price"], 300)
        self.assertEqual(after["source"]["values"]["purchase_price"], 280)
        self.assertEqual(after["original"], original["original"])
        with self.assertRaises(repo.Conflict):
            repo.correct(
                self.key,
                token=before["token"],
                preview_token=preview["preview_token"],
                field="fulfillment_cost",
                value=30,
                reason="Уточнение",
                actor="2",
            )
        undone = self.correction(self.key, "purchase_price", None, undo=True)
        self.assertEqual(undone["values"]["purchase_price"], 280)

    def test_missing_and_explicit_zero_remain_distinct(self):
        for market in ("WB", "YANDEX MARKET"):
            key = (market, *self.key[1:])
            self.capture(key, {**inputs(market), "fulfillment_cost": None})
            missing = repo.get(key)
            self.assertIsNone(missing["values"]["fulfillment_cost"])
            self.assertIn("fulfillment_cost", missing["result"]["missing"])
            corrected = self.correction(key, "fulfillment_cost", 0)
            self.assertEqual(corrected["values"]["fulfillment_cost"], 0)
            self.assertNotIn("fulfillment_cost", corrected["result"]["missing"])
            self.assertIsNotNone(corrected["result"]["margin"])

    def test_zero_purchase_roi_undefined_and_no_basis_missing(self):
        _, zero = calculate("WB", {**inputs(), "purchase_price": 0})
        self.assertIsNone(zero["roi"])
        _, absent = calculate("WB", {**inputs(), "retail_price": None, "customer_price": None})
        self.assertIsNone(absent["margin"])

    def test_computed_fields_invalid_values_and_reason_rejected(self):
        data = self.capture()
        for field, value in (
            ("margin", 99),
            ("roi", 50),
            ("paid_acceptance_cost", 2),
            ("orders_count", 1.5),
            ("buyout_percent", 101),
            ("purchase_price", -1),
            ("purchase_price", float("inf")),
            ("purchase_price", None),
            ("purchase_price", True),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                repo.proposal(self.key, data, field, value)
        preview, _ = repo.proposal(self.key, data, "purchase_price", 300)
        with self.assertRaises(ValueError):
            repo.correct(
                self.key,
                token=data["token"],
                preview_token=preview["preview_token"],
                field="purchase_price",
                value=300,
                reason=" ",
                actor="1",
            )

    def test_parallel_changes_only_one_succeeds(self):
        data = self.capture()
        preview, _ = repo.proposal(self.key, data, "purchase_price", 300)

        def save(_):
            try:
                repo.correct(
                    self.key,
                    token=data["token"],
                    preview_token=preview["preview_token"],
                    field="purchase_price",
                    value=300,
                    reason="Проверка гонки",
                    actor="1",
                )
                return "ok"
            except repo.Conflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(save, range(2))), ["conflict", "ok"])

    def test_journal_failure_rolls_back_values_and_calculation(self):
        before = self.capture()
        with self.database.connect() as conn:
            conn.execute(
                "CREATE TRIGGER refuse_event BEFORE INSERT ON economics_daily_events WHEN NEW.actor='fail' BEGIN SELECT RAISE(ABORT,'audit failure'); END"
            )
            conn.commit()
        preview, _ = repo.proposal(self.key, before, "purchase_price", 300)
        with self.assertRaises(IntegrityError):
            repo.correct(
                self.key,
                token=before["token"],
                preview_token=preview["preview_token"],
                field="purchase_price",
                value=300,
                reason="Документ",
                actor="fail",
            )
        self.assertEqual(repo.get(self.key), before)

    def test_read_never_creates_snapshot_and_pagination_is_unbounded(self):
        with patch.object(repo, "capture", side_effect=AssertionError("GET wrote snapshot")):
            response = self.client.get(
                "/api/unit-economics-1c/calendar", params={"store": "rimili", "day": self.day}
            )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["rows"][0]["snapshot"])
        self.assertIsNone(repo.get(self.key))
        with patch.object(
            api, "products", return_value=[{"article": f"sku-{i}", "name": str(i)} for i in range(723)]
        ):
            response = self.client.get(
                "/api/unit-economics-1c/calendar",
                params={"store": "rimili", "day": self.day, "page": 8, "page_size": 100},
            )
            self.assertEqual(response.json()["total"], 723)
            self.assertEqual(len(response.json()["rows"]), 23)

    def test_yandex_first_day_uses_common_sources_and_permitted_articles(self):
        for article, source in (
            ("sku-1", "day-input:2026-09-20:COMMON"),
            ("sku-1", "day-input:2026-09-01:FBY"),
            ("other-manager", "day-input:2026-08-01:COMMON"),
        ):
            yandex_economics.save_source("rimili", article, source, {})
        self.assertEqual(repo.first_day("YANDEX MARKET", "rimili", {"sku-1"}), "2026-09-20")
        self.assertIsNone(repo.first_day("YANDEX MARKET", "rimili", {"absent"}))

    def test_http_scope_manager_readonly_and_future_checks(self):
        self.capture()
        self.user = self.make_user(
            role="user",
            store_slugs=("rimili",),
            section_access={"unit_economics_wb": "read", "unit_economics_yandex": "none"},
        )
        base = "/api/unit-economics-1c/calendar"
        query = {"store": "rimili", "day": self.day, "article": "sku-1"}
        self.assertEqual(self.client.get(base + "/cell", params=query).status_code, 200)
        self.assertEqual(self.client.get(base, params={**query, "store": "tris"}).status_code, 403)
        self.assertEqual(
            self.client.get(base.replace("/calendar", "/yandex-market/calendar"), params=query).status_code,
            403,
        )
        body = {**query, "field": "purchase_price", "value": 300, "reason": "Документ", "token": "1"}
        self.assertEqual(self.client.post(base + "/preview", json=body).status_code, 403)
        self.assertEqual(self.client.post(base + "/correction", json=body).status_code, 403)
        self.user = self.make_user(role="user", full_name="Чужой менеджер", store_slugs=("rimili",))
        self.assertEqual(self.client.get(base + "/cell", params=query).status_code, 404)
        self.assertEqual(self.client.post(base + "/correction", json=body).status_code, 404)
        self.user = self.make_user()
        self.assertEqual(
            self.client.get(
                base, params={**query, "day": (self.today + timedelta(days=1)).isoformat()}
            ).status_code,
            422,
        )

    def test_http_preview_required_and_applies_both_markets(self):
        for market, suffix in (("WB", ""), ("YANDEX MARKET", "/yandex-market")):
            key = (market, *self.key[1:])
            self.capture(key)
            base = "/api/unit-economics-1c" + suffix + "/calendar"
            body = {
                "store": "rimili",
                "day": self.day,
                "article": "sku-1",
                "field": "purchase_price",
                "value": 300,
                "reason": "Уточнение",
                "token": "1",
            }
            self.assertEqual(self.client.post(base + "/correction", json=body).status_code, 409)
            preview = self.client.post(base + "/preview", json=body)
            self.assertEqual(preview.status_code, 200)
            result = self.client.post(
                base + "/correction", json={**body, "preview_token": preview.json()["preview_token"]}
            )
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["values"]["purchase_price"], 300)

    def test_report_readers_use_effective_day_only(self):
        for market in ("WB", "YANDEX MARKET"):
            key = (market, *self.key[1:])
            self.capture(key)
            changed = self.correction(key, "purchase_price", 400)
            if market == "WB":
                rows = unit_economics_1c.get_daily_margin_snapshots(("rimili",), self.day, self.day)
                self.assertEqual(rows[0]["unit_margin"], changed["result"]["margin"])
                self.assertEqual(json.loads(rows[0]["inputs_json"])["purchase_price"], 400)
            else:
                rows = yandex_economics.history("rimili", self.day, self.day)
                self.assertEqual(rows[0]["data"]["profit"], changed["result"]["day_profit"])
                self.assertEqual(rows[0]["data"]["inputs"]["purchase_price"], 400)

    def test_schema_compiles_for_postgres_and_sqlite(self):
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            for name in ("economics_daily", "economics_daily_events"):
                self.assertIn(
                    "CREATE TABLE", str(CreateTable(OrmBase.metadata.tables[name]).compile(dialect=dialect))
                )

    def test_legacy_read_then_correction_preserves_original_without_backfill(self):
        for market in ("WB", "YANDEX MARKET"):
            with self.subTest(market=market):
                key = (market, *self.key[1:])
                values = inputs(market)
                with self.database.connect() as conn:
                    if market == "WB":
                        conn.execute(
                            "INSERT INTO unit_economics_1c_daily_margin_snapshots "
                            "(store_slug,article,day,unit_margin,inputs_json,result_json,captured_at,calculation_version) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (*key[1:], 999, repo.encode(values), "{}", "2026-09-24T09:00:00Z", 3),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO yandex_economics_sources "
                            "(store_slug,article,source,payload_json,updated_at) VALUES (?,?,?,?,?)",
                            (
                                key[1],
                                key[2],
                                "day-input:" + self.day + ":COMMON",
                                repo.encode({"values": values, "version": 15}),
                                "2026-09-24T09:00:00Z",
                            ),
                        )
                    conn.commit()
                before = repo.get(key)
                self.assertEqual(before["source"]["basis"], "legacy")
                self.assertEqual(repo.first_day(market, key[1], {key[2]}), self.day)
                self.assertEqual(repo.records(market, (key[1],), self.day, self.day), [])
                self.assertEqual(
                    repo.day_records(market, key[1], self.day)[key[2]]["values"]["purchase_price"], 250
                )
                after = self.correction(key, "purchase_price", 300)
                self.assertEqual(after["original"], before["original"])
                self.assertEqual(after["values"]["purchase_price"], 300)
                self.assertEqual(after["revision"], 1)

    def test_dated_metric_refresh_preserves_costs_original_and_overrides(self):
        for market in ("WB", "YANDEX MARKET"):
            key = (market, *self.key[1:])
            original = self.capture(key)
            self.correction(key, "purchase_price", 300)
            self.correction(key, "advertising_spend", 20)
            self.capture(key, {**inputs(market), "purchase_price": 280})
            raw = {"received_at": "2026-09-25T09:00:00Z", "orders": [], "advertising": []}
            self.assertTrue(repo.refresh_metrics(key, {"orders_count": 12, "advertising_spend": 200}, raw))
            after = repo.get(key)
            self.assertEqual(after["original"], original["original"])
            self.assertEqual(after["source"]["values"]["purchase_price"], 280)
            self.assertEqual(after["values"]["purchase_price"], 300)
            self.assertEqual(after["values"]["advertising_spend"], 20)
            self.assertEqual(after["values"]["orders_count"], 12)
            self.assertEqual(after["source_times"]["orders_count"], raw["received_at"])
            self.assertFalse(repo.refresh_metrics(key, {"orders_count": 12}, raw))
            with self.assertRaises(ValueError):
                repo.refresh_metrics(key, {"purchase_price": 999}, raw)

    def test_partial_day_summary_explains_missing_without_treating_zero_as_missing(self):
        self.capture(values={**inputs(), "fulfillment_cost": None, "advertising_spend": None})
        base = "/api/unit-economics-1c/calendar"
        query = {"store": "rimili", "day": self.day}
        partial = self.client.get(base, params=query).json()
        self.assertIsNotNone(partial["day_profit"])
        self.assertFalse(partial["profit_complete"])
        self.assertIn("fulfillment_cost", partial["profit_missing"][0]["parameters"])
        self.assertIn("advertising_spend", partial["profit_missing"][0]["parameters"])
        self.correction(self.key, "orders_count", 0)
        self.correction(self.key, "advertising_spend", 50)
        zero_orders = self.client.get(base, params=query).json()
        self.assertEqual(zero_orders["day_profit"], -50)
        self.assertTrue(zero_orders["profit_complete"])
        self.assertEqual(zero_orders["profit_missing"], [])

    def test_writers_reject_backdated_current_configuration_before_reading_sources(self):
        from app.economics.wb.history import save_daily_margin_snapshots
        from app.yandex.economics import capture_today

        yesterday = self.today - timedelta(days=1)
        with self.assertRaises(ValueError):
            save_daily_margin_snapshots(yesterday, store_slugs=("rimili",))
        with self.assertRaises(ValueError):
            capture_today(("rimili",), today=yesterday)

    def test_source_time_tracks_actual_cabinet_fallback(self):
        values = {
            **inputs(),
            "source_synced_at": "2026-09-24T09:00:00Z",
            "cabinet_settings_updated_at": "2026-09-23T08:00:00Z",
            "buyout_default_applied": True,
        }
        source = repo.observation(values, raw={"reference": {"team_commission_percent": None}})
        times = repo.source_times("WB", source)
        self.assertEqual(times["purchase_price"], values["source_synced_at"])
        self.assertEqual(times["team_commission_percent"], values["cabinet_settings_updated_at"])
        self.assertEqual(times["buyout_percent"], values["cabinet_settings_updated_at"])
        source["raw"]["reference"]["team_commission_percent"] = 0
        self.assertEqual(
            repo.source_times("WB", source)["team_commission_percent"], values["source_synced_at"]
        )


if __name__ == "__main__":
    unittest.main()
