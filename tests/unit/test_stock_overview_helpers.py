import logging
import unittest
from unittest import mock

from app.web.routers import stock_overview


def row(**changes) -> dict:
    base = {
        "store_slug": "rimili",
        "store_name": "Rimili",
        "store_initials": "RI",
        "store_color": "#000",
        "store_text": "#fff",
        "marketplace": "WB",
        "article": "A",
        "barcode": "bc",
        "name": "Product",
        "in_catalog": True,
        "catalog_id": 1,
        "marketplace_stock": 5,
        "fulfillment_stock": 5,
        "total_stock": 10,
        "sold_30": 10,
        "sold_60": 20,
        "sales_loaded": True,
        "avg_daily": 1.0,
        "coverage_days": 10.0,
        "purchase_price": 100.0,
        "stock_value": 1000.0,
        "stock_updated_at": "2026-08-12T10:00:00+00:00",
        "last_sold_at": "2026-08-11T10:00:00+00:00",
    }
    base.update(changes)
    return base


class StockOverviewHelpersTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_format_status_badge_and_product(self) -> None:
        self.assertEqual(stock_overview._fmt_float(None), "—")
        self.assertEqual(stock_overview._fmt_float(1.25, 2), "1,25")
        self.assertEqual(stock_overview._fmt_money(None), "—")
        self.assertIn("млн", stock_overview._fmt_money(2_000_000))
        self.assertIn("тыс", stock_overview._fmt_money(2_000))
        self.assertIn("₽", stock_overview._fmt_money(20))
        cases = [
            (row(total_stock=0), "danger"),
            (row(sales_loaded=False), "muted"),
            (row(coverage_days=5), "danger"),
            (row(coverage_days=10), "warning"),
            (row(coverage_days=stock_overview.settings.stock_excess_days), "info"),
            (row(coverage_days=None, sold_60=0), "violet"),
            (row(coverage_days=None, avg_daily=0), "warning"),
            (row(coverage_days=30), "ok"),
        ]
        for source, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(stock_overview._stock2_status(source)[0], expected)
        self.assertIn("stock2-badge--danger", stock_overview._stock2_badge("danger", "<bad>"))
        cell = stock_overview._stock2_product_cell(row(name="<Product>"))
        self.assertIn("&lt;Product&gt;", cell)
        self.assertIn("bc", cell)

    def test_load_cache_and_aggregate(self) -> None:
        raw = [
            {
                "store_slug": "rimili",
                "marketplace": "WB",
                "article": "A",
                "barcode": "bc",
                "name": "Product",
                "catalog_id": 1,
                "marketplace_stock": 5,
                "fulfillment_stock": 5,
                "sold_30": 30,
                "sold_60": 40,
                "sales_loaded": True,
                "purchase_price": 100,
                "stock_updated_at": "now",
            }
        ]
        with mock.patch.object(
            stock_overview.stock_dashboard_repository, "get_inventory_rows", return_value=raw
        ):
            loaded = stock_overview._stock2_load_inventory_rows()
        self.assertEqual(loaded[0]["total_stock"], 10)
        self.assertEqual(loaded[0]["stock_value"], 1000)
        self.assertGreater(loaded[0]["avg_daily"], 0)
        stock_overview._stock2_cache.update({"rows": None, "expires_at": 0.0})
        with mock.patch.object(stock_overview, "_stock2_load_inventory_rows", return_value=loaded) as loader:
            self.assertIs(stock_overview._stock2_inventory_rows(), loaded)
            self.assertIs(stock_overview._stock2_inventory_rows(), loaded)
        loader.assert_called_once()

        sources = [
            row(article="risk", coverage_days=5),
            row(article="zero", total_stock=0, marketplace_stock=0, fulfillment_stock=0, sold_30=5),
            row(article="excess", coverage_days=stock_overview.settings.stock_excess_days + 1),
            row(article="frozen", coverage_days=None, sold_60=0),
            row(article="nodata", sales_loaded=False, coverage_days=None, stock_value=None),
        ]
        aggregate = stock_overview._stock2_aggregate(sources, "store_slug")["rimili"]
        self.assertEqual(aggregate["sku_count"], 5)
        self.assertEqual(aggregate["risk_count"], 1)
        self.assertEqual(aggregate["zero_selling_count"], 1)
        self.assertEqual(aggregate["excess_count"], 1)
        self.assertEqual(aggregate["frozen_count"], 1)
        self.assertEqual(aggregate["no_data_count"], 1)

    def test_aggregate_status_renderers_tables_splits_and_pagination(self) -> None:
        statuses = [
            ({"sku_count": 1, "total_stock": 0}, "danger"),
            ({"sku_count": 1, "total_stock": 1, "risk_count": 1}, "danger"),
            ({"sku_count": 1, "total_stock": 1, "zero_selling_count": 1}, "danger"),
            ({"sku_count": 1, "total_stock": 1, "excess_count": 1}, "info"),
            ({"sku_count": 1, "total_stock": 1, "frozen_count": 1}, "violet"),
            ({"sku_count": 1, "total_stock": 1, "no_data_count": 1}, "muted"),
            ({"sku_count": 2, "total_stock": 1, "no_data_count": 1}, "muted"),
            ({"sku_count": 1, "total_stock": 1}, "ok"),
        ]
        for item, expected in statuses:
            self.assertEqual(stock_overview._stock2_aggregate_status(item)[0], expected)
        self.assertIn("stock2-kpi", stock_overview._stock2_summary_card("L", "V", "N", "blue"))
        self.assertIn(
            "href",
            stock_overview._stock2_attention_card("T", "1", "N", "danger", "/x", "Details"),
        )
        sources = [
            row(article="risk", coverage_days=5),
            row(article="zero", total_stock=0, marketplace_stock=0, fulfillment_stock=0, sold_30=5),
            row(article="excess", coverage_days=stock_overview.settings.stock_excess_days + 1),
            row(article="frozen", coverage_days=None, sold_60=0, stock_value=2000),
        ]
        store_data = stock_overview._stock2_aggregate(sources, "store_slug")
        market_data = stock_overview._stock2_aggregate(sources, "marketplace")
        self.assertIn("WB", stock_overview._stock2_render_marketplaces(market_data))
        self.assertIn("stock2-bar", stock_overview._stock2_render_bars(store_data, ["rimili"]))
        self.assertIn("RIMILI", stock_overview._stock2_render_stores(store_data, ["rimili"]))
        self.assertIn("stock2-empty", stock_overview._stock2_table_rows([]))
        self.assertIn("stock2-badge", stock_overview._stock2_table_rows(sources))
        self.assertIn("2026", stock_overview._stock2_table_rows(sources, frozen=True, limit=None))
        split = stock_overview._stock2_split_rows(sources)
        self.assertTrue(all(len(split[key]) == 1 for key in ("ending-7", "zero", "excess", "frozen")))
        self.assertEqual(stock_overview._stock2_pagination("zero", 1, 1), "")
        pagination = stock_overview._stock2_pagination(
            "zero", 1, stock_overview.settings.stock_detail_page_size + 1
        )
        self.assertIn("is-disabled", pagination)
        self.assertIn("page=2", pagination)
        last = stock_overview._stock2_pagination(
            "zero", 2, stock_overview.settings.stock_detail_page_size + 1
        )
        self.assertIn("page=1", last)


if __name__ == "__main__":
    unittest.main()
