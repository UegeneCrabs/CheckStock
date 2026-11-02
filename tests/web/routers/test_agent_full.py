import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.catalog import documentation
from app.agents.schema import build_action_schema
from app.container import ApplicationContainer
from app.dto.identity import User
from app.dto.wb_standard_costs import MarginRoi, ReportRow, Scenario, Snapshot
from app.infrastructure.async_database import AsyncDatabase
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

    # Тест-кейс: Почасовые данные ограничены московским днём и магазином; пагинация и null сохраняются.
    def test_moscow_boundaries_store_isolation_pagination_and_nulls(self) -> None:
        """Тест-кейс: Почасовые данные ограничены московским днём и магазином; пагинация и null сохраняются.

        Ожидается: Почасовые данные ограничены московским днём и магазином; пагинация и null сохраняются.
        """

        # Given
        page_size = 1

        # When
        response = self.get(limit=page_size, order="asc")
        second = self.get(limit=1, offset=1, order="asc").json()
        filtered = self.get(article="A").json()

        # Then
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()
        self.assertEqual(page["total_rows"], 2)
        self.assertEqual(page["next_offset"], 1)
        self.assertEqual(page["rows"][0]["observed_at"], "2026-10-19T21:00:00+00:00")
        self.assertIsNone(page["rows"][0]["spp_percent"])
        self.assertIsNone(page["rows"][0]["customer_price_with_wallet"])
        self.assertEqual(page["totals"], {})
        self.assertEqual(second["rows"][0]["article"], "B")
        self.assertIsNone(second["next_offset"])
        self.assertEqual(filtered["total_rows"], 1)

    # Тест-кейс: Дневной API возвращает сохранённые средние и последнее наблюдение; пустой день не считается полным.
    def test_daily_returns_saved_averages_and_last_observation(self) -> None:
        """Тест-кейс: Дневной API возвращает сохранённые средние и последнее наблюдение; пустой день не считается полным.

        Ожидается: Дневной API возвращает сохранённые средние и последнее наблюдение; пустой день не считается полным.
        """

        # Given
        method = "spp-daily"

        # When
        response = self.get(method)
        missing = self.get("spp-daily", date_from="2026-10-18", date_to="2026-10-18").json()

        # Then
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
        self.assertEqual(missing["rows"], [])
        self.assertEqual(missing["context"]["coverage"], "unknown")

    # Тест-кейс: Некорректные параметры СПП отклоняются с кодом 422.
    def test_invalid_spp_parameters(self) -> None:
        """Тест-кейс: Некорректные параметры СПП отклоняются с кодом 422.

        Ожидается: Некорректные параметры СПП отклоняются с кодом 422.
        """

        # Given
        for params in (
            {"marketplace": "YANDEX MARKET"},
            {"marketplace": "OZON"},
            {"date_to": "2026-10-19"},
            {"date_to": "2099-01-01"},
            {"date_from": "2026-01-01"},
            {"scheme": "fbo"},
        ):
            with self.subTest(params=params):
                # When
                response = self.get(**params)

                # Then
                self.assertEqual(response.status_code, 422)

    # Тест-кейс: Неизвестный магазин и отсутствие периода отклоняются.
    def test_unknown_store_and_missing_period(self) -> None:
        """Тест-кейс: Неизвестный магазин и отсутствие периода отклоняются.

        Ожидается 403 для неизвестного магазина и 422 без периода.
        """

        # Given
        store = "not-a-store"

        # When
        unknown_store = self.get(store=store)
        missing_period = self.client.get("/api/agent/v1/spp-hourly", params={"store": "rimili"})

        # Then
        self.assertEqual(unknown_store.status_code, 403)
        self.assertEqual(missing_period.status_code, 422)

    # Тест-кейс: Запрос СПП без авторизации отклоняется.
    def test_authentication_is_required(self) -> None:
        """Тест-кейс: Запрос СПП без авторизации отклоняется.

        Ожидается код 401 без чтения отчёта.
        """

        # Given
        self.app.dependency_overrides.clear()

        # When
        response = self.get()

        # Then
        self.assertEqual(response.status_code, 401)

    # Тест-кейс: Схема требует период, а поля каталога совпадают с ответами обоих методов СПП.
    def test_schema_and_catalog_match_the_new_fields(self) -> None:
        """Тест-кейс: Схема требует период, а поля каталога совпадают с ответами обоих методов СПП.

        Ожидается: Схема требует период, а поля каталога совпадают с ответами обоих методов СПП.
        """

        # Given
        routes = agent_full.router.routes

        # When
        schema = build_action_schema(routes)
        for method in ("spp-hourly", "spp-daily"):
            doc = documentation(method)
            response = self.get(method)

            # Then
            operation = schema["paths"]["/" + method]["get"]
            self.assertLessEqual(len(operation["description"]), 300)
            parameters = {p["name"]: p for p in operation["parameters"]}
            self.assertTrue(parameters["date_from"]["required"])
            self.assertTrue(parameters["date_to"]["required"])
            self.assertNotIn("marketplace_guides", doc)
            keys = {f["name"] for f in doc["fields"]}
            self.assertEqual(keys, set(response.json()["rows"][0]))


class StandardCostsTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(agent_full.router, prefix="/api/agent/v1")
        self.app = app
        app.dependency_overrides[agent_analytics.employee] = lambda: User(
            id=1, login="test", full_name="Test", created_at=datetime.now(UTC)
        )
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def get(self, **params):
        return self.client.get("/api/agent/v1/standard-costs", params={"store": "rimili", **params})

    # Тест-кейс: API возвращает компактные сценарии прибыли, исходный период и все страницы без потери null.
    def test_scenarios_details_pagination_and_source_period(self) -> None:
        """Тест-кейс: API возвращает компактные сценарии прибыли, исходный период и все страницы без потери null.

        Ожидается: API возвращает компактные сценарии прибыли, исходный период и все страницы без потери null.
        """

        # Given
        snapshot = Snapshot(
            version=6,
            date_from="2026-10-12",
            date_to="2026-10-18",
            updated_at="2026-10-19",
            rows=(
                ReportRow(
                    article="A",
                    scheme="FBW",
                    profit=Scenario(margin=None, missing=("commission",)),
                    current_profit=Scenario(margin=10, roi=20),
                    delta=MarginRoi(margin=None),
                ),
                ReportRow(
                    article="A",
                    scheme="FBS",
                    profit=Scenario(margin=15, roi=30),
                    current_profit=Scenario(margin=10, roi=20),
                    delta=MarginRoi(margin=5, roi=10),
                ),
            ),
        )
        with (
            patch("app.repositories.wb_standard_costs.load", return_value=snapshot) as load,
            patch("app.economics.wb.standard_profit.enrich", side_effect=lambda store, value: value),
        ):
            # When
            result = self.get(limit=1).json()
            second = self.get(limit=1, offset=1).json()
            absent = self.get(article="not-found").json()

            # Then
            self.assertEqual(result["total_rows"], 2)
            self.assertEqual(result["next_offset"], 1)
            self.assertEqual(result["totals"], {})
            self.assertEqual(result["context"]["snapshot_period"]["date_to"], "2026-10-18")
            self.assertIsNone(result["rows"][0]["profit"]["margin"])
            self.assertEqual(
                set(result["rows"][0]),
                {
                    "article",
                    "name",
                    "scheme",
                    "profit",
                    "current_profit",
                    "delta",
                    "calculator_date",
                    "warnings",
                },
            )
            self.assertEqual(
                set(result["rows"][0]["current_profit"]), {"margin", "roi", "complete", "missing"}
            )
            self.assertEqual(set(result["rows"][0]["profit"]), {"margin", "roi", "complete", "missing"})
            self.assertNotIn("retail", result["rows"][0]["profit"])
            self.assertEqual(result["rows"][0]["profit"]["missing"], ["commission"])
            self.assertEqual(second["rows"][0]["delta"]["roi"], 10)
            self.assertIsNone(second["next_offset"])
            self.assertEqual(absent["total_rows"], 0)
            load.assert_called_with("rimili")

    # Тест-кейс: Отсутствующий отчёт даёт пустой ответ с пометкой недоступности.
    def test_missing_source(self) -> None:
        """Тест-кейс: Отсутствующий отчёт даёт пустой ответ с пометкой недоступности.

        Ожидается: Отсутствующий отчёт даёт пустой ответ с пометкой недоступности.
        """

        # Given
        with patch("app.repositories.wb_standard_costs.load", return_value=None):
            # When
            r = self.get()
            # Then
            self.assertEqual(r.status_code, 200)
            self.assertFalse(r.json()["context"]["available"])
            self.assertEqual(r.json()["rows"], [])

    # Тест-кейс: Ошибка чтения отчёта не скрывается пустым результатом.
    def test_failed_source(self) -> None:
        """Тест-кейс: Ошибка чтения отчёта не скрывается пустым результатом.

        Ожидается код 503 при сбое источника.
        """

        # Given
        with patch("app.repositories.wb_standard_costs.load", side_effect=RuntimeError("unavailable")):
            # When
            response = self.get()

            # Then
            self.assertEqual(response.status_code, 503)

    # Тест-кейс: API расходов отклоняет неподдерживаемую площадку, период и схему с кодом 422.
    def test_invalid_standard_costs_parameters(self) -> None:
        """Тест-кейс: API расходов отклоняет неподдерживаемую площадку, период и схему с кодом 422.

        Ожидается: API расходов отклоняет неподдерживаемую площадку, период и схему с кодом 422.
        """

        # Given
        for params in (
            {"marketplace": "YANDEX MARKET"},
            {"date_from": "2026-10-12", "date_to": "2026-10-18"},
            {"scheme": "fbs"},
        ):
            # When
            response = self.get(**params)

            # Then
            self.assertEqual(response.status_code, 422)

    # Тест-кейс: Каталог и схема описывают компактный ответ расходов WB.
    def test_schema_and_catalog(self) -> None:
        """Тест-кейс: Каталог и схема описывают компактный ответ расходов WB.

        Ожидается метод StandardCosts без периода и без удалённых расходных полей.
        """

        # Given
        routes = self.app.routes

        # When
        schema = build_action_schema(routes)
        doc = documentation("standard-costs")
        operation = schema["paths"]["/api/agent/v1/standard-costs"]["get"]
        # Then
        self.assertEqual(operation["operationId"], "getAnalyticsStandardCosts")
        self.assertNotIn("date_from", [p["name"] for p in operation["parameters"]])
        self.assertEqual(doc["title"], "Расходы WB без ИУ")
        self.assertTrue(any("delta.roi" in f["name"] for f in doc["fields"]))
        self.assertFalse(
            any(
                "calculator_evidence" in f["name"]
                or "profit.commission" in f["name"]
                or "profit.purchase" in f["name"]
                or "profit.total_cost" in f["name"]
                for f in doc["fields"]
            )
        )

    # Тест-кейс: Запрос расходов без авторизации отклоняется.
    def test_authentication_is_required(self) -> None:
        """Тест-кейс: Запрос расходов без авторизации отклоняется.

        Ожидается код 401.
        """

        # Given
        self.app.dependency_overrides.clear()
        # When
        response = self.get()

        # Then
        self.assertEqual(response.status_code, 401)

    # Тест-кейс: Поздние причины неполноты меняют только пометку; числа сохраняются в расчёте сайта и API.
    def test_late_missing_reasons_preserve_values_in_report_and_api(self) -> None:
        """Тест-кейс: Поздние причины неполноты меняют только пометку; числа сохраняются в расчёте сайта и API.

        Ожидается: Поздние причины неполноты меняют только пометку; числа сохраняются в расчёте сайта и API.
        """

        # Given
        from app.dto.wb_standard_costs import CalculatorProduct, StorageCalculation
        from app.economics.wb import standard_profit

        row = ReportRow(article="TEST-COMPLETE", scheme="FBW")
        scenario = Scenario(margin=200, roi=40, complete=True)
        for reasons in ([], ["Не хватает данных о хранении"]):
            with (
                self.subTest(reasons=reasons),
                patch.object(standard_profit, "calculate_scenario", return_value=scenario),
                patch.object(
                    standard_profit, "storage_per_unit", return_value=(StorageCalculation(value=10), reasons)
                ),
                patch.object(standard_profit, "logistics_with_returns") as logistics,
            ):
                logistics.return_value.total = 20
                logistics.return_value.missing = ()

                # When
                comparison = standard_profit.compare(row, CalculatorProduct())
                report_row = row.model_copy(
                    update={
                        "profit": comparison.standard,
                        "current_profit": comparison.current,
                        "delta": comparison.delta,
                    }
                )
                snapshot = Snapshot(date_from="2026-10-12", date_to="2026-10-18", rows=(report_row,))
                with (
                    patch("app.repositories.wb_standard_costs.load", return_value=snapshot),
                    patch.object(standard_profit, "enrich", side_effect=lambda store, value: value),
                ):
                    response = self.get()

                # Then
                self.assertEqual(comparison.standard.complete, not reasons)
                self.assertEqual(comparison.standard.missing, tuple(reasons))
                self.assertEqual(comparison.standard.margin, 200)
                self.assertEqual(comparison.standard.roi, 40)
                self.assertEqual(comparison.delta.margin, 0)
                self.assertEqual(comparison.delta.roi, 0)
                self.assertEqual(response.status_code, 200)
                returned = response.json()["rows"][0]
                self.assertEqual(
                    returned["profit"],
                    {
                        "margin": 200,
                        "roi": 40,
                        "complete": not reasons,
                        "missing": reasons,
                    },
                )
                self.assertTrue(returned["current_profit"]["complete"])


class RatingApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(path=Path(directory.name) / "rating.db")
        self.addCleanup(self.database.engine.dispose)
        with self.database.engine.begin() as connection:
            for statement in (
                "CREATE TABLE stock_items (id INTEGER PRIMARY KEY, store_slug TEXT, marketplace TEXT, article TEXT, barcode TEXT, name TEXT, is_service INTEGER)",
                "CREATE TABLE unit_economics_1c_product_categories (stock_item_id INTEGER, brand TEXT, category TEXT)",
                "CREATE TABLE unit_economics_1c_source_values (stock_item_id INTEGER, manager TEXT)",
                "CREATE TABLE wb_product_rating_daily (store_slug TEXT, nm_id TEXT, day TEXT, rating REAL, reviews_count INTEGER, observed_at TEXT)",
                "CREATE TABLE unit_economics_1c_product_reputation (store_slug TEXT, nm_id TEXT, rating REAL, reviews_count INTEGER, synced_at TEXT)",
            ):
                connection.exec_driver_sql(statement)
            for identity, article, nm_id in (
                (1, "100 / M", "100"),
                (2, "100 / L", "100"),
                (3, "200", "200"),
                (4, "300", "300"),
            ):
                connection.exec_driver_sql(
                    "INSERT INTO stock_items VALUES (?, 'rimili', 'WB', ?, '', ?, 0)",
                    (identity, article, "Card " + nm_id),
                )
                connection.exec_driver_sql(
                    "INSERT INTO unit_economics_1c_product_categories VALUES (?, 'Brand', 'Category')",
                    (identity,),
                )
                connection.exec_driver_sql(
                    "INSERT INTO unit_economics_1c_source_values VALUES (?, 'Manager')", (identity,)
                )
            for store, nm_id, day, rating, reviews in (
                ("rimili", "100", "2026-10-12", 4.8, 10),
                ("rimili", "100", "2026-10-19", 4.5, 0),
                ("rimili", "200", "2026-10-13", 4.6, 2),
                ("rimili", "200", "2026-10-20", 4.8, 5),
                ("rimili", "300", "2026-10-19", 4.7, 1),
                ("tris", "100", "2026-10-20", 1.0, 999),
            ):
                connection.exec_driver_sql(
                    "INSERT INTO wb_product_rating_daily VALUES (?, ?, ?, ?, ?, ?)",
                    (store, nm_id, day, rating, reviews, day + "T12:00:00+00:00"),
                )
            connection.exec_driver_sql(
                "INSERT INTO unit_economics_1c_product_reputation VALUES ('rimili', '100', 4.9, 30, '2026-10-21T12:00:00+00:00')"
            )
        database = AsyncDatabase(url="", path=Path(directory.name) / "rating.db")
        self.addCleanup(lambda: asyncio.run(database.close()))
        self.app = FastAPI()
        self.app.state.async_resources = SimpleNamespace(database=database)
        self.app.include_router(agent_full.router, prefix="/api/agent/v1")
        self.user = User(id=1, full_name="Test", login="test", created_at=datetime.now(UTC))
        self.app.dependency_overrides[agent_analytics.employee] = lambda: self.user
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    def get(self, method="rating-comparison", **params):
        return self.client.get(
            "/api/agent/v1/" + method,
            params={"store": "rimili", "date_from": "2026-10-14", "date_to": "2026-10-20", **params},
        )

    # Тест-кейс: Сравнение использует реальные даты замеров, отдельный свежий кэш и итоги до пагинации.
    def test_comparison_periods_cache_deduplication_and_totals(self):
        """Тест-кейс: Сравнение рейтинга склейки WB за два периода.

        Ожидается: размеры не дублируются, чужой магазин исключён, кэш не заменяет историю.
        """
        # Given
        limit = 1
        # When
        response = self.get(limit=limit)
        page = self.get(limit=1, offset=1).json()
        # Then
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["total_rows"], 3)
        self.assertEqual(result["totals"], {"checked": 3, "problem": 1, "healthy": 1, "no_data": 1})
        self.assertEqual(result["context"]["previous_from"], "2026-10-07")
        self.assertEqual(result["context"]["previous_to"], "2026-10-13")
        row = result["rows"][0]
        self.assertEqual(row["nm_id"], "100")
        self.assertEqual(row["current_observed_day"], "2026-10-19")
        self.assertEqual(row["previous_observed_day"], "2026-10-12")
        self.assertEqual((row["current_rating"], row["previous_rating"], row["change"]), (4.5, 4.8, -0.3))
        self.assertEqual((row["latest_rating"], row["reviews_count"]), (4.9, 30))
        self.assertEqual(page["totals"], result["totals"])
        self.assertEqual(result["context"]["rating_label"], "Рейтинг склейки WB")
        self.assertFalse(result["context"]["individual_rating_available"])

    # Тест-кейс: Фильтры применяются до подсчётов; границы падения и порога различаются.
    def test_filters_and_tolerance_boundaries(self):
        """Тест-кейс: Фильтры рейтинга и допуска падения.

        Ожидается: rating_below строгий, drop_at_least включительный, падение ровно tolerance допустимо.
        """
        # Given
        params = {"brand": "brand", "manager": "manager", "subject": "category", "search": "Card"}
        # When
        selected = self.get(rating_below=4.6, drop_at_least=0.3, **params).json()
        strict = self.get(rating_below=4.5).json()
        allowed = self.get(tolerance=0.3, article="100").json()
        # Then
        self.assertEqual(selected["totals"]["checked"], 1)
        self.assertEqual(selected["rows"][0]["nm_id"], "100")
        self.assertEqual(strict["total_rows"], 0)
        self.assertEqual(allowed["rows"][0]["status"], "healthy")
        self.assertEqual(self.get(status="no_data").json()["rows"][0]["nm_id"], "300")

    # Тест-кейс: История выдаёт каждый день с null при пропуске и сохраняет ноль отзывов.
    def test_history_gaps_zero_reviews_and_exact_article(self):
        """Тест-кейс: Дневная история рейтинга склейки WB.

        Ожидается: семь дней, шесть пропусков, ноль отзывов и точное время наблюдения.
        """
        # Given
        article = "100 / M"
        # When
        response = self.get("rating-history", article=article, limit=3)
        next_page = self.get("rating-history", article=article, offset=3).json()
        # Then
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["total_rows"], 7)
        self.assertEqual(result["rows"][0]["day"], "2026-10-14")
        self.assertIsNone(result["rows"][0]["rating"])
        observation = next(item for item in next_page["rows"] if item["rating"] is not None)
        self.assertEqual(observation["reviews_count"], 0)
        self.assertEqual(observation["observed_at"], "2026-10-19T12:00:00Z")
        self.assertEqual(result["totals"], {"expected_days": 7, "observed_days": 1, "missing_days": 6})
        self.assertFalse(result["context"]["coverage_complete"])
        self.assertEqual(self.get("rating-history", article="404").status_code, 404)

    # Тест-кейс: API отклоняет неразрешённые параметры, площадки и интервалы.
    def test_invalid_parameters_and_platforms(self):
        """Тест-кейс: Валидация новых методов рейтинга.

        Ожидается: ошибочные параметры дают 422, недоступный магазин — 403.
        """
        # Given
        invalid = (
            {"marketplace": "OZON"},
            {"marketplace": "YANDEX MARKET"},
            {"tolerance": "nan"},
            {"status": "other"},
            {"rating_below": 6},
            {"drop_at_least": -1},
            {"date_to": "2026-10-01"},
            {"date_from": "2026-01-01"},
            {"date_to": "2999-01-01"},
            {"date_from": "0001-01-01", "date_to": "0001-01-01"},
        )
        # When
        responses = [self.get(**params) for params in invalid]
        missing_article = self.get("rating-history")
        extra_filter = self.get("rating-history", article="100", manager="Manager")
        # Then
        self.assertTrue(
            all(response.status_code == 422 for response in responses),
            [(r.status_code, r.text) for r in responses],
        )
        self.assertEqual(missing_article.status_code, 422)
        self.assertEqual(extra_filter.status_code, 422)
        self.assertEqual(self.get(store="unknown").status_code, 403)
        self.app.dependency_overrides.clear()
        self.assertEqual(self.get().status_code, 401)

    # Тест-кейс: Схема и каталог объявляют новые поля и поддержку только WB.
    def test_schema_and_catalog(self):
        """Тест-кейс: Новые методы доступны в схеме API и каталоге.

        Ожидается: обязательные даты и артикул истории; единицы — пункты, доступность только WB.
        """
        # Given
        paths = build_action_schema(self.app.routes)["paths"]
        # When
        history = paths["/api/agent/v1/rating-history"]["get"]
        doc = documentation("rating-comparison")
        # Then
        self.assertEqual(
            history["x-marketplace-support"], {"WB": True, "YANDEX MARKET": False, "OZON": False}
        )
        parameters = {value["name"]: value for value in history["parameters"]}
        self.assertTrue(all(parameters[key]["required"] for key in ("date_from", "date_to", "article")))
        self.assertEqual(parameters["order"]["schema"]["default"], "asc")
        self.assertIn("totals.checked", {field["path"] for field in doc["fields"]})
        self.assertIn("rows[].current_observed_day", {field["path"] for field in doc["fields"]})
