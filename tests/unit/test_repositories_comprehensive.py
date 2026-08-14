import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db, decision_center, rnp_analytics
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

    def test_fulfillment_rates_costs_and_wb_metrics(self) -> None:
        db.seed_defaults()
        fulfillments = db.get_fulfillments()
        self.assertTrue(fulfillments)
        self.assertEqual(db.upsert_fulfillment_unit_rates([], NOW), 0)
        with self.assertRaises(ValueError):
            db.upsert_fulfillment_unit_rates([{"name": "Unknown"}], NOW)
        first = fulfillments[0]
        self.assertEqual(
            db.upsert_fulfillment_unit_rates(
                [{"name": first, "storage": 1.5, "accept": 2.5, "fulfillment": 3.5}], NOW
            ),
            1,
        )
        rates = db.get_fulfillment_unit_rates()
        self.assertEqual(next(row for row in rates if row["name"] == first)["storage_per_m3_day"], 1.5)

        self.assertEqual(
            db.replace_unit_costs(
                "rimili",
                [{"article": "A-1", "purchase_price": 100.0, "other_cost": 20.0}],
                "gid-1",
                NOW,
            ),
            1,
        )
        self.assertEqual(db.get_unit_costs("rimili")["A-1"]["purchase_price"], 100.0)
        self.assertEqual(db.upsert_wb_unit_references("rimili", [], NOW), 0)
        self.assertEqual(
            db.upsert_wb_unit_references(
                "rimili",
                [
                    {
                        "article": "A-1",
                        "nm_id": 1001,
                        "tech_size": "0",
                        "subject_id": 55,
                        "category": "Категория",
                        "length_cm": 10,
                        "width_cm": 20,
                        "height_cm": 30,
                        "volume_l": 6,
                        "weight_kg": 1.2,
                        "commission_fbs_rate": 15,
                    }
                ],
                NOW,
            ),
            1,
        )
        self.assertEqual(db.upsert_wb_unit_prices("rimili", [], NOW), 0)
        db.upsert_wb_unit_prices(
            "rimili",
            [
                {
                    "article": "A-1",
                    "nm_id": 1001,
                    "tech_size": "0",
                    "list_price": 1000,
                    "discounted_price": 900,
                    "club_discounted_price": 850,
                    "buyer_price": 800,
                    "spp_percent": 10,
                    "buyer_price_observed_at": NOW,
                }
            ],
            NOW,
        )
        self.assertEqual(db.get_wb_unit_metrics("rimili")["A-1"]["buyer_price"], 800)
        self.assertEqual(db.get_wb_price_last_sync("rimili"), NOW)

    def test_operations_sales_and_dashboard_queries(self) -> None:
        self.add_catalog()
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
        self.assertEqual(db.get_operation_items(operation_id)[0]["quantity"], 2)
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
        self.assertEqual(len(db.get_sales_daily("2026-08-01", "2026-09-01", "WB")), 2)
        available = db.get_sales_available_range("WB", "rimili")
        self.assertEqual(available["date_from"], "2026-08-10")
        self.assertEqual(len(db.get_sales_export_rows("2026-08-01", "2026-09-01", "WB", "rimili")), 1)
        self.assertEqual(len(db.get_sales_export_rows("2026-08-01", "2026-09-01", "WB")), 1)

        db.upsert_mp_stock("rimili", "A-1", "WB", "fbs", 4, NOW)
        db.upsert_ff_stock("rimili", "A-1", "ФФ", 3, NOW, "WB")
        db.replace_unit_costs(
            "rimili", [{"article": "A-1", "purchase_price": 100, "other_cost": 10}], "gid", NOW
        )
        rows = stock_dashboard.get_inventory_rows("2026-08-01", "2026-07-01")
        row = next(item for item in rows if item["article"] == "A-1")
        self.assertEqual(row["marketplace_stock"], 4)
        self.assertEqual(row["fulfillment_stock"], 3)

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
        db.replace_unit_costs(
            "rimili", [{"article": "A-1", "purchase_price": 100, "other_cost": 10}], "gid", NOW
        )
        page = db.get_rnp_catalog_page("rimili", "WB", "2026-08-01", "2026-09-01")
        self.assertEqual(page["total"], 2)
        filtered = db.get_rnp_catalog_page("rimili", "WB", "2026-08-01", "2026-09-01", "Первый", 1, 0)
        self.assertEqual(filtered["items"][0]["article"], "A-1")
        product_daily = db.get_rnp_product_daily("rimili", "WB", "2026-08-01", "2026-09-01", ["A-1"])
        sale_day = next(row for row in product_daily if row.get("gross_profit") is not None)
        self.assertEqual(sale_day["gross_profit"], 1580)
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
