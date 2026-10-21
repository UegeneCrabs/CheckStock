"""Exercise the real calculator loader and report enrichment with isolated sources."""

import unittest
from contextlib import ExitStack
from copy import deepcopy
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app.dto.unit_economics_1c import UnitEconomics1CCabinetSettings, UnitEconomics1CProductSettings
from app.dto.wb_standard_costs import ReportRow, Snapshot
from app.economics.wb import catalogue as web
from app.economics.wb import standard_profit
from app.exports.stock_sheet_inbound import InboundExport


class InactiveCalculatorLoaderTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 10, 20)
        self.start, self.end = date(2026, 10, 12), date(2026, 10, 18)
        self.article = "123456789"
        self.timestamp = "2026-10-20T00:00:00+00:00"
        self.catalog = [
            {
                "article": self.article,
                "name": "Inactive product",
                "fbs_stock": 0,
                "fbo_stock": 0,
                "ff_available": 0,
            }
        ]
        cabinet = UnitEconomics1CCabinetSettings(
            store_slug="tris",
            updated_at=self.timestamp,
            default_buyout_percent=100,
            acquiring_percent=1,
            vat_percent=0,
            usn_percent=0,
        )
        product_settings = UnitEconomics1CProductSettings(
            store_slug="tris",
            article=self.article,
            updated_at=self.timestamp,
            delivery_wb_rub=20,
            return_cost_rub=46,
            volume_l=2,
            storage_wb_rub=0,
        )
        prices = [
            {
                "store_slug": "tris",
                "article": self.article,
                "day": self.today.isoformat(),
                "retail_price": 1000,
                "customer_price_with_spp": 900,
            }
        ]
        references = [
            {
                "store_slug": "tris",
                "article": self.article,
                "purchase_price": 400,
                "fulfillment_cost": 20,
                "turnover_days": 30,
                "subject_commission_percent": 10,
                "team_commission_percent": 0,
                "source_synced_at": self.timestamp,
            }
        ]
        stack = self.enterContext(ExitStack())
        # These are source readers only: filtering, product construction and formulas run unchanged.
        reads = {
            "get_unit_economics_1c_latest_daily_prices": prices,
            "get_unit_economics_1c_funnel_daily_order_rows": [],
            "list_unit_economics_1c_cabinet_settings": [cabinet],
            "list_unit_economics_1c_product_settings": [product_settings],
            "get_unit_economics_1c_product_reference_rows": references,
            "get_unit_economics_1c_latest_product_reputation": [],
            "get_unit_economics_1c_product_sales_starts": [],
            "get_stock_items": self.catalog,
            "get_unit_economics_1c_daily_margin_snapshots": [],
            "list_unit_economics_1c_price_sync_states": [{"store_slug": "tris", "status": "ok"}],
        }
        for name, value in reads.items():
            stack.enter_context(
                patch.object(web.db, name, side_effect=lambda *a, value=value, **kw: deepcopy(value))
            )
        stack.enter_context(patch.object(web.data_errors_repository, "source_states", return_value={}))
        stack.enter_context(patch.object(web.unit_economics_1c, "load_product_metrics", return_value={}))
        stack.enter_context(
            patch.object(web.unit_economics_1c, "load_product_average_daily_orders", return_value={})
        )
        days = {(self.start + timedelta(days=i)).isoformat() for i in range(9)}
        stack.enter_context(
            patch.object(
                web,
                "wb_days",
                return_value={
                    "tris": {"orders": days, "advertising": days},
                },
            )
        )
        stack.enter_context(
            patch.object(
                web.stock_sheet_inbound,
                "load",
                side_effect=lambda store, market, catalog: InboundExport(
                    catalog=catalog, quantities={self.article: 0}, available=True
                ),
            )
        )
        stack.enter_context(patch.object(standard_profit.wb_standard_costs, "photos", return_value=[]))

    def load(self, *, include_inactive):
        return web.load_wb_calculator_products(
            ("tris",),
            self.today,
            7,
            self.start,
            self.end,
            include_inactive=include_inactive,
        )[0]

    def test_real_loader_keeps_inactive_product_with_price_and_purchase(self):
        products = self.load(include_inactive=True)
        self.assertEqual(len(products), 1)
        product = products[0]
        self.assertEqual(product["article"], self.article)
        self.assertEqual(product["stock"]["total"], 0)
        self.assertEqual(product["stock"]["inbound"], 0)
        self.assertEqual(product["advertising"]["orders"], 0)
        self.assertEqual(product["current_economics"]["orders"], 0)
        self.assertEqual(product["price"]["current"], 1000)
        self.assertEqual(product["details"]["purchase_cost"], 400)
        self.assertEqual(product["details"]["fulfillment_cost"], 20)

    def test_normal_catalogue_still_hides_inactive_product(self):
        self.assertEqual(self.load(include_inactive=False), [])

    def test_weekly_report_matches_inactive_product_through_real_loader(self):
        snapshot = Snapshot(
            date_from=self.start.isoformat(),
            date_to=self.end.isoformat(),
            rows=(
                ReportRow(
                    article=self.article, scheme="FBS", volume=2, sales=10, logistics=40, commission_rate=15
                ),
            ),
        )
        with patch.object(standard_profit, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 20, tzinfo=web.MOSCOW_TIMEZONE)
            report = standard_profit.enrich("tris", snapshot)
        row = report.rows[0]
        self.assertEqual(row.sales, 10)
        self.assertEqual(row.current_profit.retail, 1000)
        self.assertEqual(row.current_profit.purchase, 400)
        self.assertEqual(row.profit.purchase, 400)
        self.assertIsNotNone(row.current_profit.margin)
        self.assertIsNotNone(row.current_profit.roi)
        self.assertIsNotNone(row.profit.margin)
        self.assertIsNotNone(row.profit.roi)

    def test_detail_reads_are_scoped_to_selected_article(self):
        with (
            patch.object(web.unit_economics_1c, "load_product_metrics", return_value={}) as metrics,
            patch.object(web.db, "get_unit_economics_1c_funnel_daily_order_rows", return_value=[]) as orders,
            patch.object(web.db, "get_unit_economics_1c_daily_price_history", return_value=[]),
            patch.object(web.db, "get_daily_stock_history", return_value=[]) as stocks,
        ):
            products, _ = web.load_wb_calculator_products(
                ("tris",), self.today, 7, self.start, self.end,
                include_history=True, detail_store="tris", detail_article=self.article,
            )
        self.assertEqual([p["article"] for p in products], [self.article])
        self.assertEqual(len(metrics.call_args_list), 3)
        for call in metrics.call_args_list:
            self.assertEqual(call.kwargs["article"], self.article)
        self.assertEqual(orders.call_args.kwargs["article"], self.article)
        self.assertEqual(stocks.call_args.kwargs["article"], self.article)

    def test_saved_today_snapshot_preserves_results_without_recalculation(self):
        snapshots = []
        calculate = web.unit_economics_1c_history.calculate_snapshot_row
        def capture(**kwargs):
            result = calculate(**kwargs)
            snapshots.append(result)
            return result
        with patch.object(web.unit_economics_1c_history, "calculate_snapshot_row", side_effect=capture):
            before = self.load(include_inactive=True)
        self.assertEqual(len(snapshots), 1)
        with (
            patch.object(web.db, "get_unit_economics_1c_daily_margin_snapshots", return_value=snapshots),
            patch.object(web.unit_economics_1c_history, "calculate_snapshot_row") as recalculate,
        ):
            after = self.load(include_inactive=True)
        recalculate.assert_not_called()
        self.assertEqual(before, after)
