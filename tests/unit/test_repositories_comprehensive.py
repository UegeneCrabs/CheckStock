import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db, decision_center, rnp_analytics
from app.dto.unit_economics_1c import (
    UnitEconomics1CCabinetSettingsRequest,
    UnitEconomics1CProductSettingsRequest,
)
from app.repositories import core, stock_dashboard

NOW = "2026-08-12T10:00:00+00:00"


def sales_line(**overrides) -> dict:
    line = {
        "store_slug": "rimili",
        "marketplace": "WB",
        "order_key": "order-1",
        "line_key": "line-1",
        "external_order_id": "external-1",
        "scheme": "fbs",
        "status": "sold",
        "substatus": "",
        "article": "A-1",
        "barcode": "460000000001",
        "name": "Товар 1",
        "ordered_at": "2026-08-10T10:00:00+00:00",
        "source_updated_at": NOW,
        "cancelled_at": None,
        "sold_at": "2026-08-11T10:00:00+00:00",
        "returned_at": None,
        "quantity": 2,
        "cancelled_quantity": 0,
        "sold_quantity": 2,
        "return_quantity": 0,
        "order_amount": 2000.0,
        "cancelled_amount": 0.0,
        "sale_amount": 1800.0,
        "return_amount": 0.0,
        "currency": "RUB",
        "raw_json": "{}",
    }
    line.update(overrides)
    return line


class RepositoryUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_patch = mock.patch.object(
            core,
            "DB_PATH",
            Path(self.temporary_directory.name) / "unit.db",
        )
        self.database_patch.start()
        db.init_db()
        decision_center.init_schema()
        rnp_analytics.init_schema()

    def tearDown(self) -> None:
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def add_catalog(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [
                {
                    "article": "A-1",
                    "barcode": "460000000001",
                    "name": "Первый товар",
                    "mp_sku": "1001",
                    "mp_product_id": "P-1",
                    "image_url": "https://example.test/1.jpg",
                    "mp_updated_at": NOW,
                },
                {
                    "article": "B-2",
                    "barcode": "460000000002",
                    "name": "Второй товар",
                },
                {
                    "article": "SERVICE",
                    "barcode": "",
                    "name": "Служебный",
                    "is_service": True,
                },
            ],
            NOW,
        )

    def test_identity_sessions_permissions_and_activity(self) -> None:
        self.assertEqual(db.count_users(), 0)
        self.assertEqual(db.normalize_store_slugs([])[0], "rimili")
        self.assertEqual(
            db.normalize_store_slugs(["RIMILI", "unknown", "rimili", "TRIS"]),
            ["rimili", "tris"],
        )
        admin_id = db.create_user(
            "Admin",
            "admin@example.test",
            "admin",
            "hash-1",
            "superadmin",
            NOW,
            ["rimili"],
        )
        user_id = db.create_user(
            "User",
            "user@example.test",
            "user",
            "hash-2",
            "user",
            NOW,
            ["tris"],
        )
        self.assertEqual(db.count_users(), 2)
        self.assertEqual(db.count_superadmins(), 1)
        self.assertEqual(db.count_superadmins(exclude_user_id=admin_id), 0)
        self.assertEqual(db.get_user_by_login("admin")["store_slugs"], ["rimili"])
        self.assertEqual(db.get_user(user_id)["full_name"], "User")
        self.assertIsNone(db.get_user(9999))
        self.assertEqual(len(db.list_users()), 2)

        db.set_user_store_access(user_id, ["rimili", "tris"])
        self.assertEqual(db.get_user_store_access(user_id), ["rimili", "tris"])
        db.set_user_permission(user_id, "can_edit_stock", True)
        db.set_user_permission(user_id, "can_manage_users", False)
        with self.assertRaises(ValueError):
            db.set_user_permission(user_id, "admin", True)
        db.set_user_active(user_id, False)
        self.assertEqual(db.get_user(user_id)["is_active"], 0)

        db.create_session("expired", user_id, NOW, "2020-01-01T00:00:00+00:00")
        db.create_session("active", user_id, NOW, "2099-01-01T00:00:00+00:00")
        self.assertEqual(db.get_session("active")["user_id"], user_id)
        self.assertIsNone(db.get_session("missing"))
        db.delete_expired_sessions(NOW)
        self.assertIsNone(db.get_session("expired"))
        db.delete_session("active")
        self.assertIsNone(db.get_session("active"))

        db.log_action(user_id, "User", "created", "details", NOW)
        self.assertEqual(db.get_activity_log(1)[0]["action"], "created")
        db.upsert_wb_token_info("rimili", "2027-01-01T00:00:00+00:00", NOW)
        db.upsert_wb_token_info("rimili", None, "2026-08-12T11:00:00+00:00")
        self.assertEqual(len(db.get_wb_token_infos()), 1)
        self.assertEqual(db.get_last_token_check(), "2026-08-12T11:00:00+00:00")

        db.create_session("password-session", user_id, NOW, "2099-01-01T00:00:00+00:00")
        db.update_user_password(user_id, "new-hash")
        self.assertIsNone(db.get_session("password-session"))
        db.create_session("delete-session", user_id, NOW, "2099-01-01T00:00:00+00:00")
        db.delete_sessions_for_user(user_id)
        self.assertIsNone(db.get_session("delete-session"))
        db.delete_user(user_id)
        self.assertIsNone(db.get_user(user_id))

    def test_catalog_marketplace_and_warehouse_stock(self) -> None:
        self.add_catalog()
        self.assertEqual(len(db.get_catalog_items("rimili", "WB")), 2)
        self.assertEqual(len(db.get_catalog_items("rimili", "WB", include_service=True)), 3)

        db.upsert_ff_stock("rimili", "A-1", "ФФ", 5, NOW, "WB")
        db.upsert_ff_stock("rimili", "B-2", "ФФ", 2, NOW, "WB")
        db.upsert_mp_stock("rimili", "A-1", "WB", "fbs", 7, NOW)
        db.upsert_mp_stock("rimili", "A-1", "WB", "fbo", 3, NOW)
        rows = db.get_stock_items("rimili", "WB", ("fbs", "fbo"))
        self.assertEqual(rows[0]["ff_available"], 5)
        self.assertEqual(rows[0]["fbs_stock"], 7)
        self.assertEqual(db.get_mp_stock_totals("rimili", "WB", "fbs"), {"A-1": 7})
        self.assertEqual(db.get_last_sync_at("WB"), NOW)
        self.assertEqual(db.get_last_sync_at(), NOW)

        db.upsert_mp_stock("rimili", "A-1", "YANDEX MARKET", "fbs", 17, NOW)
        db.upsert_mp_stock("rimili", "A-1", "YANDEX MARKET", "fbs_149217490", 17, NOW)
        db.replace_mp_warehouse_stock(
            "rimili",
            "YANDEX MARKET",
            "fbs_149217490",
            [("A-1", "Afflatus", None, 17, NOW)],
        )
        db.delete_mp_stock_scheme_variants("rimili", "YANDEX MARKET", "fbs")
        self.assertEqual(db.get_mp_stock_totals("rimili", "YANDEX MARKET", "fbs"), {"A-1": 17})
        self.assertEqual(db.get_mp_stock_totals("rimili", "YANDEX MARKET", "fbs_149217490"), {})
        self.assertEqual(db.get_mp_warehouse_details("rimili", "YANDEX MARKET", "fbs_149217490"), [])
        db.upsert_mp_stock("rimili", "A-1", "YANDEX MARKET", "fbs", 0, NOW)

        db.replace_mp_warehouse_stock(
            "rimili",
            "WB",
            "fbo",
            [
                ("A-1", "Коледино", "Москва", 3, NOW),
                ("A-1", "Пустой", None, 0, NOW),
            ],
        )
        self.assertEqual(db.get_mp_stock_by_warehouse("rimili", "WB", "fbo", "Коледино"), {"A-1": 3})
        self.assertEqual(db.get_mp_warehouse_details("rimili", "WB", "fbo")[0]["warehouse"], "Коледино")
        self.assertEqual(
            db.get_mp_warehouse_details("rimili", "WB", "fbo", group_by_cluster=True)[0]["warehouse"],
            "Москва",
        )

        db.replace_mp_warehouse_stock(
            "rimili",
            "WB",
            "fbo",
            [("A-1", "Пустой", None, 0, NOW)],
        )
        self.assertEqual(db.get_mp_warehouse_details("rimili", "WB", "fbo"), [])

        self.assertEqual(db.save_warehouse_clusters("WB", {}, NOW), 0)
        self.assertEqual(db.save_warehouse_clusters("WB", {"Коледино": "Центр"}, NOW), 1)
        self.assertEqual(db.save_warehouse_clusters("WB", {"Коледино": "Москва"}, NOW), 0)
        self.assertEqual(db.get_warehouse_clusters("WB"), {"Коледино": "Москва"})
        db.replace_ff_warehouse_map("rimili", [("ФФ", 10, "Склад 10", NOW)])
        self.assertEqual(db.get_ff_warehouse_map("rimili")[0]["wb_warehouse_id"], 10)

        overview = db.get_stock_overview()["rimili"]
        self.assertEqual(overview["marketplace_stock"], 10)
        self.assertEqual(overview["fulfillment_stock"], 7)
        self.assertEqual(overview["total_stock"], 17)

        report = db.replace_catalog(
            "rimili",
            "WB",
            [
                {
                    "article": "A-1",
                    "barcode": "460000000009",
                    "name": "Обновлённый товар",
                }
            ],
            "2026-08-12T12:00:00+00:00",
            force_remove_articles={"B-2"},
        )
        self.assertEqual(report, {"added": 0, "updated": 1, "removed": 2, "kept": 0, "forced_removed": 1})
        db.upsert_mp_stock("rimili", "A-1", "WB", "fbs", 0, NOW)
        self.assertEqual(db.get_mp_stock_totals("rimili", "WB", "fbs"), {})
        db.upsert_ff_stock("rimili", "A-1", "ФФ", 0, NOW, "WB")
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "ФФ", "WB"), 0)

    def test_daily_stock_history_keeps_marketplace_zeroes_and_ff_breakdown(self) -> None:
        self.add_catalog()
        db.upsert_mp_stock("rimili", "A-1", "WB", "fbs", 7, NOW)
        db.upsert_mp_stock("rimili", "A-1", "WB", "fbo", 3, NOW)
        db.upsert_ff_stock("rimili", "A-1", "ФФ-1", 100, NOW, "WB")
        db.upsert_ff_stock("rimili", "A-1", "ФФ-2", 60, NOW, "WB")

        self.assertEqual(
            db.replace_marketplace_stock_daily_history("rimili", "WB", "fbs", "2026-08-20", NOW),
            2,
        )
        self.assertEqual(
            db.replace_marketplace_stock_daily_history("rimili", "WB", "fbo", "2026-08-20", NOW),
            2,
        )
        self.assertEqual(db.replace_fulfillment_stock_daily_history("2026-08-20", NOW), 2)

        history = db.get_daily_stock_history(("rimili",), "WB", "2026-08-20", "2026-08-20")
        by_article = {row["article"]: row for row in history}
        self.assertEqual(by_article["A-1"]["fbs"], 7)
        self.assertEqual(by_article["A-1"]["fbo"], 3)
        self.assertEqual(by_article["A-1"]["fulfillment"], 160)
        self.assertEqual(by_article["B-2"]["fbs"], 0)
        self.assertEqual(by_article["B-2"]["fbo"], 0)

        db.upsert_ff_stock("rimili", "A-1", "ФФ-2", 0, NOW, "WB")
        self.assertEqual(db.replace_fulfillment_stock_daily_history("2026-08-21", NOW), 2)
        ff_rows = db.get_fulfillment_stock_daily_history("rimili", "WB", "A-1", "2026-08-20", "2026-08-21")
        quantities = {(row["day"], row["fulfillment"]): row["quantity"] for row in ff_rows}
        self.assertEqual(quantities[("2026-08-20", "ФФ-1")], 100)
        self.assertEqual(quantities[("2026-08-20", "ФФ-2")], 60)
        self.assertEqual(quantities[("2026-08-21", "ФФ-1")], 100)
        self.assertEqual(quantities[("2026-08-21", "ФФ-2")], 0)

        with self.assertRaises(ValueError):
            db.replace_marketplace_stock_daily_history("rimili", "WB", "wrong", "2026-08-20", NOW)

    def test_legacy_catalog_exclusion_table_is_removed_before_catalog_refresh(self) -> None:
        connection = core.get_connection()
        connection.execute(
            """
            CREATE TABLE catalog_product_exclusions (
                store_slug TEXT NOT NULL,
                marketplace TEXT NOT NULL,
                nm_id TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (store_slug, marketplace, nm_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO catalog_product_exclusions
                (store_slug, marketplace, nm_id, status, updated_at)
            VALUES ('rimili', 'WB', '200', 'Старье', ?)
            """,
            (NOW,),
        )
        connection.commit()
        connection.close()
        db.init_db()
        connection = core.get_connection()
        legacy_table = connection.execute(
            """
            SELECT name FROM sqlite_master
             WHERE type = 'table' AND name = 'catalog_product_exclusions'
            """
        ).fetchone()
        connection.close()
        self.assertIsNone(legacy_table)

        db.replace_catalog(
            "rimili",
            "WB",
            [
                {"article": "100", "barcode": "bc-100", "name": "Активный"},
                {"article": "200", "barcode": "bc-200", "name": "Старье"},
            ],
            NOW,
        )
        db.upsert_mp_stock("rimili", "200", "WB", "fbs", 7, NOW)

        self.assertEqual(
            [row["article"] for row in db.get_catalog_items("rimili", "WB")],
            ["100", "200"],
        )
        self.assertEqual(db.get_mp_stock_totals("rimili", "WB", "fbs")["200"], 7)

        db.replace_catalog(
            "rimili",
            "WB",
            [
                {"article": "100", "barcode": "bc-100", "name": "Активный"},
                {"article": "200", "barcode": "bc-200", "name": "Вернулся из API"},
            ],
            NOW,
        )
        self.assertEqual(
            [row["article"] for row in db.get_catalog_items("rimili", "WB")],
            ["100", "200"],
        )

    def test_fulfillment_sources_search_and_movements(self) -> None:
        self.add_catalog()
        db.increment_ff_stock("rimili", "A-1", "ФФ-1", 0, NOW, "WB")
        db.increment_ff_stock("rimili", "A-1", "ФФ-1", 10, NOW, "WB")
        db.increment_ff_stock("rimili", "A-1", "ФФ-1", -2, NOW, "WB")
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "ФФ-1", "WB"), 8)
        self.assertEqual(db.get_ff_available_totals("rimili"), {"A-1": 8})
        self.assertEqual(db.get_ff_available_totals("rimili", "ФФ-1", "WB"), {"A-1": 8})

        self.assertEqual(db.search_catalog("rimili", "A-", marketplace="WB")[0]["article"], "A-1")
        self.assertEqual(db.search_catalog("rimili", "4600", marketplace="WB")[0]["article"], "A-1")
        self.assertEqual(db.search_catalog("rimili", "товар", marketplace="WB", limit=1)[0]["article"], "A-1")
        self.assertEqual(
            db.search_catalog("rimili", "Первый", fulfillment="ФФ-1", marketplace="WB")[0]["stock"], 8
        )
        self.assertEqual(db.search_catalog("rimili", ""), [])

        sheet_fingerprint = db.source_fingerprint("sheet", " https://sheet.test/1 ", None)
        file_fingerprint = db.source_fingerprint("file", None, b"content")
        self.assertEqual(sheet_fingerprint, "sheet:https://sheet.test/1")
        self.assertTrue(file_fingerprint.startswith("file:"))
        self.assertIsNone(db.source_fingerprint("manual", None, None))
        self.assertIsNone(db.find_used_source("rimili", "delivery", None))
        db.record_used_source("rimili", "delivery", None, "none", "manual", None, "User", NOW)
        db.record_used_source("rimili", "delivery", sheet_fingerprint, "Sheet", "sheet", None, "User", NOW)
        self.assertEqual(db.find_used_source("rimili", "delivery", sheet_fingerprint)["label"], "Sheet")

        db.record_delivery("rimili", "ФФ-1", "sheet", "https://sheet.test/1", "Таблица", 2, 1, 1, NOW, "WB")
        self.assertEqual(db.find_existing_delivery("rimili", "https://sheet.test/1", "", "WB")["matched"], 1)
        self.assertEqual(db.find_existing_delivery("rimili", None, "Таблица", "WB")["unmatched"], 1)
        self.assertIsNone(db.find_existing_delivery("rimili", None, "Нет", "WB"))

        db.record_sync_health("rimili", "WB", "catalog", False, "broken", NOW)
        self.assertEqual(db.get_sync_health("rimili")[0]["error"], "broken")
        db.record_sync_health("rimili", "WB", "catalog", True, None, NOW)
        self.assertEqual(db.get_sync_health("rimili"), [])

        db.apply_ff_transfer(
            "rimili",
            [("A-1", "A-1", 3)],
            "ФФ-1",
            "WB",
            "ФФ-2",
            "WB",
            1,
            "User",
            NOW,
        )
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "ФФ-1", "WB"), 5)
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "ФФ-2", "WB"), 3)
        self.assertEqual(len(db.get_ff_transfers("rimili", 10)), 1)
        self.assertEqual(len(db.get_ff_transfers(None, 10)), 1)

        db.apply_ff_shipment("rimili", [("A-1", 2)], "ФФ-1", "WB", NOW)
        with self.assertRaises(ValueError):
            db.apply_ff_shipment("rimili", [("A-1", 10)], "ФФ-1", "WB", NOW)
        self.assertEqual(db.get_ff_stock_one("rimili", "A-1", "ФФ-1", "WB"), 3)

        db.apply_ff_trash("rimili", [("A-1", 1)], "ФФ-1", "WB", NOW)
        trash = db.get_trash_details("rimili", "WB")
        self.assertEqual(trash[0]["quantity"], 1)
        db.set_trash_checked("rimili", "WB", "A-1", "ФФ-1", True)
        self.assertEqual(db.get_trash_details("rimili", "WB")[0]["checked"], 1)
        db.apply_ff_surplus("rimili", [("A-1", 1)], "ФФ-1", "WB", NOW)
        self.assertEqual(db.get_trash_details("rimili", "WB"), [])
        self.assertTrue(db.get_ff_warehouse_details_by_mp("rimili", "WB"))

    def test_fulfillment_names(self) -> None:
        db.seed_defaults()
        fulfillments = db.get_fulfillments()
        self.assertTrue(fulfillments)

    def test_legacy_unit_economics_storage_is_removed(self) -> None:
        user_id = db.create_user(
            "Legacy User",
            "legacy@example.test",
            "legacy",
            "hash",
            "user",
            NOW,
            ["rimili"],
        )
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO user_section_access (user_id, section, access_level) VALUES (?, ?, ?)",
            (user_id, "unit_economics", "read"),
        )
        conn.execute(
            "INSERT INTO user_section_usage "
            "(user_id, section, usage_date, page_views, active_seconds, last_viewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, "unit_economics", "2026-08-12", 1, 30, NOW),
        )
        conn.execute(
            "INSERT INTO user_usage_sessions "
            "(session_key, user_id, started_at, last_seen_at, active_seconds, last_section, last_path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-unit-session",
                user_id,
                NOW,
                NOW,
                30,
                "unit_economics",
                "/sales/unit-economics/wb-fbs",
            ),
        )
        conn.execute(
            "INSERT INTO sync_health (store_slug, marketplace, scope, ok, checked_at) VALUES (?, ?, ?, ?, ?)",
            ("rimili", "WB", "unit_prices", 1, NOW),
        )
        for table in ("fulfillment_unit_rates", "wb_unit_metrics", "unit_costs"):
            conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        db.init_db()

        conn = db.get_connection()
        access_rows = conn.execute(
            "SELECT section, access_level FROM user_section_access WHERE user_id=?",
            (user_id,),
        ).fetchall()
        tables = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        usage_count = conn.execute(
            "SELECT COUNT(*) FROM user_section_usage WHERE section='unit_economics'"
        ).fetchone()[0]
        session = conn.execute(
            "SELECT last_section, last_path FROM user_usage_sessions WHERE session_key=?",
            ("legacy-unit-session",),
        ).fetchone()
        sync_count = conn.execute("SELECT COUNT(*) FROM sync_health WHERE scope='unit_prices'").fetchone()[0]
        conn.close()
        self.assertEqual(
            [(row["section"], row["access_level"]) for row in access_rows],
            [("unit_economics_1c", "read")],
        )
        self.assertTrue({"fulfillment_unit_rates", "wb_unit_metrics", "unit_costs"}.isdisjoint(tables))
        self.assertEqual(usage_count, 0)
        self.assertEqual((session["last_section"], session["last_path"]), (None, None))
        self.assertEqual(sync_count, 0)

    def test_unit_economics_1c_cabinet_settings_defaults_and_save(self) -> None:
        defaults = db.get_unit_economics_1c_cabinet_settings("rimili")
        self.assertEqual(defaults.buyout_period_days, 14)
        self.assertEqual(defaults.acquiring_percent, 3.8)
        self.assertEqual(defaults.team_commission_percent, 0)
        self.assertEqual(defaults.vat_percent, 9)
        self.assertEqual(defaults.usn_percent, 0)
        self.assertEqual(defaults.osno_percent, 0)
        self.assertEqual(defaults.tax_system, "usn")
        self.assertIsNone(defaults.updated_at)

        saved = db.save_unit_economics_1c_cabinet_settings(
            "rimili",
            UnitEconomics1CCabinetSettingsRequest(
                buyout_period_days=21,
                acceptance_coefficient=1.25,
                wb_extra_tariff_percent=3.5,
                acquiring_percent=4.15,
                team_commission_percent=2.5,
                vat_percent=20,
                usn_percent=6,
                osno_percent=25,
                tax_system="osno",
            ),
            updated_at=NOW,
            updated_by_user_id=7,
            updated_by_name="Unit Admin",
        )
        self.assertEqual(saved.acceptance_coefficient, 1.25)
        self.assertEqual(saved.buyout_period_days, 21)
        self.assertEqual(saved.acquiring_percent, 4.15)
        self.assertEqual(saved.team_commission_percent, 2.5)
        self.assertEqual(saved.vat_percent, 20)
        self.assertEqual(saved.usn_percent, 6)
        self.assertEqual(saved.osno_percent, 25)
        self.assertEqual(saved.tax_system, "osno")
        self.assertEqual(saved.updated_by_name, "Unit Admin")
        self.assertEqual(
            db.list_unit_economics_1c_cabinet_settings(("rimili", "tris"))[1].vat_percent,
            9,
        )

    def test_unit_economics_1c_product_settings_defaults_and_save(self) -> None:
        defaults = db.get_unit_economics_1c_product_settings("rimili", "949558341")
        self.assertEqual(defaults.delivery_wb_rub, 0)
        self.assertIsNone(defaults.updated_at)

        saved = db.save_unit_economics_1c_product_settings(
            "rimili",
            UnitEconomics1CProductSettingsRequest(
                article="949558341",
                delivery_wb_rub=120,
                return_cost_rub=50,
                volume_l=1.2,
                storage_wb_rub=2.5,
            ),
            updated_at=NOW,
            updated_by_user_id=7,
            updated_by_name="Unit Admin",
        )
        self.assertEqual(saved.delivery_wb_rub, 120)
        self.assertEqual(saved.volume_l, 1.2)
        self.assertEqual(saved.updated_by_name, "Unit Admin")
        listed = db.list_unit_economics_1c_product_settings(("rimili", "tris"))
        self.assertEqual([(item.store_slug, item.article) for item in listed], [("rimili", "949558341")])

    def test_operations_sales_and_dashboard_queries(self) -> None:
        self.add_catalog()
        with core.get_connection() as connection:
            stock_item = connection.execute(
                "SELECT id FROM stock_items WHERE store_slug='rimili' "
                "AND marketplace='WB' AND article='A-1'"
            ).fetchone()
            connection.execute(
                """
                INSERT INTO unit_economics_1c_source_values
                    (stock_item_id, purchase_price, source_sheet_id,
                     source_sheet_title, source_row, synced_at)
                VALUES (?, 125.5, 1, 'Тест', 2, ?)
                """,
                (stock_item["id"], NOW),
            )
            connection.commit()
        operation_id = db.record_operation(
            "rimili",
            "delivery",
            "manual",
            [{"article": "A-1", "barcode": "460000000001", "name": "Товар", "quantity": 2}],
            1,
            "User",
            NOW,
            source_name="Manual",
            to_fulfillment="ФФ",
            to_marketplace="WB",
            note="note",
        )
        self.assertEqual(db.get_operation(operation_id)["note"], "note")
        self.assertIsNone(db.get_operation(9999))
        self.assertEqual(db.get_store_operations("rimili")[0]["positions"], 1)
        self.assertEqual(db.get_store_operations("rimili", ("delivery",), 1)[0]["units"], 2)
        self.assertEqual(db.get_store_operations("rimili", ("trash",)), [])
        operation_item = db.get_operation_items(operation_id)[0]
        self.assertEqual(operation_item["quantity"], 2)
        self.assertEqual(operation_item["purchase_price"], 125.5)
        self.assertEqual(operation_item["purchase_price_recorded"], 1)
        with core.get_connection() as connection:
            connection.execute(
                "UPDATE unit_economics_1c_source_values SET purchase_price=999 "
                "WHERE stock_item_id=?",
                (stock_item["id"],),
            )
            connection.commit()
        self.assertEqual(db.get_operation_items(operation_id)[0]["purchase_price"], 125.5)
        self.assertEqual(db.get_operations_with_items("rimili")[0]["items"][0]["article"], "A-1")
        self.assertEqual(db.get_operations_with_items("tris"), [])
        db.log_action_for_operation(1, "User", "delivery", "details", NOW, operation_id)
        self.assertEqual(db.get_activity_log(1)[0]["operation_id"], operation_id)

        self.assertEqual(db.upsert_sales_order_lines([], NOW), 0)
        db.upsert_sales_order_lines([sales_line()], NOW)
        db.upsert_sales_order_lines([sales_line(order_amount=2500.0)], NOW)
        self.assertTrue(db.sales_has_history("rimili", "WB"))
        self.assertFalse(db.sales_has_history("tris", "WB"))
        db.record_sales_sync("rimili", "WB", True, None, 1, 30, NOW)
        db.record_sales_sync("rimili", "WB", False, "failed", 0, 2, "2026-08-12T11:00:00+00:00")
        self.assertEqual(db.get_sales_sync_states("WB", "rimili")[0]["lookback_days"], 30)
        self.assertEqual(len(db.get_sales_sync_states("WB")), 1)
        daily = db.get_sales_daily("2026-08-01", "2026-09-01", "WB", "rimili")
        self.assertEqual(daily[0]["orders_amount"], 2500.0)
        self.assertEqual(daily[0]["fbs_count"], 2)
        self.assertEqual(len(db.get_sales_daily("2026-08-01", "2026-09-01", "WB")), 2)
        available = db.get_sales_available_range("WB", "rimili")
        self.assertEqual(available["date_from"], "2026-08-10")
        self.assertEqual(len(db.get_sales_export_rows("2026-08-01", "2026-09-01", "WB", "rimili")), 1)
        self.assertEqual(len(db.get_sales_export_rows("2026-08-01", "2026-09-01", "WB")), 1)

        db.upsert_mp_stock("rimili", "A-1", "WB", "fbs", 4, NOW)
        db.upsert_ff_stock("rimili", "A-1", "ФФ", 3, NOW, "WB")
        rows = stock_dashboard.get_inventory_rows("2026-08-01", "2026-07-01")
        row = next(item for item in rows if item["article"] == "A-1")
        self.assertEqual(row["marketplace_stock"], 4)
        self.assertEqual(row["fulfillment_stock"], 3)

    def test_open_ozon_fbs_orders_exclude_terminal_statuses(self) -> None:
        statuses = ("awaiting_packaging", "delivered", "cancelled", "not_accepted")
        db.upsert_sales_order_lines(
            [
                sales_line(
                    marketplace="OZON",
                    order_key=f"order-{status}",
                    line_key=f"line-{status}",
                    status=status,
                    sold_at=None,
                    quantity=2,
                    sold_quantity=0,
                    cancelled_quantity=0,
                )
                for status in statuses
            ],
            NOW,
        )

        self.assertEqual(db.get_open_fbs_order_totals("rimili", "OZON"), {"A-1": 2})
        self.assertEqual(
            db.get_fbs_order_totals_for_period(
                "rimili",
                "OZON",
                "2026-08-01",
                "2026-09-01",
                ("awaiting_packaging", "awaiting_deliver", "delivering"),
            ),
            {"A-1": 2},
        )
        self.assertEqual(
            db.get_fbs_order_totals_for_period(
                "rimili",
                "OZON",
                "2026-09-01",
                "2026-10-01",
                ("awaiting_packaging",),
            ),
            {},
        )

    def test_rnp_repository_queries_and_mutations(self) -> None:
        self.add_catalog()
        db.upsert_sales_order_lines(
            [
                sales_line(
                    returned_at="2026-08-12T08:00:00+00:00",
                    return_quantity=1,
                    return_amount=500,
                )
            ],
            NOW,
        )
        db.upsert_mp_stock("rimili", "A-1", "WB", "fbs", 8, NOW)
        page = db.get_rnp_catalog_page("rimili", "WB", "2026-08-01", "2026-09-01")
        self.assertEqual(page["total"], 2)
        filtered = db.get_rnp_catalog_page("rimili", "WB", "2026-08-01", "2026-09-01", "Первый", 1, 0)
        self.assertEqual(filtered["items"][0]["article"], "A-1")
        product_daily = db.get_rnp_product_daily("rimili", "WB", "2026-08-01", "2026-09-01", ["A-1"])
        self.assertTrue(all(row.get("gross_profit") is None for row in product_daily))
        self.assertEqual(db.get_rnp_product_daily("rimili", "WB", "x", "y", []), [])
        self.assertTrue(db.get_rnp_daily_totals("rimili", "WB", "2026-08-01", "2026-09-01"))
        self.assertEqual(db.get_rnp_stock_total("rimili", "WB"), 8)

        self.assertEqual(db.get_rnp_strategies("rimili", "WB", []), {})
        strategy = db.save_rnp_strategy(
            "rimili", "WB", "A-1", "growth", "2026-08-01", "2026-08-31", "User", NOW
        )
        self.assertEqual(strategy["strategy"], "growth")
        self.assertEqual(db.get_rnp_strategies("rimili", "WB", ["A-1"])["A-1"]["strategy"], "growth")
        self.assertEqual(db.get_rnp_action_logs("rimili", "WB", "x", "y", []), [])
        action = db.add_rnp_action_log("rimili", "WB", "A-1", "2026-08-12", "note", 1, "User", NOW)
        self.assertEqual(action["note"], "note")
        logs = db.get_rnp_action_logs("rimili", "WB", "2026-08-01", "2026-09-01", ["A-1"])
        self.assertEqual(logs[0]["article"], "A-1")
        self.assertTrue(db.rnp_article_exists("rimili", "WB", "A-1"))
        self.assertFalse(db.rnp_article_exists("rimili", "WB", "missing"))


if __name__ == "__main__":
    unittest.main()
