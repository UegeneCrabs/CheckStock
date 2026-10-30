import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request
from sqlalchemy import event

from app.application.wb_product_rating import WbProductRatingService
from app.dto.identity import User
from app.dto.wb_product_rating import WbRatingObservation
from app.economics.wb import rating_report
from app.infrastructure.database import Database
from app.infrastructure.orm import (
    OrmBase,
    StockItemRecord,
    UnitEconomics1CProductCategoryRecord,
    UnitEconomics1CProductReputationRecord,
    UnitEconomics1CSourceValueRecord,
    WbProductRatingDailyRecord,
)
from app.infrastructure.wb_product_rating_repository import SqlAlchemyWbRatingWriter
from app.web.routers import rating_report as rating_router


class WbProductRatingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "rating.sqlite"
        self.database = Database(path=self.path)
        self.addCleanup(self.database.dispose)
        OrmBase.metadata.create_all(
            self.database.engine,
            tables=[
                StockItemRecord.__table__,
                UnitEconomics1CProductCategoryRecord.__table__,
                UnitEconomics1CProductReputationRecord.__table__,
                UnitEconomics1CSourceValueRecord.__table__,
                WbProductRatingDailyRecord.__table__,
            ],
        )
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

    # Тест-кейс: поздний замер московского дня побеждает, повтор не создаёт дубликат.
    def test_later_moscow_day_measurement_wins_and_repeat_is_idempotent(self) -> None:
        """Тест-кейс: поздний замер дня побеждает, повтор не создаёт дубликат.

        Ожидается два дневных снимка и последний рейтинг в текущем кэше.
        """

        # Given
        first = self.observation("101", 4.2, "2026-10-19T20:30:00+00:00", 0)
        later = self.observation("101", 4.4, "2026-10-19T21:30:00+00:00", 3)
        older = self.observation("101", 3.8, "2026-10-19T21:10:00+00:00", 2)

        # When
        recorded = self.service.record((first, later, older))
        repeated = self.service.record((later,))
        daily = sorted(self.rows("wb_product_rating_daily"), key=lambda row: row["day"])
        cache = self.rows("unit_economics_1c_product_reputation")

        # Then
        self.assertEqual(recorded, 3)
        self.assertEqual(repeated, 1)
        self.assertEqual([row["day"] for row in daily], ["2026-10-19", "2026-10-20"])
        self.assertEqual(daily[1]["rating"], 4.4)
        self.assertEqual(daily[1]["reviews_count"], 3)
        self.assertEqual(len(daily), 2)
        self.assertEqual(cache[0]["rating"], 4.4)

    # Тест-кейс: нулевой и отсутствующий рейтинг обновляют кэш без дневного снимка.
    def test_invalid_rating_updates_cache_without_creating_daily_snapshot(self) -> None:
        """Тест-кейс: нулевой и отсутствующий рейтинги записываются в кэш.

        Ожидается пустая дневная история при сохранённых значениях кэша.
        """

        # Given
        zero_rating = self.observation("101", 0, "2026-10-20T09:00:00+00:00", 0)
        missing_rating = self.observation("102", None, "2026-10-20T09:00:00+00:00", 5)

        # When
        self.service.record((zero_rating,))
        self.service.record((missing_rating,))
        daily = self.rows("wb_product_rating_daily")
        cache = {row["nm_id"]: row for row in self.rows("unit_economics_1c_product_reputation")}

        # Then
        self.assertEqual(daily, [])
        self.assertEqual(cache["101"]["rating"], 0)
        self.assertEqual(cache["101"]["reviews_count"], 0)
        self.assertIsNone(cache["102"]["rating"])

    # Тест-кейс: ошибка дневной записи откатывает обновление текущего кэша.
    def test_daily_failure_rolls_back_latest_cache(self) -> None:
        """Тест-кейс: запись дневного снимка завершается ошибкой.

        Ожидается откат записи в обе таблицы.
        """

        # Given
        def reject_daily(_connection, _cursor, statement, _parameters, _context, _executemany):
            if "INSERT INTO wb_product_rating_daily" in statement:
                raise RuntimeError("daily storage unavailable")

        event.listen(self.database.engine, "before_cursor_execute", reject_daily)

        # When
        try:
            with self.assertRaisesRegex(RuntimeError, "daily storage unavailable"):
                self.service.record((self.observation("101", 4.5, "2026-10-20T09:00:00+00:00"),))
        finally:
            event.remove(self.database.engine, "before_cursor_execute", reject_daily)

        # Then
        self.assertEqual(self.rows("unit_economics_1c_product_reputation"), [])
        self.assertEqual(self.rows("wb_product_rating_daily"), [])

    # Тест-кейс: отчёт сравнивает последние снимки и объединяет размеры карточки.
    def test_report_compares_last_snapshot_and_deduplicates_sizes(self) -> None:
        """Тест-кейс: отчёт сравнивает последние снимки карточки по периодам.

        Ожидается одна строка на карточку и смена статуса при меньшем пороге.
        """

        # Given
        self.seed_catalog()
        self.service.record(
            (
                self.observation("101", 4.8, "2026-10-05T09:00:00+00:00"),
                self.observation("101", 4.6, "2026-10-10T09:00:00+00:00"),
                self.observation("101", 4.3, "2026-10-15T09:00:00+00:00"),
            )
        )

        # When
        result = rating_report.load(
            ("rimili",),
            date(2026, 10, 11),
            date(2026, 10, 20),
            rating_report.RatingFilters(),
            tolerance=0.3,
        )
        filtered = rating_report.load(
            ("rimili",),
            date(2026, 10, 11),
            date(2026, 10, 20),
            rating_report.RatingFilters(article="101 / M"),
            tolerance=0.2,
        )
        card = next(row for row in result.rows if row.nm_id == "101")

        # Then
        self.assertEqual(result.total, 2)
        self.assertEqual((card.previous_rating, card.current_rating, card.change), (4.6, 4.3, -0.3))
        self.assertEqual(card.status, "healthy")
        self.assertEqual(result.summary.no_data, 1)
        self.assertEqual(result.options.articles, ["101 / M", "101 / S", "102"])
        self.assertEqual(filtered.total, 1)
        self.assertEqual(filtered.rows[0].status, "problem")
        self.assertEqual(filtered.options.articles, result.options.articles)

    # Тест-кейс: история сохраняет дни без замера и известный ноль отзывов.
    def test_history_keeps_missing_days(self) -> None:
        """Тест-кейс: история запрашивается за дни с одним замером.

        Ожидается None в пропусках и ноль отзывов в дне замера.
        """

        # Given
        self.service.record((self.observation("101", 4.5, "2026-10-20T09:00:00+00:00", 0),))

        # When
        history = rating_report.daily_history("rimili", "101", date(2026, 10, 19), date(2026, 10, 21))

        # Then
        self.assertEqual([day.rating for day in history], [None, 4.5, None])
        self.assertEqual(history[1].reviews_count, 0)

    # Тест-кейс: API отдаёт доступную историю и отклоняет чужой магазин и неизвестный товар.
    def test_history_api_checks_store_and_catalog_membership(self) -> None:
        """Тест-кейс: история запрашивается для доступной и недоступных карточек.

        Ожидается история для своей карточки, 404 для неизвестной и 403 для чужого магазина.
        """

        # Given
        self.seed_catalog()
        user = User(
            id=1,
            login="tester",
            full_name="Тестовый пользователь",
            created_at=datetime.now(UTC),
        )
        request = Request({"type": "http", "method": "GET", "path": "/"})
        request.state.user = user

        async def read_inline(function):
            return function()

        def call_history(store: str, nm_id: str) -> dict:
            coroutine = rating_router.history(
                request,
                store,
                nm_id,
                date_from=date(2026, 10, 19),
                date_to=date(2026, 10, 20),
            )
            try:
                coroutine.send(None)
            except StopIteration as completed:
                return completed.value
            finally:
                coroutine.close()
            self.fail("Обработчик истории должен завершиться без ожидания")

        # When
        with patch.object(rating_router, "run_in_threadpool", read_inline):
            allowed = call_history("rimili", "101")
            with self.assertRaises(HTTPException) as unknown:
                call_history("rimili", "999")
            with self.assertRaises(HTTPException) as denied:
                call_history("unknown", "101")

        # Then
        self.assertEqual(len(allowed["days"]), 2)
        self.assertEqual(unknown.exception.status_code, 404)
        self.assertEqual(denied.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
