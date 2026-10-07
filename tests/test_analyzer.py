"""Analyzer joins, missing-data semantics, dated notes and access checks; isolated DB."""

import os
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import dotenv

with (
    patch.object(dotenv, "dotenv_values", return_value={}),
    patch.dict(os.environ, {"CHECKSTOCK_DATABASE_URL": ""}),
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.access.sections import has_access, section_for_path
    from app.analytics import analyzer
    from app.dto.identity import SectionAccessLevel, SectionName, User
    from app.infrastructure.database import Database
    from app.infrastructure.orm import OrmBase
    from app.repositories import analyzer as repository
    from app.repositories import core
    from app.web.routers import analyzer as api


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="checkstock-analyzer-")
        self.database = Database(path=Path(self.directory.name) / "isolated.sqlite")
        OrmBase.metadata.create_all(self.database.engine)
        self.database_patch = patch.object(core, "database_for_path", return_value=self.database)
        self.database_patch.start()
        self.clock_patch = patch.object(api, "datetime", wraps=datetime)
        self.clock_patch.start().now.return_value = datetime(2026, 10, 7, 12, tzinfo=UTC)
        self.user = self.make_user()
        app = FastAPI()

        @app.middleware("http")
        async def identify(request, call_next):
            request.state.user = self.user
            return await call_next(request)

        app.include_router(api.router)
        self.client = TestClient(app)
        self.execute(
            "INSERT INTO stock_items (store_slug,marketplace,article,barcode,name) VALUES ('rimili','WB','123','00123','Первый товар')"
        )
        self.execute(
            "INSERT INTO stock_items (store_slug,marketplace,article,barcode,name) VALUES ('rimili','WB','456','00456','Без остатков')"
        )
        self.execute(
            "INSERT INTO stock_items (store_slug,marketplace,article,barcode,name) VALUES ('tris','WB','123','00999','Другой проект')"
        )
        for product_id, manager in ((1, "Менеджер Один"), (2, "Менеджер Два"), (3, "Менеджер Один")):
            self.execute(
                "INSERT INTO unit_economics_1c_source_values (stock_item_id,manager,goal_week,goal_day,abc_code,source_sheet_id,source_sheet_title,source_row,synced_at) VALUES (?,?,70,12,'NEW',1,'WB',1,'2026-10-05')",
                (product_id, manager),
            )
        self.execute(
            "INSERT INTO wb_funnel_daily_orders (store_slug,day,article,orders_count,orders_amount,buyout_percent,updated_at) VALUES ('rimili','2026-10-05','123',10,10000,50,'2026-10-06')"
        )
        self.execute(
            "INSERT INTO wb_funnel_daily_orders (store_slug,day,article,orders_count,orders_amount,buyout_percent,updated_at) VALUES ('tris','2026-10-05','123',99,99000,50,'2026-10-06')"
        )
        self.execute(
            "INSERT INTO wb_funnel_daily_orders (store_slug,day,article,orders_count,orders_amount,updated_at) VALUES ('rimili','2026-09-28','123',4,2000,'2026-09-29')"
        )
        self.execute(
            "INSERT INTO unit_economics_1c_wb_daily_advertising (store_slug,nm_id,day,spend,impressions,clicks,synced_at) VALUES ('rimili','123','2026-10-05',100,1000,40,'2026-10-06')"
        )
        self.execute(
            "INSERT INTO rnp_daily_metrics (store_slug,marketplace,article,day,traffic_carts,ad_impressions,ad_clicks) VALUES ('rimili','WB','123','05-10-2026',7,900,20)"
        )
        self.execute(
            "INSERT INTO unit_economics_1c_wb_daily_prices (store_slug,article,day,nm_id,retail_price,customer_price_with_spp,customer_price_with_wallet,updated_at) VALUES ('rimili','123','2026-10-05','123',1000,750,725,'2026-10-05')"
        )

    def tearDown(self):
        self.client.close()
        self.database_patch.stop()
        self.clock_patch.stop()
        self.database.dispose()
        self.directory.cleanup()

    def execute(self, sql, params=()):
        with self.database.connect() as conn:
            conn.execute(sql, params)
            conn.commit()

    def make_user(self, **changes):
        return User(
            id=1,
            login="analyzer",
            full_name="Менеджер Один",
            created_at=datetime.now(UTC),
            role=changes.pop("role", "superadmin"),
            **changes,
        )

    def load(self, stores=("rimili",), week=date(2026, 10, 5)):
        return analyzer.load(stores, week, self.user, today=date(2026, 10, 7))

    def test_all_catalog_products_and_real_values(self):
        rows = self.load()["rows"]
        self.assertEqual(len(rows), 2)
        row = next(r for r in rows if r["article"] == "123")
        self.assertEqual(row["barcode"], "00123")
        self.assertEqual(row["week"], [10, None, None, None, None, None, None])
        self.assertEqual(row["impressions"], [None, 1000, None, None])
        self.assertEqual(row["ctr"][1], 4)
        self.assertEqual(row["carts"][1], 7)
        self.assertEqual(row["drr"], 2)
        self.assertEqual(row["previousPrice"], 500)
        self.assertEqual(row["spp"], 25)
        self.assertEqual(row["wallet"], 725)
        self.assertEqual(row["dayGoal"], 12)
        self.assertEqual(row["plan"], 6000)
        self.assertEqual(row["coeff"], 1)
        for key in ("stock", "roi", "fact", "forecast", "difference", "deviation", "stockDays"):
            self.assertIsNone(row[key], key)

    def test_loaded_empty_days_are_zero_unknown_and_future_remain_null(self):
        self.execute(
            "INSERT INTO economics_source_days (marketplace,store_slug,source,day,updated_at) VALUES ('WB','rimili','orders','2026-10-06','2026-10-07')"
        )
        self.execute(
            "INSERT INTO economics_source_days (marketplace,store_slug,source,day,updated_at) VALUES ('WB','rimili','advertising','2026-10-06','2026-10-07')"
        )
        rows = self.load()["rows"]
        self.assertEqual(rows[0]["week"][:3], [0, 0, None])
        self.assertEqual(rows[0]["impressions"][2], 0)
        self.assertIsNone(rows[0]["impressions"][3])
        self.assertTrue(all(value is None for value in rows[0]["week"][3:]))

    def test_project_and_article_join_do_not_mix_cabinets(self):
        rows = self.load(("rimili", "tris"))["rows"]
        self.assertEqual(next(r for r in rows if r["store_slug"] == "tris")["week"][0], 99)
        self.assertEqual(next(r for r in rows if r["id"] == "rimili:123")["week"][0], 10)

    def test_week_switch_changes_dates_and_metrics(self):
        data = self.load(week=date(2026, 9, 30))
        self.assertEqual(data["week"][0], "2026-09-28")
        self.assertEqual(data["traffic_days"], ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"])
        self.assertEqual(next(r for r in data["rows"] if r["article"] == "123")["week"][0], 4)

    def test_monday_uses_sunday_snapshot_for_yesterday_drr(self):
        self.execute(
            "INSERT INTO wb_funnel_daily_orders (store_slug,day,article,orders_count,orders_amount,updated_at) VALUES ('rimili','2026-10-04','123',10,10000,'2026-10-05')"
        )
        self.execute(
            "INSERT INTO unit_economics_1c_wb_daily_advertising (store_slug,nm_id,day,spend,impressions,clicks,synced_at) VALUES ('rimili','123','2026-10-04',100,1000,40,'2026-10-05')"
        )
        self.execute(
            "INSERT INTO unit_economics_1c_daily_margin_snapshots (store_slug,marketplace,article,day,unit_margin,calculation_version,inputs_json,result_json,captured_at) VALUES ('rimili','WB','123','2026-10-04',0,3,?, '{}','2026-10-05')",
            ('{"buyout_percent":25}',),
        )
        data = analyzer.load(("rimili",), date(2026, 10, 5), self.user, today=date(2026, 10, 5))
        row = next(r for r in data["rows"] if r["article"] == "123")
        self.assertEqual(row["spend"], 100)
        self.assertEqual(row["yesterdayDrr"], 4)

    def test_manager_scope_and_missing_scope(self):
        self.user = self.make_user(role="user", store_slugs=("rimili",))
        self.assertEqual([r["article"] for r in self.load()["rows"]], ["123"])
        response = self.client.get("/api/analytics/analyzer?week=2026-10-05")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["id"] for r in response.json()["rows"]], ["rimili:123"])

    def test_section_defaults_and_routes_are_protected(self):
        self.user = self.make_user(
            role="admin", section_access={SectionName.UNIT_ECONOMICS_WB: SectionAccessLevel.NONE}
        )
        self.assertFalse(has_access(self.user, SectionName.ANALYZER))
        for path in ("/analytics/analyzer", "/api/analytics/analyzer", "/api/analytics/analyzer/notes"):
            self.assertEqual(section_for_path(path), SectionName.ANALYZER)
        self.assertEqual(self.client.get("/api/analytics/analyzer").status_code, 403)
        self.assertEqual(self.client.get("/analytics/analyzer").status_code, 403)

    def test_notes_persist_by_project_article_and_date_and_append(self):
        payload = {"store": "rimili", "article": "123", "day": "2026-10-07", "note": "Проверили рекламу"}
        self.assertEqual(self.client.post("/api/analytics/analyzer/notes", json=payload).status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/analytics/analyzer/notes", json={**payload, "note": "Изменили ставки"}
            ).status_code,
            200,
        )
        rows = self.load(("rimili", "tris"))["rows"]
        saved = next(r for r in rows if r["id"] == "rimili:123")["notes"]
        self.assertEqual([n["note"] for n in saved[2]], ["Проверили рекламу", "Изменили ставки"])
        self.assertEqual(saved[1], [])
        self.assertEqual(next(r for r in rows if r["id"] == "tris:123")["notes"][0], [])
        self.assertEqual(repository.notes(("rimili",), "2026-09-28", "2026-10-04"), [])

    def test_notes_reject_read_only_cross_store_cross_manager_and_blank(self):
        payload = {"store": "rimili", "article": "123", "day": "2026-10-07", "note": "Запись"}
        self.user = self.make_user(
            role="admin",
            store_slugs=("rimili",),
            section_access={SectionName.ANALYZER: SectionAccessLevel.READ},
        )
        self.assertEqual(self.client.post("/api/analytics/analyzer/notes", json=payload).status_code, 403)
        self.user = self.make_user(role="user", store_slugs=("rimili",))
        self.assertEqual(
            self.client.post("/api/analytics/analyzer/notes", json={**payload, "store": "tris"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post("/api/analytics/analyzer/notes", json={**payload, "article": "456"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/api/analytics/analyzer/notes", json={**payload, "note": "  "}).status_code, 422
        )

    def test_stock_days_use_last_complete_calendar_week_not_goal_or_selected_week(self):
        self.execute(
            "INSERT INTO mp_stock (store_slug,article,marketplace,scheme,quantity) VALUES ('rimili','123','WB','fbo',140)"
        )
        self.execute("DELETE FROM wb_funnel_daily_orders WHERE store_slug='rimili' AND day<'2026-10-05'")
        for offset in range(7):
            day = (date(2026, 9, 28) + timedelta(days=offset)).isoformat()
            self.execute(
                "INSERT INTO wb_funnel_daily_orders (store_slug,day,article,orders_count,orders_amount,updated_at) VALUES ('rimili',?,'123',10,5000,'2026-10-05')",
                (day,),
            )
        for selected in (date(2026, 10, 5), date(2026, 9, 14)):
            data = self.load(week=selected)
            row = next(r for r in data["rows"] if r["article"] == "123")
            self.assertEqual(row["stock"], 140)
            self.assertEqual(row["stockDailyOrders"], 10)
            self.assertEqual(row["stockDays"], 14)
            self.assertTrue(row["stockOrdersComplete"])
            self.assertEqual(
                (data["stockPreviousFrom"], data["stockPreviousTo"]), ("2026-09-28", "2026-10-04")
            )

    def test_stock_missing_days_and_zero_demand_do_not_invent_coverage(self):
        self.execute(
            "INSERT INTO mp_stock (store_slug,article,marketplace,scheme,quantity) VALUES ('rimili','123','WB','fbo',140)"
        )
        row = next(r for r in self.load()["rows"] if r["article"] == "123")
        self.assertIsNone(row["stockDailyOrders"])
        self.assertIsNone(row["stockDays"])
        self.assertFalse(row["stockOrdersComplete"])
        for offset in range(7):
            self.execute(
                "INSERT INTO economics_source_days (marketplace,store_slug,source,day,updated_at) VALUES ('WB','rimili','orders',?,'2026-10-05')",
                ((date(2026, 9, 28) + timedelta(days=offset)).isoformat(),),
            )
        self.execute("DELETE FROM wb_funnel_daily_orders WHERE store_slug='rimili' AND day<'2026-10-05'")
        row = next(r for r in self.load()["rows"] if r["article"] == "123")
        self.assertEqual(row["stockDailyOrders"], 0)
        self.assertTrue(row["stockOrdersComplete"])
        self.assertIsNone(row["stockDays"])

    def test_turnover_uses_today_gross_funnel_orders_and_average_price(self):
        self.execute(
            "INSERT INTO wb_funnel_daily_orders (store_slug,day,article,orders_count,orders_amount,cancel_amount,buyout_amount,updated_at) VALUES ('rimili','2026-10-07','123',8,4000,1000,2000,'2026-10-07')"
        )
        data = self.load()
        row = next(r for r in data["rows"] if r["article"] == "123")
        self.assertEqual(data["turnover_day"], "2026-10-07")
        self.assertEqual(row["plan"], 6000)
        self.assertEqual(row["fact"], 4000)
        self.assertEqual(row["forecast"], 4000)
        self.assertEqual(row["difference"], 2000)
        self.assertEqual(row["deviation"], 33.33)
        old = next(r for r in self.load(week=date(2026, 9, 28))["rows"] if r["article"] == "123")
        self.assertEqual(old["fact"], 4000)
        self.assertIsNone(old["plan"])

    def test_today_turnover_loaded_zero_and_unknown_are_different(self):
        unknown = next(r for r in self.load()["rows"] if r["article"] == "123")
        self.assertIsNone(unknown["fact"])
        self.execute(
            "INSERT INTO economics_source_days (marketplace,store_slug,source,day,updated_at) VALUES ('WB','rimili','orders','2026-10-07','2026-10-07')"
        )
        zero = next(r for r in self.load()["rows"] if r["article"] == "123")
        self.assertEqual(zero["fact"], 0)
        self.assertEqual(zero["forecast"], 0)
        self.assertEqual(zero["difference"], 6000)
        self.assertEqual(zero["deviation"], 100)

    def test_notes_history_extends_to_today_and_today_edits_preserve_author_and_revisions(self):
        for day in ("2026-09-27", "2026-09-28", "2026-10-02", "2026-10-07", "2026-10-08"):
            repository.add_note("rimili", "123", day, "Заметка " + day, self.user)
        row = next(r for r in self.load(week=date(2026, 9, 28))["rows"] if r["article"] == "123")
        self.assertEqual(
            [n["action_date"] for n in row["noteHistory"]], ["2026-09-28", "2026-10-02", "2026-10-07"]
        )
        self.assertEqual([n["can_edit"] for n in row["noteHistory"]], [False, False, True])
        today_note = row["noteHistory"][-1]
        self.user = self.user.model_copy(update={"full_name": "Другой редактор"})
        response = self.client.put(
            f"/api/analytics/analyzer/notes/{today_note['id']}", json={"note": "Исправленный текст"}
        )
        self.assertEqual(response.status_code, 200)
        updated = response.json()["note"]
        self.assertEqual(updated["note"], "Исправленный текст")
        self.assertEqual(updated["user_name"], "Менеджер Один")
        self.assertEqual(updated["created_at"], today_note["created_at"])
        self.assertEqual(updated["updated_by"], "Другой редактор")
        self.assertEqual(updated["revisions"][0]["previous_note"], "Заметка 2026-10-07")
        reloaded = next(r for r in self.load()["rows"] if r["article"] == "123")
        self.assertEqual(reloaded["noteHistory"][0]["note"], "Исправленный текст")

    def test_notes_reject_past_future_writes_and_foreign_or_read_only_updates(self):
        for day in ("2026-10-06", "2026-10-08"):
            payload = {"store": "rimili", "article": "123", "day": day, "note": "Текст"}
            self.assertEqual(self.client.post("/api/analytics/analyzer/notes", json=payload).status_code, 422)
            note = repository.add_note("rimili", "123", day, "Старая запись", self.user)
            self.assertEqual(
                self.client.put(
                    f"/api/analytics/analyzer/notes/{note['id']}", json={"note": "Правка"}
                ).status_code,
                422,
            )
        today_note = repository.add_note("rimili", "456", "2026-10-07", "Другой менеджер", self.user)
        path = f"/api/analytics/analyzer/notes/{today_note['id']}"
        self.user = self.make_user(role="user", store_slugs=("rimili",))
        self.assertEqual(self.client.put(path, json={"note": "Правка"}).status_code, 404)
        foreign = repository.add_note("tris", "123", "2026-10-07", "Другой магазин", self.user)
        self.assertEqual(
            self.client.put(
                f"/api/analytics/analyzer/notes/{foreign['id']}", json={"note": "Правка"}
            ).status_code,
            404,
        )
        self.user = self.make_user(
            role="admin", section_access={SectionName.ANALYZER: SectionAccessLevel.READ}
        )
        self.assertEqual(self.client.put(path, json={"note": "Правка"}).status_code, 403)

    def test_future_week_rejected(self):
        self.assertEqual(self.client.get("/api/analytics/analyzer?week=2099-10-05").status_code, 422)
        self.assertEqual(self.client.get("/api/analytics/analyzer?week=0001-01-01").status_code, 422)
        self.assertEqual(self.client.get("/api/analytics/analyzer?week=invalid").status_code, 422)

    def test_page_navigation_and_live_script_no_demo(self):
        response = self.client.get("/analytics/analyzer")
        self.assertEqual(response.status_code, 200)
        page = response.text
        self.assertLess(page.index('title="Аналитика"'), page.index('title="Юнит-экономика 1С"'))
        self.assertIn("/static/analytics/analyzer.js", page)
        self.assertNotIn("Демонстрационные значения", page)
        self.assertNotIn(">МАКЕТ<", page)


if __name__ == "__main__":
    unittest.main()
