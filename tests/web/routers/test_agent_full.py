import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.catalog import documentation
from app.agents.schema import build_action_schema
from app.container import ApplicationContainer
from app.dto.identity import User
from app.dto.wb_standard_costs import MarginRoi, ReportRow, Scenario, Snapshot
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
