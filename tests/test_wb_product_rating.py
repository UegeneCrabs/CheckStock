"""WB rating snapshots remain atomic and report one row per storefront card."""

import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.application.wb_product_rating import WbProductRatingService
from app.dto.identity import User
from app.dto.wb_product_rating import WbRatingObservation
from app.economics.wb import rating_report
from app.infrastructure.database import Database
from app.infrastructure.orm import OrmBase
from app.infrastructure.wb_product_rating_repository import SqlAlchemyWbRatingWriter
from app.web.routers import rating_report as rating_router


class WbProductRatingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "rating.sqlite"
        self.database = Database(path=self.path)
        self.addCleanup(self.database.dispose)
        OrmBase.metadata.create_all(self.database.engine)
        writer = SqlAlchemyWbRatingWriter(lambda: self.path)
        self.service = WbProductRatingService(writer)
        database_patch = patch(
            "app.infrastructure.wb_product_rating_repository.database_for_path",
            return_value=self.database,
        )
        database_patch.start()
        self.addCleanup(database_patch.stop)
        connection_patch = patch.object(rating_report.db, "get_connection", self.database.connect)
        connection_patch.start()
        self.addCleanup(connection_patch.stop)

    @staticmethod
    def observation(
        nm_id: str,
        rating: float | None,
        observed_at: str,
        reviews_count: int | None = 0,
        store: str = "rimili",
    ) -> WbRatingObservation:
        return WbRatingObservation(store, nm_id, rating, reviews_count, datetime.fromisoformat(observed_at))

    def rows(self, table: str) -> list:
        with self.database.connect() as connection:
            return connection.execute(f"SELECT * FROM {table}").fetchall()

    def seed_catalog(self) -> None:
        with self.database.connect() as connection:
            for article, name in (
                ("101 / S", "Лампа S"),
                ("101 / M", "Лампа M"),
                ("102", "Чайник"),
            ):
                connection.execute(
                    "INSERT INTO stock_items (store_slug, marketplace, article, barcode, name) "
                    "VALUES ('rimili', 'WB', ?, ?, ?)",
                    (article, article, name),
                )
            connection.commit()

    def test_later_moscow_day_measurement_wins_and_repeat_is_idempotent(self) -> None:
        first = self.observation("101", 4.2, "2026-10-19T20:30:00+00:00", 0)
        later = self.observation("101", 4.4, "2026-10-19T21:30:00+00:00", 3)
        older = self.observation("101", 3.8, "2026-10-19T21:10:00+00:00", 2)
        self.assertEqual(self.service.record((first, later, older)), 3)
        self.assertEqual(self.service.record((later,)), 1)

        daily = sorted(self.rows("wb_product_rating_daily"), key=lambda row: row["day"])
        self.assertEqual([row["day"] for row in daily], ["2026-10-19", "2026-10-20"])
        self.assertEqual(daily[1]["rating"], 4.4)
        self.assertEqual(daily[1]["reviews_count"], 3)
        self.assertEqual(len(daily), 2)
        cache = self.rows("unit_economics_1c_product_reputation")
        self.assertEqual(cache[0]["rating"], 4.4)

    def test_invalid_rating_updates_cache_without_creating_daily_snapshot(self) -> None:
        self.service.record((self.observation("101", 0, "2026-10-20T09:00:00+00:00", 0),))
        self.service.record((self.observation("102", None, "2026-10-20T09:00:00+00:00", 5),))
        self.assertEqual(self.rows("wb_product_rating_daily"), [])
        cache = {row["nm_id"]: row for row in self.rows("unit_economics_1c_product_reputation")}
        self.assertEqual(cache["101"]["rating"], 0)
        self.assertEqual(cache["101"]["reviews_count"], 0)
        self.assertIsNone(cache["102"]["rating"])

    def test_daily_failure_rolls_back_latest_cache(self) -> None:
        def reject_daily(_connection, _cursor, statement, _parameters, _context, _executemany):
            if "INSERT INTO wb_product_rating_daily" in statement:
                raise RuntimeError("daily storage unavailable")

        event.listen(self.database.engine, "before_cursor_execute", reject_daily)
        try:
            with self.assertRaisesRegex(RuntimeError, "daily storage unavailable"):
                self.service.record((self.observation("101", 4.5, "2026-10-20T09:00:00+00:00"),))
        finally:
            event.remove(self.database.engine, "before_cursor_execute", reject_daily)
        self.assertEqual(self.rows("unit_economics_1c_product_reputation"), [])
        self.assertEqual(self.rows("wb_product_rating_daily"), [])

    def test_report_compares_last_snapshot_and_deduplicates_sizes(self) -> None:
        self.seed_catalog()
        self.service.record(
            (
                self.observation("101", 4.8, "2026-10-05T09:00:00+00:00"),
                self.observation("101", 4.6, "2026-10-10T09:00:00+00:00"),
                self.observation("101", 4.3, "2026-10-15T09:00:00+00:00"),
            )
        )
        result = rating_report.load(
            ("rimili",),
            date(2026, 10, 11),
            date(2026, 10, 20),
            rating_report.RatingFilters(),
            tolerance=0.3,
        )
        self.assertEqual(result.total, 2)
        card = next(row for row in result.rows if row.nm_id == "101")
        self.assertEqual((card.previous_rating, card.current_rating, card.change), (4.6, 4.3, -0.3))
        self.assertEqual(card.status, "healthy")
        self.assertEqual(result.summary.no_data, 1)
        self.assertEqual(result.options.articles, ["101 / M", "101 / S", "102"])

        filtered = rating_report.load(
            ("rimili",),
            date(2026, 10, 11),
            date(2026, 10, 20),
            rating_report.RatingFilters(article="101 / M"),
            tolerance=0.2,
        )
        self.assertEqual(filtered.total, 1)
        self.assertEqual(filtered.rows[0].status, "problem")
        self.assertEqual(filtered.options.articles, result.options.articles)

    def test_history_keeps_missing_days(self) -> None:
        self.service.record((self.observation("101", 4.5, "2026-10-20T09:00:00+00:00", 0),))
        history = rating_report.daily_history("rimili", "101", date(2026, 10, 19), date(2026, 10, 21))
        self.assertEqual([day.rating for day in history], [None, 4.5, None])
        self.assertEqual(history[1].reviews_count, 0)

    def test_history_api_checks_store_and_catalog_membership(self) -> None:
        self.seed_catalog()
        user = User(
            id=1,
            login="tester",
            full_name="Тестовый пользователь",
            created_at=datetime.now(UTC),
        )
        app = FastAPI()

        @app.middleware("http")
        async def identify(request, call_next):
            request.state.user = user
            return await call_next(request)

        app.include_router(rating_router.router)
        with TestClient(app) as client:
            allowed = client.get(
                "/api/unit-economics-1c/reports/rating/history",
                params={
                    "store": "rimili",
                    "nm_id": "101",
                    "date_from": "2026-10-19",
                    "date_to": "2026-10-20",
                },
            )
            unknown = client.get(
                "/api/unit-economics-1c/reports/rating/history",
                params={"store": "rimili", "nm_id": "999"},
            )
            denied = client.get(
                "/api/unit-economics-1c/reports/rating/history",
                params={"store": "unknown", "nm_id": "101"},
            )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(len(allowed.json()["days"]), 2)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
