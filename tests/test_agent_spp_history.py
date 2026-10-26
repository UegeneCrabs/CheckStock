import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.catalog import documentation
from app.agents.schema import build_action_schema
from app.container import ApplicationContainer
from app.dto.identity import User
from app.infrastructure.database import Database
from app.web.routers import agent_analytics, agent_full


class SppHistoryApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(path=Path(directory.name) / "history.db")
        self.addCleanup(self.database.engine.dispose)
        with self.database.engine.begin() as connection:
            for table, date_column in (
                ("wb_hourly_prices", "observed_hour"),
                ("wb_daily_prices", "observed_day"),
            ):
                connection.exec_driver_sql(
                    f"CREATE TABLE {table} (store_slug TEXT, article TEXT, {date_column} TEXT, "
                    "observed_at TEXT, customer_price_with_spp REAL, spp_percent REAL, customer_price_with_wallet REAL)"
                )
            for store, article, stamp in (
                ("rimili", "A", "2026-10-19T20:59:59+00:00"),
                ("rimili", "A", "2026-10-19T21:00:00+00:00"),
                ("rimili", "B", "2026-10-20T20:59:59+00:00"),
                ("rimili", "A", "2026-10-20T21:00:00+00:00"),
                ("tris", "A", "2026-10-20T10:00:00+00:00"),
            ):
                connection.exec_driver_sql(
                    "INSERT INTO wb_hourly_prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (store, article, stamp, stamp, 900.0, None, None),
                )
            connection.exec_driver_sql(
                "INSERT INTO wb_daily_prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("rimili", "A", "2026-10-20", "2026-10-20T20:15:00+00:00", 875.25, 12.5, 0.0),
            )
        self.app = FastAPI()
        self.app.state.container = ApplicationContainer(
            database_path=lambda: Path(directory.name) / "history.db"
        )
        self.app.include_router(agent_full.router, prefix="/api/agent/v1")
        self.user = User(id=1, full_name="Test", login="test", created_at=datetime.now(UTC))
        self.app.dependency_overrides[agent_analytics.employee] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def get(self, method="spp-hourly", **params):
        return self.client.get(
            "/api/agent/v1/" + method,
            params={"store": "rimili", "date_from": "2026-10-20", "date_to": "2026-10-20", **params},
        )

    def test_moscow_boundaries_store_isolation_pagination_and_nulls(self):
        response = self.get(limit=1, order="asc")
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.assertEqual(page["total_rows"], 2)
        self.assertEqual(page["next_offset"], 1)
        self.assertEqual(page["rows"][0]["observed_at"], "2026-10-19T21:00:00+00:00")
        self.assertIsNone(page["rows"][0]["spp_percent"])
        self.assertIsNone(page["rows"][0]["customer_price_with_wallet"])
        self.assertEqual(page["totals"], {})
        second = self.get(limit=1, offset=1, order="asc").json()
        self.assertEqual(second["rows"][0]["article"], "B")
        self.assertIsNone(second["next_offset"])
        self.assertEqual(self.get(article="A").json()["total_rows"], 1)

    def test_daily_returns_saved_averages_and_last_observation(self):
        response = self.get("spp-daily")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["rows"],
            [
                {
                    "article": "A",
                    "day": "2026-10-20",
                    "observed_at": "2026-10-20T20:15:00+00:00",
                    "average_customer_price_with_spp": 875.25,
                    "average_spp_percent": 12.5,
                    "average_customer_price_with_wallet": 0.0,
                }
            ],
        )
        missing = self.get("spp-daily", date_from="2026-10-18", date_to="2026-10-18").json()
        self.assertEqual(missing["rows"], [])
        self.assertEqual(missing["context"]["coverage"], "unknown")

    def test_validation_auth_and_store_guard(self):
        for params in (
            {"marketplace": "YANDEX MARKET"},
            {"marketplace": "OZON"},
            {"date_to": "2026-10-19"},
            {"date_to": "2099-01-01"},
            {"date_from": "2026-01-01"},
            {"scheme": "fbo"},
        ):
            with self.subTest(params=params):
                self.assertEqual(self.get(**params).status_code, 422)
        self.assertEqual(self.get(store="not-a-store").status_code, 403)
        self.assertEqual(
            self.client.get("/api/agent/v1/spp-hourly", params={"store": "rimili"}).status_code, 422
        )
        self.app.dependency_overrides.clear()
        self.assertEqual(self.get().status_code, 401)

    def test_schema_and_catalog_match_the_new_fields(self):
        schema = build_action_schema(agent_full.router.routes)
        for method in ("spp-hourly", "spp-daily"):
            operation = schema["paths"]["/" + method]["get"]
            self.assertLessEqual(len(operation["description"]), 300)
            parameters = {p["name"]: p for p in operation["parameters"]}
            self.assertTrue(parameters["date_from"]["required"])
            self.assertTrue(parameters["date_to"]["required"])
            doc = documentation(method)
            self.assertNotIn("marketplace_guides", doc)
            keys = {f["name"] for f in doc["fields"]}
            self.assertEqual(keys, set(self.get(method).json()["rows"][0]))


if __name__ == "__main__":
    unittest.main()
