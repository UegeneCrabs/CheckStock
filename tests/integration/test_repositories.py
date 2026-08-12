import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db
from app.repositories import core, stock_dashboard


class RepositoryIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_patch = mock.patch.object(
            core,
            "DB_PATH",
            Path(self.temporary_directory.name) / "integration.db",
        )
        self.database_patch.start()
        db.init_db()

    def tearDown(self) -> None:
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def test_catalog_and_fulfillment_stock_are_joined(self) -> None:
        timestamp = "2026-08-11T12:00:00+00:00"
        report = db.replace_catalog(
            "rimili",
            "WB",
            [{"article": "A-1", "barcode": "460000000001", "name": "Товар"}],
            timestamp,
        )
        db.upsert_ff_stock("rimili", "A-1", "ФулСервис Подольск", 7, timestamp, "WB")

        rows = db.get_stock_items("rimili", "WB", ("fbs", "fbo"))
        dashboard_rows = stock_dashboard.get_inventory_rows(timestamp, timestamp)

        self.assertEqual(report["added"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["article"], "A-1")
        self.assertEqual(rows[0]["ff_available"], 7)
        dashboard_item = next(row for row in dashboard_rows if row["article"] == "A-1")
        self.assertEqual(dashboard_item["fulfillment_stock"], 7)

    def test_user_store_access_and_session_persist(self) -> None:
        user_id = db.create_user(
            full_name="Тестовый пользователь",
            google_email="user@example.com",
            login="tester",
            password_hash="hash",
            role="user",
            created_at="2026-08-11T12:00:00+00:00",
            store_slugs=["rimili", "trusthome"],
        )
        db.create_session(
            "session-token",
            user_id,
            "2026-08-11T12:00:00+00:00",
            "2026-08-12T12:00:00+00:00",
        )

        user = db.get_user_by_login("tester")
        session = db.get_session("session-token")

        self.assertEqual(user["store_slugs"], ["rimili", "trusthome"])
        self.assertEqual(session["user_id"], user_id)


if __name__ == "__main__":
    unittest.main()
