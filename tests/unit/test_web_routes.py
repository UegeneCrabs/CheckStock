import json
import logging
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import db, decision_center, rnp_analytics
from app.dto.decision import DecisionAction, DecisionStatus
from app.dto.marketplace import Marketplace
from app.dto.rnp import RnpAction, RnpStrategy
from app.dto.system import ReadinessStatus
from app.dto.unit_economics_1c import UnitEconomics1CProductSettings
from app.main import create_app
from app.repositories import core
from app.stores import STORES
from app.wb import funnel_orders as wb_funnel_orders
from app.web import middleware
from app.web.routers import decision_center as decision_routes
from app.web.routers import rnp as rnp_routes
from app.web.routers import sales_overview, unit_economics

NOW = "2026-08-12T10:00:00+00:00"


class WebRouteUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_patch = mock.patch.object(
            core,
            "DB_PATH",
            Path(self.temporary_directory.name) / "web-unit.db",
        )
        self.database_patch.start()
        db.init_db()
        decision_center.init_schema()
        rnp_analytics.init_schema()
        db.seed_defaults()
        self.user = {
            "id": 1,
            "full_name": "Unit Admin",
            "login": "unit-admin",
            "role": "superadmin",
            "is_active": 1,
            "can_edit_stock": 1,
            "can_manage_users": 1,
            "store_slugs": list(STORES),
        }
        self.app = create_app()
        self.authentication_patch = mock.patch.object(
            self.app.state.container.identity,
            "user_for_token",
            return_value=self.user,
        )
        self.authentication_patch.start()
        logging.disable(logging.CRITICAL)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.client.cookies.set(middleware.auth.SESSION_COOKIE, "test-session")
        db.upsert_ff_stock("rimili", "949558341", "ФулСервис Подольск", 5, NOW, "WB")
        db.upsert_mp_stock("rimili", "949558341", "WB", "fbs", 3, NOW)
        db.replace_mp_warehouse_stock(
            "rimili",
            "WB",
            "fbo",
            [("949558341", "Коледино", "Москва", 2, NOW)],
        )
        self.operation_id = db.record_operation(
            "rimili",
            "delivery",
            "manual",
            [
                {
                    "article": "949558341",
                    "barcode": "2050292584830",
                    "name": "Ретро гирлянда",
                    "quantity": 5,
                }
            ],
            1,
            "Unit Admin",
            NOW,
        )

    def tearDown(self) -> None:
        self.client.close()
        logging.disable(logging.NOTSET)
        self.authentication_patch.stop()
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    def test_html_pages_and_downloads_render(self) -> None:
        paths = (
            "/sales",
            "/sales/decision-center",
            "/sales/ephemerides",
            "/sales/rnp",
            "/sales/unit-economics-1c/cabinet-settings",
            "/sales/unit-economics-1c",
            "/sales/unit-economics-1c/ozon",
            "/sales/unit-economics-1c/yandex-market",
            "/supply",
            "/stock",
            "/stock-2",
            "/stock-2/details/frozen",
            "/stock/rimili",
            "/stock/rimili/fbs",
            "/stock/rimili/warehouses",
            "/stock/rimili/operations",
            "/stock/cost-report",
            "/admin",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, f"{path}: {response.text[:500]}")
                self.assertTrue(response.content)

        root = self.client.get("/", follow_redirects=False)
        self.assertEqual(root.status_code, 303)
        self.assertEqual(root.headers["location"], "/sales")

        stock_page = self.client.get("/stock")
        self.assertIn('id="sync-products-btn"', stock_page.text)
        self.assertIn("Синхронизировать товары", stock_page.text)
        self.assertIn("catalogBadge(entry.wb_catalog)", stock_page.text)
        self.assertIn("if (!response.ok)", stock_page.text)
        self.assertIn('aria-live="polite"', stock_page.text)

        article = self.client.get(
            "/stock/rimili/article-detail",
            params={"mp": "WB", "article": "949558341"},
        )
        self.assertEqual(article.status_code, 200, article.text)
        self.assertIn("949558341", article.text)

        downloads = (
            "/stock/rimili/stock.xlsx?mp=WB",
            "/stock/rimili/warehouses/xlsx?mp=WB&scheme=fbo&warehouse=Коледино",
            "/stock/rimili/operations/xlsx",
            "/stock/cost-report.xlsx",
            f"/admin/operations/{self.operation_id}/xlsx",
        )
        for path in downloads:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200, response.text[:500])
                self.assertTrue(response.content)

    def test_only_unit_economics_1c_is_visible_in_sales_navigation(self) -> None:
        response = self.client.get("/sales")

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/sales/unit-economics-1c"', response.text)
        self.assertIn('href="/sales/unit-economics-1c/cabinet-settings"', response.text)
        self.assertNotIn('href="/sales/unit-economics/wb-fbs"', response.text)

    def test_cost_report_tables_have_filters_and_separate_store_marketplace_columns(self) -> None:
        db.record_operation(
            "rimili",
            "delivery",
            "manual",
            [
                {
                    "article": "949558341",
                    "barcode": "2050292584830",
                    "name": "Ретро гирлянда",
                    "quantity": 2,
                }
            ],
            1,
            "Unit Admin",
            NOW,
            to_fulfillment="ФулСервис Подольск",
            to_marketplace="WB",
        )
        for view, table_class in (
            ("summary", "cost-summary-table"),
            ("deliveries", "cost-operation-table"),
            ("fbs_sales", "cost-sales-table"),
        ):
            with self.subTest(view=view):
                response = self.client.get(
                    "/stock/cost-report",
                    params={
                        "date_from": "2026-08-11",
                        "date_to": "2026-08-13",
                        "view": view,
                    },
                )

                self.assertEqual(response.status_code, 200, response.text[:500])
                if view != "fbs_sales":
                    self.assertIn(f'class="{table_class} data-table"', response.text)
                    self.assertIn("<th>Магазин</th><th>Маркетплейс</th>", response.text)
                if view == "summary":
                    self.assertIn(
                        "<th>Продано FBS, ед.</th><th>ЗЦ продаж FBS</th>",
                        response.text,
                    )
                self.assertIn("Все маркетплейсы", response.text)
                self.assertNotIn("Все каналы", response.text)
                self.assertNotIn("нет ЗЦ для", response.text)

    def test_cost_report_can_classify_shipment_as_fbs_transfer(self) -> None:
        operation_id = db.record_operation(
            "rimili",
            "shipment",
            "manual",
            [
                {
                    "article": "949558341",
                    "barcode": "2050292584830",
                    "name": "Ретро гирлянда",
                    "quantity": 2,
                }
            ],
            1,
            "Unit Admin",
            NOW,
            from_fulfillment="ФулСервис Подольск",
            from_marketplace="WB",
        )

        response = self.client.post(
            f"/stock/cost-report/operations/{operation_id}/fbs-transfer",
            data={
                "is_fbs_transfer": "1",
                "date_from": "2026-08-11",
                "date_to": "2026-08-13",
                "view": "shipments",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 303, response.text)
        operations = db.get_operations_with_items_for_period(
            ("rimili",),
            "2026-08-11T00:00:00+00:00",
            "2026-08-13T23:59:59+00:00",
        )
        shipment = next(operation for operation in operations if operation["id"] == operation_id)
        self.assertEqual(shipment["is_fbs_transfer"], 1)

    def test_unit_economics_1c_cabinet_settings_page_and_api(self) -> None:
        root = Path(__file__).resolve().parents[2]
        settings_script = (root / "static" / "unit-economics-1c-cabinet-settings.js").read_text(
            encoding="utf-8"
        )
        settings_styles = (root / "static" / "unit-economics-1c-cabinet-settings.css").read_text(
            encoding="utf-8"
        )
        page = self.client.get("/sales/unit-economics-1c/cabinet-settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Ввод данных по кабинетам", page.text)
        self.assertIn("Только хранение данных", page.text)
        self.assertIn("unit-economics-1c-cabinet-settings.js", page.text)
        self.assertIn('id="ue1cs-source-sync"', page.text)
        self.assertIn("Выгрузить себес", page.text)
        self.assertIn('id="ue1cs-price-sync"', page.text)
        self.assertIn("Выгрузить цены", page.text)
        self.assertIn('"acquiring_percent": 3.8', page.text)
        self.assertIn('"team_commission_percent": 0.0', page.text)
        self.assertIn('"vat_percent": 9.0', page.text)
        self.assertIn('"usn_percent": 0.0', page.text)
        self.assertIn('"tax_system": "usn"', page.text)
        self.assertIn("Google Sheets", page.text)
        self.assertIn("key: 'vat_percent', label: 'Налог НДС'", settings_script)
        self.assertIn("key: 'usn_percent', label: 'Налог УСН'", settings_script)
        self.assertIn("key: 'osno_percent', label: 'Налог ОСНО'", settings_script)
        self.assertIn("key: 'tax_system', label: 'Система налогообложения'", settings_script)
        self.assertIn("gogolOnly: true", settings_script)
        self.assertIn("payload.usn_percent = 0", settings_script)
        self.assertIn("payload.osno_percent = 0", settings_script)
        self.assertIn(".ue1cs-field[hidden]", settings_styles)

        payload = {
            "acceptance_coefficient": 1.2,
            "wb_extra_tariff_percent": 2.3,
            "acquiring_percent": 4.2,
            "vat_percent": 10,
            "usn_percent": 6,
            "osno_percent": 0,
            "tax_system": "usn",
        }
        saved = self.client.put(
            "/api/unit-economics-1c/cabinet-settings/rimili",
            json=payload,
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["settings"]["acquiring_percent"], 4.2)
        self.assertEqual(saved.json()["settings"]["team_commission_percent"], 0)
        self.assertEqual(saved.json()["settings"]["vat_percent"], 10)
        self.assertEqual(saved.json()["settings"]["usn_percent"], 6)
        self.assertEqual(saved.json()["settings"]["tax_system"], "usn")

        loaded = self.client.get("/api/unit-economics-1c/cabinet-settings?marketplace=WB")
        self.assertEqual(loaded.status_code, 200)
        rimili = next(item for item in loaded.json()["items"] if item["store_slug"] == "rimili")
        self.assertEqual(rimili["acquiring_percent"], 4.2)
        self.assertEqual(rimili["team_commission_percent"], 0)
        self.assertEqual(rimili["vat_percent"], 10)
        self.assertEqual(rimili["usn_percent"], 6)
        gogol_payload = {
            **payload,
            "vat_percent": 20,
            "osno_percent": 25,
            "tax_system": "osno",
        }
        gogol_saved = self.client.put(
            "/api/unit-economics-1c/cabinet-settings/gogol",
            json=gogol_payload,
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(gogol_saved.status_code, 200, gogol_saved.text)
        self.assertEqual(gogol_saved.json()["settings"]["vat_percent"], 20)
        self.assertEqual(gogol_saved.json()["settings"]["usn_percent"], 0)
        self.assertEqual(gogol_saved.json()["settings"]["osno_percent"], 25)
        self.assertEqual(gogol_saved.json()["settings"]["tax_system"], "osno")
        gogol_usn_saved = self.client.put(
            "/api/unit-economics-1c/cabinet-settings/gogol",
            json={**gogol_payload, "usn_percent": 5, "tax_system": "usn"},
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(gogol_usn_saved.status_code, 200, gogol_usn_saved.text)
        self.assertEqual(gogol_usn_saved.json()["settings"]["usn_percent"], 5)
        self.assertEqual(gogol_usn_saved.json()["settings"]["osno_percent"], 0)
        gogol_loaded = self.client.get("/api/unit-economics-1c/cabinet-settings?marketplace=WB")
        gogol = next(item for item in gogol_loaded.json()["items"] if item["store_slug"] == "gogol")
        self.assertEqual(gogol["usn_percent"], 5)
        self.assertEqual(gogol["osno_percent"], 0)
        unit_page = self.client.get("/sales/unit-economics-1c")
        self.assertEqual(unit_page.status_code, 200)
        self.assertIn('"acquiring": 4.2', unit_page.text)
        self.assertIn('"team_commission_percent": 0.0', unit_page.text)
        self.assertIn('"vat_percent": 10.0', unit_page.text)
        self.assertIn('"usn_percent": 6.0', unit_page.text)
        forbidden = self.client.put(
            "/api/unit-economics-1c/cabinet-settings/rimili",
            json={**payload, "team_commission_percent": 2.8},
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(forbidden.status_code, 422)
        self.assertEqual(
            self.client.put(
                "/api/unit-economics-1c/cabinet-settings/missing",
                json=payload,
                headers={"X-Requested-With": "fetch"},
            ).status_code,
            404,
        )
        invalid = {**payload, "vat_percent": -1}
        self.assertEqual(
            self.client.put(
                "/api/unit-economics-1c/cabinet-settings/rimili",
                json=invalid,
                headers={"X-Requested-With": "fetch"},
            ).status_code,
            422,
        )

    def test_unit_economics_1c_manual_source_sync(self) -> None:
        report = {
            "ok": True,
            "saved": 945,
            "sheet_count": 6,
            "source_rows": 1791,
            "matched": 945,
            "unmatched": 846,
        }
        with mock.patch.object(
            unit_economics.unit_economics_1c_source,
            "sync_all",
            return_value=report,
        ) as sync:
            response = self.client.post(
                "/api/unit-economics-1c/source-data/sync",
                headers={"X-Requested-With": "fetch"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["report"]["saved"], 945)
        self.assertTrue(response.json()["items"])
        sync.assert_called_once_with()

        with mock.patch.object(
            unit_economics.unit_economics_1c_source,
            "sync_all",
            side_effect=unit_economics.unit_economics_1c_source.SourceDataError("нет доступа"),
        ):
            failed = self.client.post(
                "/api/unit-economics-1c/source-data/sync",
                headers={"X-Requested-With": "fetch"},
            )
        self.assertEqual(failed.status_code, 502)
        self.assertIn("нет доступа", failed.json()["error"])

    def test_unit_economics_1c_uses_sidebar_marketplace_navigation(self) -> None:
        response = self.client.get("/sales/unit-economics-1c")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Юнит-экономика 1С", response.text)
        self.assertIn("949558341", response.text)
        self.assertIn("Ретро гирлянда", response.text)
        self.assertIn('id="ue1c-product-rows"', response.text)
        self.assertIn('id="ue1c-store-filter"', response.text)
        self.assertIn('id="ue1c-detail"', response.text)
        self.assertIn('id="ue1c-chart"', response.text)
        self.assertIn('id="ue1c-pagination"', response.text)
        self.assertIn('id="ue1c-page-size"', response.text)
        self.assertIn('<option value="100">100</option>', response.text)
        self.assertIn('class="ue1c-group-row"', response.text)
        self.assertIn('colspan="7" class="ue1c-header-group ue1c-col-tag', response.text)
        self.assertIn("Рекламные расходы", response.text)
        self.assertIn("ДРР, %", response.text)
        self.assertIn("Хватит, дней", response.text)
        self.assertNotIn('class="ue1c-col-price"', response.text)
        self.assertIn('class="data-table ue1c-redesign-table"', response.text)
        self.assertIn('data-filter-column="0"', response.text)
        self.assertIn('data-filter-column="18" data-filter-type="number"', response.text)
        self.assertIn("Комментарии", response.text)
        self.assertNotIn("Товарная юнит-экономика", response.text)
        self.assertNotIn('class="ue1c-kpis"', response.text)
        self.assertIn('href="/sales/unit-economics-1c/ozon"', response.text)
        self.assertNotIn('class="ue1c-market-tabs"', response.text)

        ozon = self.client.get("/sales/unit-economics-1c/ozon")
        yandex = self.client.get("/sales/unit-economics-1c/yandex-market")
        self.assertEqual(ozon.status_code, 200)
        self.assertEqual(yandex.status_code, 200)
        self.assertIn("Раздел Ozon в разработке", ozon.text)
        self.assertIn("Раздел Яндекс Маркета в разработке", yandex.text)
        self.assertIn('class="nav-subitem active" href="/sales/unit-economics-1c/ozon"', ozon.text)
        self.assertIn(
            'class="nav-subitem active" href="/sales/unit-economics-1c/yandex-market"',
            yandex.text,
        )

    def test_unit_economics_1c_hides_catalog_exclusions(self) -> None:
        db.set_product_exclusions(
            "rimili",
            "WB",
            {"949558341"},
            status="Старье",
            updated_at=NOW,
        )

        response = self.client.get("/sales/unit-economics-1c")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("949558341", response.text)

    def test_unit_economics_1c_product_contains_only_real_or_null_values(self) -> None:
        product = unit_economics._unit_economics_1c_mock_product(
            "rimili",
            {
                "article": "949558341",
                "barcode": "2050292584830",
                "name": "Ретро гирлянда",
                "image_url": "",
                "fbs_stock": 12,
                "fbo_stock": 8,
                "ff_available": 5,
            },
            {
                "seller_base_price": 10000,
                "retail_price": 7950,
                "club_discounted_price": 7600,
                "customer_price_with_spp": 5756,
                "customer_price_with_wallet": 5640,
                "day": "2026-08-19",
                "customer_price_window_days": 7,
                "customer_price_orders_count": 3,
            },
        )

        self.assertEqual(len(product["history"]), 7)
        self.assertEqual(product["price"]["current"], 7950)
        self.assertEqual(product["price"]["base"], 10000)
        self.assertEqual(product["price"]["club"], 7600)
        self.assertEqual(product["price"]["with_spp"], 5756)
        self.assertEqual(product["price"]["with_wallet"], 5640)
        self.assertEqual(product["price"]["window_days"], 7)
        self.assertIn("margin_rub", product["history"][0])
        self.assertNotIn("margin_percent", product["history"][0])
        self.assertIn("advertising_rub", product["history"][0])
        self.assertIn("drr_percent", product["history"][0])
        self.assertIn("purchase_value", product["history"][0])
        self.assertIn("purchase_cost", product["details"])
        self.assertEqual(product["details"]["acquiring"], 3.8)
        self.assertNotIn("spp_percent", product["details"])
        self.assertIsNone(product["rating"])
        self.assertTrue(all(value is None for value in product["tag_data"].values()))
        self.assertEqual(product["stock"]["fbs"], 12)
        self.assertEqual(product["stock"]["fbo"], 8)
        self.assertEqual(product["stock"]["fulfillment"], 5)
        self.assertEqual(product["stock"]["total"], 25)
        self.assertEqual(product["stock"]["days"], 0)
        self.assertEqual(product["stock"]["orders_21d"], 0)
        self.assertEqual(product["stock"]["average_daily_orders"], 0)
        self.assertEqual(product["history"][0]["margin_rub"], 0)
        self.assertEqual(product["history"][0]["drr_percent"], 0)
        self.assertIsNone(product["history"][0]["fbs_units"])
        self.assertEqual(product["history"][-1]["fbs_units"], 12)
        self.assertIsNone(product["details"]["subject"])
        self.assertEqual(product["details"]["commission_percent"], 0)
        self.assertEqual(product["details"]["purchase_cost"], 0)
        self.assertEqual(product["details"]["fulfillment_cost"], 0)
        self.assertNotIn("fbo_commission", product["details"])
        self.assertNotIn("fbs_commission", product["details"])
        self.assertNotIn("tax_type", product["details"])
        self.assertEqual(product["details"]["vat_percent"], 0)
        self.assertEqual(product["details"]["usn_percent"], 0)
        self.assertEqual(product["details"]["osno_percent"], 0)
        self.assertEqual(product["details"]["tax_system"], "usn")
        self.assertNotIn("margin_percent", product["details"])
        self.assertNotIn("fixed_margin", product["details"])
        self.assertNotIn("package", product["details"])

        product_with_demand = unit_economics._unit_economics_1c_mock_product(
            "rimili",
            {
                "article": "949558341",
                "fbs_stock": None,
                "fbo_stock": 8,
                "ff_available": None,
            },
            stock_order_metrics={
                "period_from": "2026-07-30",
                "period_to": "2026-08-19",
                "period_days": 21,
                "orders_count": 42,
            },
        )
        self.assertEqual(product_with_demand["stock"]["fbs"], 0)
        self.assertEqual(product_with_demand["stock"]["fbo"], 8)
        self.assertEqual(product_with_demand["stock"]["fulfillment"], 0)
        self.assertEqual(product_with_demand["stock"]["total"], 8)
        self.assertEqual(product_with_demand["stock"]["average_daily_orders"], 2)
        self.assertEqual(product_with_demand["stock"]["days"], 4)

        product_with_stock_history = unit_economics._unit_economics_1c_mock_product(
            "rimili",
            {"article": "949558341", "fbs_stock": 12, "fbo_stock": 8, "ff_available": 5},
            product_metrics={"period_to": "2026-08-19", "daily": []},
            stock_history_by_day={
                "2026-08-18": {"fbs": 9, "fbo": 7, "fulfillment": 4},
                "2026-08-19": {"fbs": 11, "fbo": 6, "fulfillment": 3},
            },
        )
        self.assertEqual(product_with_stock_history["history"][-2]["fbs_units"], 9)
        self.assertEqual(product_with_stock_history["history"][-1]["fbs_units"], 11)
        self.assertEqual(product_with_stock_history["history"][-1]["fbo_units"], 6)
        self.assertEqual(product_with_stock_history["history"][-1]["fulfillment_units"], 3)

    def test_unit_economics_1c_commission_panel_uses_split_taxes(self) -> None:
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "unit-economics-1c.js").read_text(encoding="utf-8")
        template = (root / "templates" / "unit_economics_1c_content.html").read_text(encoding="utf-8")

        self.assertNotIn("'NULL'", script)
        self.assertNotIn("parameter('FBO'", script)
        self.assertNotIn("parameter('FBS'", script)
        self.assertNotIn("parameter('Ставка'", script)
        self.assertNotIn("parameter('Тип'", script)
        self.assertNotIn("parameter('ИРП'", script)
        self.assertNotIn("parameter('Упаковка'", script)
        self.assertNotIn("parameter('Фикс. маржа'", script)
        self.assertIn("parameter('Налоговая система'", script)
        self.assertIn("parameter('НДС', nullable(item.vat_percent, decimal, '%'))", script)
        self.assertIn("metric('Чистая прибыль', nullable(metrics.margin, preciseMoney)", script)
        self.assertNotIn("metric('Маржинальность'", script)
        for key in (
            "retail",
            "spp",
            "client",
            "walletPercent",
            "wallet",
            "commission",
            "commissionRub",
            "drr",
            "advertisingRub",
            "logistics",
            "storage",
            "storageTotal",
            "acquiringPercent",
            "acquiringRub",
            "purchase",
            "team",
            "teamRub",
            "fulfillment",
            "vat",
            "vatRub",
            "usn",
            "osno",
            "secondaryTaxRub",
        ):
            self.assertIn(f'data-calculator-input="{key}"', template)
        self.assertIn("current * acquiringPercent / 100", script)
        self.assertNotIn("walletPrice * acquiringPercent / 100", script)
        self.assertIn("- acquiringPercent / 100", script)
        self.assertNotIn("- walletPriceFactor * acquiringPercent / 100", script)
        self.assertIn("current * drrPercent / 100", script)
        self.assertIn("current * teamCommissionPercent / 100", script)
        self.assertNotIn("clientPrice * teamCommissionPercent / 100", script)
        self.assertIn("- teamCommissionPercent / 100", script)
        self.assertIn("clientPrice * vatPercent / (100 + vatPercent)", script)
        self.assertIn("(clientPrice - vat) * activeTaxPercent / 100", script)
        self.assertIn("clientPrice * activeTaxPercent / 100", script)
        self.assertIn("clientPriceFactor * (vatFactor + secondaryTaxFactor)", script)
        self.assertNotIn("taxRate", script)
        self.assertIn("storageRate * turnoverDays", script)
        self.assertIn("current - acquiring - logistics - storage - commission - advertising", script)
        self.assertIn("netRevenue - purchase - fulfillment - teamCommission - tax", script)
        self.assertIn("margin / purchase * 100", script)
        self.assertIn("function sppPriceFactor(product)", script)
        self.assertIn("function walletPriceFactor(product)", script)
        self.assertIn("function syncLinkedPriceInputs(product, source)", script)
        self.assertIn("source === 'retail'", script)
        self.assertIn("source === 'client'", script)
        self.assertIn("source === 'wallet'", script)
        self.assertIn("syncLinkedPriceInputs(product, editedPriceKind)", script)
        self.assertIn('id="ue1c-spp-price-input" data-calculator-input="client"', template)
        self.assertIn('id="ue1c-calculator-mode" type="checkbox" role="switch"', template)
        self.assertIn('id="ue1c-calculator-inputs"', template)
        self.assertIn("Затраты на ФФ", template)
        self.assertIn("Рекламные расходы, руб (7 дней)", template)
        self.assertIn('id="ue1c-secondary-tax-label"', template)
        for output_id in (
            "ue1c-spp-percent",
            "ue1c-wallet-percent",
            "ue1c-commission-rub",
            "ue1c-advertising-rub",
            "ue1c-acquiring-percent",
            "ue1c-acquiring-rub",
            "ue1c-storage-rub",
            "ue1c-purchase-rub",
            "ue1c-team-rub",
            "ue1c-vat-rub",
            "ue1c-secondary-tax-rub",
        ):
            self.assertIn(f'id="{output_id}"', template)
        self.assertIn("calculatorFields.classList.toggle('is-expanded'", script)
        self.assertIn("function syncDetailedCalculatorInputs(product, source)", script)
        self.assertIn("syncPair('commission', 'commissionRub'", script)
        self.assertIn("syncPair('acquiringPercent', 'acquiringRub'", script)
        self.assertIn("syncPair('team', 'teamRub'", script)
        self.assertIn("finite(values.storageTotal", script)
        self.assertIn("finite(values.vatRub", script)
        self.assertIn("finite(values.secondaryTaxRub", script)
        self.assertIn("product.store_slug === 'gogol'", script)
        self.assertNotIn("data-price-filter=", template)
        self.assertNotIn("data-color-filter", template)
        self.assertNotIn("is-price-queued", script)
        self.assertNotIn('id="ue1c-reset-prices"', template)
        self.assertNotIn('id="ue1c-show-changed"', template)
        self.assertNotIn("priceQueueTtlMs", script)
        self.assertNotIn("schedulePriceQueueExpiry", script)
        self.assertNotIn("expirePendingPrices", script)
        self.assertNotIn("Выгрузить цены", template)
        self.assertIn('id="ue1c-price-confirm-modal"', template)
        self.assertIn("/api/unit-economics-1c/prices/preview", script)
        self.assertIn("Подтвердить и отправить", template)
        self.assertEqual(
            unit_economics.unit_economics_1c_prices.PRICE_SYNC_AFTER_UPLOAD_DELAY_SECONDS,
            10.0,
        )
        self.assertNotIn("ue1c-store-row", script)
        self.assertIn("function calculateSppPercent(product)", script)
        self.assertIn("(withoutSpp - withSpp) / withoutSpp * 100", script)
        self.assertIn(
            "parameter('СПП', nullable(calculateSppPercent(product), decimal, '%'))",
            script,
        )
        self.assertNotIn("item.spp_percent", script)
        self.assertNotIn("Структура расходов", template)
        self.assertNotIn('id="ue1c-costs"', template)
        self.assertNotIn("nodes.costs", script)
        self.assertIn(
            '<details class="ue1c-drawer-section ue1c-profit-formula">',
            template,
        )
        self.assertIn('<summary class="ue1c-section-head">', template)
        self.assertEqual(template.count("Формула чистой прибыли"), 1)
        self.assertIn(
            "чистая выручка − закупочная цена − фулфилмент − комиссия компании − налог НДС − налог УСН/ОСНО",
            template,
        )
        self.assertIn(
            "цена без СПП − эквайринг − логистика − хранение − комиссия WB − реклама по ДРР",
            template,
        )
        self.assertIn("можно изменять любой показатель для симуляции", template)
        self.assertIn("Эквайринг, комиссия компании, комиссия WB и ДРР считаются от цены без СПП", template)
        self.assertIn("НДС</strong> = цена покупателя × ставка НДС", template)
        self.assertIn("УСН</strong> = (цена покупателя − НДС) × ставка УСН", template)

    def test_unit_economics_1c_history_calculates_daily_drr_from_raw_metrics(self) -> None:
        product = unit_economics._unit_economics_1c_mock_product(
            "trusthome",
            {"article": "551394618", "name": "Товар"},
            product_metrics={
                "period_to": "2026-08-19",
                "period_days": 7,
                "daily": [
                    {
                        "date": "2026-08-15",
                        "advertising_spend": 211.5,
                        "orders_amount": 4372,
                        "orders_count": 3,
                    }
                ],
            },
        )

        day = next(item for item in product["history"] if item["date"] == "2026-08-15")
        self.assertEqual(day["drr_percent"], 4.84)

    def test_unit_economics_1c_period_economics_apply_real_buyout(self) -> None:
        product = unit_economics._unit_economics_1c_mock_product(
            "rimili",
            {"article": "949735537", "name": "Товар"},
            {
                "retail_price": 1000,
                "customer_price_with_spp": 800,
                "customer_price_with_wallet": 700,
            },
            acquiring_percent=3.8,
            product_metrics={
                "period_from": "2026-08-13",
                "period_to": "2026-08-19",
                "period_days": 7,
                "orders_amount": 8000,
                "orders_count": 10,
                "sold_count": 8,
                "buyout_percent": 80,
                "spend": 800,
                "drr": 10,
                "daily": [
                    {
                        "date": "2026-08-19",
                        "advertising_spend": 800,
                        "orders_amount": 8000,
                        "orders_count": 10,
                        "sold_count": 8,
                        "drr": 10,
                    }
                ],
            },
            team_commission_percent=4,
            vat_percent=9,
            usn_percent=6,
            product_reference={
                "turnover_days": 21,
                "purchase_price": 300,
                "fulfillment_cost": 50,
                "subject_commission_percent": 10,
            },
        )

        self.assertEqual(product["advertising"]["buyout_percent"], 80)
        self.assertEqual(product["economics_7d"]["turnover"], 8000)
        self.assertEqual(product["economics_7d"]["orders"], 10)
        self.assertEqual(product["details"]["vat_value"], 66.06)
        self.assertEqual(product["details"]["usn_value"], 44.04)
        self.assertEqual(product["details"]["tax_value"], 110.09)
        self.assertEqual(product["economics_7d"]["margin"], 2619.1)
        self.assertEqual(product["economics_7d"]["roi"], 87.3)
        self.assertEqual(product["history"][-1]["purchased_units"], 8)
        self.assertEqual(product["history"][-1]["margin_rub"], 2095.28)

        gogol_taxes = unit_economics._unit_economics_1c_mock_product(
            "gogol",
            {"article": "GOGOL-TAX", "name": "Товар"},
            {"retail_price": 1000, "customer_price_with_spp": 800},
            vat_percent=20,
            usn_percent=6,
            osno_percent=25,
            tax_system="osno",
        )
        self.assertEqual(gogol_taxes["details"]["tax_system"], "osno")
        self.assertEqual(gogol_taxes["details"]["vat_value"], 133.33)
        self.assertEqual(gogol_taxes["details"]["usn_value"], 0)
        self.assertEqual(gogol_taxes["details"]["osno_value"], 200)
        self.assertEqual(gogol_taxes["details"]["tax_value"], 333.33)

        non_gogol_taxes = unit_economics._unit_economics_1c_mock_product(
            "rimili",
            {"article": "RIMILI-TAX", "name": "Товар"},
            {"retail_price": 1000, "customer_price_with_spp": 800},
            vat_percent=20,
            usn_percent=6,
            osno_percent=25,
            tax_system="osno",
        )
        self.assertEqual(non_gogol_taxes["details"]["tax_system"], "usn")
        self.assertEqual(non_gogol_taxes["details"]["vat_value"], 133.33)
        self.assertEqual(non_gogol_taxes["details"]["usn_value"], 40)
        self.assertEqual(non_gogol_taxes["details"]["osno_value"], 0)
        self.assertEqual(non_gogol_taxes["details"]["tax_value"], 173.33)

        storefront_only = unit_economics._unit_economics_1c_mock_product(
            "rimili",
            {"article": "949558341", "name": "Ретро гирлянда"},
            {"retail_price": None, "customer_price_with_spp": 2034},
        )
        self.assertIsNone(storefront_only["price"]["current"])
        self.assertEqual(storefront_only["price"]["with_spp"], 2034)
        self.assertNotIn("spp_percent", storefront_only["details"])

    def test_unit_economics_1c_uses_abc_turnover_and_total_wb_percent(self) -> None:
        product = unit_economics._unit_economics_1c_mock_product(
            "trusthome",
            {"article": "367080326", "name": "Смеситель"},
            {"retail_price": 1232.84, "customer_price_with_spp": 986},
            product_settings=UnitEconomics1CProductSettings(
                store_slug="trusthome",
                article="367080326",
                storage_wb_rub=1.7,
            ),
            product_reference={
                "abc_code": "A",
                "turnover_days": 30,
                "category": "Смесители",
                "subject_commission_percent": 24.62,
                "purchase_price": 901.35,
                "fulfillment_cost": 100.53,
                "team_commission_percent": 3.85,
                "tag_raw": "1 / 0 / SHORT1 / W34 2026",
                "goal_week": 1,
                "goal_day": 0,
                "stock_status": "SHORT1",
                "stock_end_week": "W34 2026",
                "fact_sales": 4,
                "plan_sales": 7,
            },
            wb_extra_tariff_percent=2.3,
        )

        self.assertEqual(product["tag_data"]["code"], "A")
        self.assertEqual(product["tag_data"]["goal_week"], 1)
        self.assertEqual(product["tag_data"]["status"], "SHORT1")
        self.assertEqual(product["tag_data"]["ends"], "W34 2026")
        self.assertEqual(product["tag_data"]["fact"], 4)
        self.assertEqual(product["tag_data"]["plan"], 7)
        self.assertEqual(product["details"]["purchase_cost"], 901.35)
        self.assertEqual(product["details"]["fulfillment_cost"], 100.53)
        self.assertEqual(product["details"]["team_commission_percent"], 3.85)
        self.assertEqual(product["details"]["storage_days"], 30)
        self.assertEqual(product["details"]["storage_sum"], 51)
        self.assertEqual(product["details"]["subject"], "Смесители")
        self.assertEqual(product["details"]["subject_commission_percent"], 24.62)
        self.assertEqual(product["details"]["wb_extra_tariff_percent"], 2.3)
        self.assertEqual(product["details"]["commission_percent"], 26.92)
        self.assertEqual(product["details"]["commission_value"], 331.88)

    def test_unit_economics_1c_price_sync_endpoint(self) -> None:
        with mock.patch.object(
            unit_economics.unit_economics_1c_prices,
            "sync_stores",
            return_value={"rimili": {"ok": True, "rows": 2}},
        ) as sync:
            response = self.client.post(
                "/api/unit-economics-1c/prices/sync",
                headers={"X-Requested-With": "fetch"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        sync.assert_called_once()

    def test_unit_economics_1c_price_submit_endpoint(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [{"article": "949558341", "barcode": "2050292584830", "name": "Товар"}],
            NOW,
        )
        report = {
            "ok": True,
            "sent": 1,
            "upload_id": 12345,
            "accepted": [
                {
                    "product_id": "rimili:949558341",
                    "article": "949558341",
                    "base_price": 1000,
                    "target_price": 800,
                    "discount": 20,
                    "calculated_price": 800,
                }
            ],
            "errors": [],
        }
        finalized_report = {
            **report,
            "price_data_refreshed": True,
            "sync": {"ok": True, "rows": 1},
        }
        with (
            mock.patch.object(
                unit_economics.unit_economics_1c_prices,
                "submit_price_changes",
                return_value=report,
            ) as submit,
            mock.patch.object(
                unit_economics.unit_economics_1c_prices,
                "finalize_price_change_report",
                return_value=finalized_report,
            ) as finalize,
        ):
            response = self.client.post(
                "/api/unit-economics-1c/prices",
                json={
                    "data": [
                        {
                            "store_slug": "rimili",
                            "article": "949558341",
                            "target_price": 800,
                        }
                    ]
                },
                headers={"X-Requested-With": "fetch"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["accepted_product_ids"], ["rimili:949558341"])
        submit.assert_called_once_with(
            "rimili",
            [
                {
                    "article": "949558341",
                    "target_price": 800.0,
                    "target_kind": "retail",
                }
            ],
        )
        finalize.assert_called_once_with("rimili", report)
        self.assertTrue(response.json()["price_data_refreshed"])

    def test_unit_economics_1c_price_preview_endpoint_is_point_only(self) -> None:
        db.replace_catalog(
            "rimili",
            "WB",
            [{"article": "949558341", "barcode": "2050292584830", "name": "Товар"}],
            NOW,
        )
        report = {
            "ok": True,
            "planned": 1,
            "accepted": [
                {
                    "product_id": "rimili:949558341",
                    "article": "949558341",
                    "target_kind": "spp",
                    "target_price": 700,
                    "base_price": 2002,
                    "discount": 50,
                    "predicted_spp_price": 700,
                }
            ],
            "errors": [],
        }
        with mock.patch.object(
            unit_economics.unit_economics_1c_prices,
            "preview_price_changes",
            return_value=report,
        ) as preview:
            response = self.client.post(
                "/api/unit-economics-1c/prices/preview",
                json={
                    "data": [
                        {
                            "store_slug": "rimili",
                            "article": "949558341",
                            "target_kind": "spp",
                            "target_price": 700,
                        }
                    ]
                },
                headers={"X-Requested-With": "fetch"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["accepted"][0]["predicted_spp_price"], 700)
        preview.assert_called_once_with(
            "rimili",
            [
                {
                    "article": "949558341",
                    "target_price": 700.0,
                    "target_kind": "spp",
                }
            ],
        )

        too_many = self.client.post(
            "/api/unit-economics-1c/prices/preview",
            json={
                "data": [
                    {
                        "store_slug": "rimili",
                        "article": "949558341",
                        "target_price": 700,
                    },
                    {
                        "store_slug": "rimili",
                        "article": "949558341",
                        "target_price": 701,
                    },
                ]
            },
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(too_many.status_code, 422)

    def test_unit_economics_1c_product_settings_save_and_render(self) -> None:
        payload = {
            "article": "949558341",
            "delivery_wb_rub": 100,
            "buyout_percent": 80,
            "return_cost_rub": 50,
            "volume_l": 1.2,
            "storage_wb_rub": 3,
        }
        response = self.client.put(
            "/api/unit-economics-1c/product-settings/rimili",
            json=payload,
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()["settings"]
        self.assertEqual(saved["paid_acceptance_cost"], 0)
        self.assertEqual(saved["delivery_with_returns"], 130)

        page = self.client.get("/sales/unit-economics-1c")
        self.assertEqual(page.status_code, 200)
        self.assertIn('"delivery_wb_rub": 100.0', page.text)
        self.assertIn('"buyout_percent": 80.0', page.text)
        self.assertIn('"delivery_with_returns": 130.0', page.text)
        self.assertIn('"subject": null', page.text)

        invalid = self.client.put(
            "/api/unit-economics-1c/product-settings/rimili",
            json={**payload, "buyout_percent": 101},
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(invalid.status_code, 422)

    def test_unit_economics_1c_does_not_render_price_only_history(self) -> None:
        db.upsert_unit_economics_1c_daily_prices(
            [
                {
                    "store_slug": "rimili",
                    "article": "STALE-HISTORY-ONLY",
                    "day": "2026-08-19",
                    "marketplace": "WB",
                    "nm_id": "999999999",
                    "currency": "RUB",
                    "customer_price_orders_count": 0,
                    "updated_at": NOW,
                }
            ]
        )
        page = self.client.get("/sales/unit-economics-1c")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("STALE-HISTORY-ONLY", page.text)

    def test_yandex_stock_shows_combined_fby_and_fbs(self) -> None:
        db.replace_catalog(
            "rimili",
            "YANDEX MARKET",
            [{"article": "YA-1", "barcode": "123", "name": "Яндекс товар"}],
            NOW,
        )
        db.upsert_mp_stock("rimili", "YA-1", "YANDEX MARKET", "fbo", 19, NOW)
        db.upsert_mp_stock("rimili", "YA-1", "YANDEX MARKET", "fbs", 17, NOW)
        db.replace_mp_warehouse_stock(
            "rimili",
            "YANDEX MARKET",
            "fbs_149217490",
            [("YA-1", "Afflatus", None, 17, NOW)],
        )

        page = self.client.get("/stock/rimili", params={"mp": "YANDEX MARKET"})
        self.assertEqual(page.status_code, 200, page.text[:500])
        self.assertIn("FBY — склады Маркета", page.text)
        self.assertIn("FBS — склады продавца", page.text)
        self.assertIn('tot-grand">36</strong>', page.text)
        self.assertIn('tot-fbo">19</strong>', page.text)
        self.assertIn('tot-fbs">17</strong>', page.text)

        fbs = self.client.get("/stock/rimili/fbs", params={"mp": "YANDEX MARKET"})
        self.assertEqual(fbs.json(), {"fbs": {"YA-1": 17}})

        detail = self.client.get(
            "/stock/rimili/article-detail",
            params={"mp": "YANDEX MARKET", "article": "YA-1"},
        )
        fulfillment = next(row for row in detail.json()["warehouses"] if row["name"] == "AFFLATUS Купавна")
        self.assertEqual(fulfillment["fbs"], 17)

        warehouses = self.client.get("/stock/rimili/warehouses", params={"mp": "YANDEX MARKET"})
        self.assertEqual(warehouses.status_code, 200, warehouses.text[:500])
        self.assertIn("Afflatus", warehouses.text)

    def test_page_and_download_errors_are_reported(self) -> None:
        cases = (
            ("/stock/unknown", 404),
            ("/stock/unknown/fbs", 404),
            ("/stock/unknown/warehouses", 404),
            ("/stock/unknown/operations", 404),
            ("/stock-2/details/unknown", 404),
            ("/admin/operations/999999/xlsx", 404),
            ("/stock/rimili/article-detail?mp=WB&article=missing", 200),
        )
        for path, status in cases:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, status)

    def test_sales_decision_and_rnp_api_contracts(self) -> None:
        dashboard = {
            "ok": True,
            "marketplace": "WB",
            "period": {},
            "summary": {},
            "opportunities": [],
            "products": [],
            "sync": [],
        }
        with mock.patch.object(decision_routes.decision_service, "dashboard", return_value=dashboard):
            response = self.client.get("/api/decision-center")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        with mock.patch.object(
            decision_routes.decision_service, "dashboard", side_effect=RuntimeError("broken")
        ):
            response = self.client.get("/api/decision-center")
        self.assertEqual(response.status_code, 500)

        with mock.patch.object(
            decision_routes.decision_service, "sync_many", return_value={"rimili": {"ok": True}}
        ):
            response = self.client.post("/api/decision-center/sync", json={"store": "rimili"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("rimili", response.json()["stores"])

        with mock.patch.object(
            self.app.state.container.decision_commands,
            "set_status",
            return_value=DecisionAction(
                fingerprint="rimili:test",
                status=DecisionStatus.COMPLETED,
                updated_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ):
            response = self.client.post(
                "/api/decision-center/status",
                json={"fingerprint": "rimili:test", "status": "completed"},
            )
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            self.app.state.container.decision_commands,
            "set_status",
            side_effect=ValueError("bad status"),
        ):
            response = self.client.post(
                "/api/decision-center/status",
                json={"fingerprint": "rimili:test", "status": "completed"},
            )
        self.assertEqual(response.status_code, 400)

        rnp_payload = {
            "ok": True,
            "marketplace": "WB",
            "store": "rimili",
            "items": [],
            "summary": {},
        }
        with mock.patch.object(rnp_routes.rnp_service, "dashboard", return_value=rnp_payload):
            response = self.client.get("/api/rnp?store=rimili&month=2026-08")
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(rnp_routes.rnp_service, "dashboard", side_effect=ValueError("bad")):
            response = self.client.get("/api/rnp?store=rimili&month=bad")
        self.assertEqual(response.status_code, 400)
        with mock.patch.object(rnp_routes.rnp_service, "dashboard", side_effect=RuntimeError("db")):
            response = self.client.get("/api/rnp?store=rimili&month=2026-08")
        self.assertEqual(response.status_code, 500)

        with (
            mock.patch.object(rnp_routes.rnp_service, "sales_lookback_days", return_value=8),
            mock.patch.object(rnp_routes.sales_service, "sync_store", return_value={"ok": True}),
            mock.patch.object(rnp_routes.rnp_service, "sync_metrics", return_value={"ok": True}),
        ):
            response = self.client.post(
                "/api/rnp/sync",
                json={"store": "rimili", "month": "2026-08", "marketplace": "WB"},
            )
        self.assertEqual(response.status_code, 200)

        with mock.patch.object(
            self.app.state.container.rnp_commands,
            "save_strategy",
            return_value=RnpStrategy(
                store_slug="rimili",
                marketplace=Marketplace.WB,
                article="949558341",
                strategy="growth",
                date_from="2026-08-01",
                date_to="2026-08-31",
                updated_by="Unit Admin",
                updated_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ):
            response = self.client.post(
                "/api/rnp/strategy",
                json={
                    "store": "rimili",
                    "marketplace": "WB",
                    "article": "949558341",
                    "strategy": "growth",
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                },
            )
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            self.app.state.container.rnp_commands,
            "add_action",
            return_value=RnpAction(
                id=1,
                article="949558341",
                action_date="2026-08-12",
                note="Done",
                user_name="Unit Admin",
                created_at=datetime(2026, 8, 12, tzinfo=UTC),
            ),
        ):
            response = self.client.post(
                "/api/rnp/action",
                json={
                    "store": "rimili",
                    "marketplace": "WB",
                    "article": "949558341",
                    "action_date": "2026-08-12",
                    "note": "Done",
                },
            )
        self.assertEqual(response.status_code, 200)

    def test_sales_api_contracts(self) -> None:
        with mock.patch.object(
            sales_overview.sales_service,
            "dashboard",
            return_value={"ok": True, "daily": [], "summary": {}},
        ):
            response = self.client.get(
                "/api/sales?store=rimili&marketplace=WB&date_from=2026-08-01&date_to=2026-08-12"
            )
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            wb_funnel_orders,
            "dashboard",
            return_value={"ok": True, "marketplace": "WB", "series": []},
        ):
            response = self.client.get(
                "/api/sales/wb-funnel-orders?store=rimili&date_from=2026-08-01&date_to=2026-08-12"
            )
        self.assertEqual(response.status_code, 200)
        with mock.patch.object(
            wb_funnel_orders,
            "dashboard",
            return_value={"ok": True, "marketplace": "WB", "series": []},
        ):
            self.assertEqual(self.client.get("/api/sales/wb-funnel-orders").status_code, 200)
        with mock.patch.object(wb_funnel_orders, "dashboard", side_effect=ValueError("bad period")):
            self.assertEqual(self.client.get("/api/sales/wb-funnel-orders?store=rimili").status_code, 400)
        with mock.patch.object(
            sales_overview.sales_service, "dashboard", side_effect=ValueError("bad period")
        ):
            response = self.client.get("/api/sales?store=rimili")
        self.assertEqual(response.status_code, 400)
        with mock.patch.object(sales_overview.sales_service, "dashboard", side_effect=RuntimeError("db")):
            response = self.client.get("/api/sales?store=rimili")
        self.assertEqual(response.status_code, 500)

        workbook = b"PK\x03\x04unit"
        with mock.patch.object(sales_overview.sales_service, "export_xlsx", return_value=workbook):
            response = self.client.get(
                "/sales/orders.xlsx?store=rimili&marketplace=WB&date_from=2026-08-01&date_to=2026-08-12"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, workbook)
        with mock.patch.object(
            sales_overview.sales_service, "export_xlsx", side_effect=ValueError("bad period")
        ):
            response = self.client.get("/sales/orders.xlsx?store=rimili")
        self.assertEqual(response.status_code, 400)

    def test_authorization_and_system_failures(self) -> None:
        self.authentication_patch.stop()
        anonymous = mock.patch.object(self.app.state.container.identity, "user_for_token", return_value=None)
        anonymous.start()
        try:
            html_response = self.client.get("/stock", follow_redirects=False)
            json_response = self.client.get("/api/sales", headers={"accept": "application/json"})
            mutation_response = self.client.post("/api/rnp/sync", json={})
        finally:
            anonymous.stop()
            self.authentication_patch.start()
        self.assertEqual(html_response.status_code, 303)
        self.assertEqual(json_response.status_code, 401)
        self.assertEqual(mutation_response.status_code, 401)

        with mock.patch.object(
            self.app.state.container.health,
            "readiness",
            return_value=ReadinessStatus(status="unavailable", database="unavailable"),
        ):
            response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_invalid_json_and_forbidden_store_paths(self) -> None:
        limited_user = dict(self.user, role="user", store_slugs=["tris"])
        with mock.patch.object(
            self.app.state.container.identity, "user_for_token", return_value=limited_user
        ):
            stock_page = self.client.get("/stock")
            self.assertEqual(stock_page.status_code, 200)
            self.assertNotIn('id="sync-products-btn"', stock_page.text)
            self.assertEqual(self.client.post("/admin/sync-stock").status_code, 403)
            self.assertEqual(self.client.get("/api/decision-center?store=rimili").status_code, 403)
            self.assertEqual(
                self.client.post("/api/decision-center/sync", json={"store": "rimili"}).status_code,
                403,
            )
            self.assertEqual(
                self.client.post(
                    "/api/decision-center/status",
                    json={"fingerprint": "rimili:test", "status": "completed"},
                ).status_code,
                403,
            )
            self.assertEqual(self.client.get("/api/sales?store=rimili").status_code, 403)
            self.assertEqual(self.client.get("/api/sales/wb-funnel-orders?store=rimili").status_code, 403)
            self.assertEqual(self.client.get("/api/rnp?store=rimili").status_code, 403)
            self.assertEqual(
                self.client.post(
                    "/api/rnp/sync",
                    json={"store": "rimili", "marketplace": "WB", "month": "2026-08"},
                ).status_code,
                403,
            )
        bad_body = "[not-json"
        headers = {"content-type": "application/json"}
        self.assertEqual(
            self.client.post("/api/rnp/strategy", content=bad_body, headers=headers).status_code,
            422,
        )
        self.assertEqual(
            self.client.post("/api/rnp/action", content=json.dumps([]), headers=headers).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
