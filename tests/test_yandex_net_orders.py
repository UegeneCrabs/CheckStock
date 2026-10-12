"""Cancellations affect every YM economic basis, without changing source counters."""

import copy
import json
import unittest
from datetime import date
from unittest.mock import patch

from app.economics.daily_calculation import calculate
from app.economics.yandex import reports
from app.infrastructure.database import Database
from app.repositories import daily_economics as ledger
from app.yandex import economics, economics_advertising as advertising
from app.yandex import economics_history
from app.yandex.economics_calculation import aggregate
from app.yandex.economics_days import resolve_day
from app.yandex.order_basis import drr_percent, net_amount, net_count


DAY = "2026-10-11"
VALUES = dict(
    seller_price=1000, buyer_price=900, purchase_price=100, fulfillment_cost=0,
    commission_percent=0, payment_acceptance=0, acquiring_percent=0,
    delivery_cost=0, middle_mile=0, logistics_returns=0, transit_cost=0, company_commission_percent=0,
    vat_percent=0, usn_percent=0, loss_percent=0, disposal_cost=0, buyout_percent=80,
)


class YandexNetOrdersTests(unittest.TestCase):
    def test_all_cancelled_keeps_ad_loss_without_unit_inputs(self):
        values, result = calculate("YANDEX MARKET", dict(orders_count=1, cancel_count=1, advertising_spend=33.9))
        self.assertEqual(values["orders_count"], 1)
        self.assertEqual(values["net_orders_count"], 0)
        self.assertEqual(result["expected_buyouts"], 0)
        self.assertEqual(result["purchase_value"], 0)
        self.assertEqual(result["day_profit"], -33.9)
        self.assertTrue(result["daily_complete"])

    def test_partial_cancellations_unit_ads_and_period(self):
        saved = {"inputs": VALUES}
        order = dict(orders_count=3, cancel_count=1)
        ad = dict(spend=80)
        day = resolve_day(DAY, saved, order, ad, True, True)
        self.assertEqual(day["expected_buyouts"], 1.6)
        self.assertEqual(day["profit"], 1360)
        self.assertEqual(day["purchase_value"], 160)
        row = reports.daily_row(DAY, day, order, ad, True, True, 80)
        self.assertEqual(row["orders_count"], 3)
        self.assertEqual(row["net_orders_count"], 2)
        self.assertEqual(row["advertising_per_unit"], 50)
        self.assertEqual(row["net_profit"], 850)
        self.assertEqual(aggregate([day], [DAY])["roi"], 850)

    def test_all_cancelled_report_has_no_unit_profit_or_roi(self):
        order, ad = dict(orders_count=1, cancel_count=1), dict(spend=33.9)
        day = resolve_day(DAY, {"inputs": VALUES}, order, ad, True, True)
        row = reports.daily_row(DAY, day, order, ad, True, True, 80)
        self.assertEqual(row["day_profit"], -33.9)
        self.assertIsNone(row["net_profit"])
        self.assertIsNone(row["net_revenue"])
        self.assertIsNone(aggregate([day], [DAY])["roi"])

    def test_manual_gross_orders_override_is_preserved_and_net_not_subtracted_twice(self):
        saved = {"inputs": VALUES, "overrides": {"orders_count": 4, "advertising_spend": 20}}
        order = dict(orders_count=3, cancel_count=1)
        first = resolve_day(DAY, saved, order, {}, True, True)
        second = resolve_day(DAY, first, order, {}, True, True)
        self.assertEqual(first, second)
        self.assertEqual(second["orders_count"], 4)
        self.assertEqual(second["net_orders_count"], 3)
        self.assertEqual(second["expected_buyouts"], 2.4)

    def test_missing_orders_stay_unknown(self):
        day = resolve_day(DAY, {"inputs": VALUES}, {}, {"spend": 20}, False, True)
        self.assertIsNone(day["net_orders_count"])
        self.assertIsNone(day["expected_buyouts"])
        self.assertIsNone(day["profit"])
        self.assertIn("orders_count", day["missing"])

    def test_wb_does_not_subtract_cancellations(self):
        _, result = calculate("WB", dict(orders_count=3, cancel_count=1, buyout_percent=80, advertising_spend=20))
        self.assertEqual(result["expected_buyouts"], 2.4)

    def test_net_basis_and_zero_drr(self):
        row = dict(orders_count=3, cancel_count=1, orders_amount=3000, cancel_amount=1200)
        self.assertEqual(net_count(row), 2)
        self.assertEqual(net_amount(row), 1800)
        self.assertEqual(drr_percent(144, net_amount(row) * .8), 10)
        self.assertIsNone(drr_percent(33.9, 0))
        self.assertEqual(drr_percent(0, 0), 0)
        self.assertIsNone(drr_percent(None, 0))
        self.assertEqual(net_count(dict(orders_count=1, cancel_count=2)), 0)

    def test_weekly_drr_and_calculator_use_matching_net_orders(self):
        ads = [dict(day=DAY, article="sku", spend=144)]
        orders = [dict(day=DAY, article="sku", orders_count=3, cancel_count=1, orders_amount=3000, cancel_amount=1200),
                  dict(day="2026-10-10", article="sku", orders_count=100, orders_amount=100000)]
        with patch.object(advertising.metrics, "get_history", side_effect=[(ads, {DAY}), (orders, {DAY, "2026-10-10"})]):
            history = advertising.weekly_history("tris", date(2026, 10, 12))
        weekly = advertising.product_drr(history, "sku", 80)
        self.assertEqual(weekly["orders_count"], 2)
        self.assertEqual(weekly["orders_amount"], 1800)
        self.assertEqual(weekly["drr"], 10)
        values = dict(VALUES)
        advertising.apply_calculator_drr(values, {}, weekly, None)
        self.assertEqual(values["advertising_per_buyout"], 90)

    def test_current_day_deducts_cancellations(self):
        state = dict(values=dict(VALUES), origins={}, pricing={}, tariff={})
        with patch.object(economics, "effective", return_value=copy.deepcopy(state)), \
             patch.object(economics, "current_inputs", return_value=copy.deepcopy(state)), \
             patch.object(economics, "product_drr", return_value={"orders_count": 0, "days": 0, "drr": None}), \
             patch.object(ledger, "get", return_value=None):
            result = economics.detail("tris", "sku", today=date(2026, 10, 12),
                state_cache={"advertising_week": {"period_to": DAY}}, include_history=False,
                daily_metrics={"orders": 1, "cancel_count": 1, "spend": 33.9, "issues": []})
        self.assertEqual(result["orders_count"], 1)
        self.assertEqual(result["net_orders_count"], 0)
        self.assertEqual(result["day_result"]["day_profit"], -33.9)
        self.assertIsNone(result["result"]["roi"])

    def test_chart_and_calculator_history_share_the_net_basis(self):
        saved = dict(article="sku", day=DAY, scheme="COMMON", data={"day": DAY, "inputs": VALUES})
        order = dict(article="sku", day=DAY, orders_count=1, cancel_count=1, orders_amount=1000, cancel_amount=1000)
        def history(store, source, start, end):
            return ([order] if source == "orders" else [dict(article="sku", day=DAY, spend=33.9)]), {DAY}
        with patch.object(economics_history.repository, "history", return_value=[saved]), \
             patch.object(economics_history.metrics, "get_history", side_effect=history), \
             patch.object(economics_history.stock_history, "get_daily_stock_history", return_value=[]), \
             patch.object(economics_history.catalog, "get_stock_items", return_value=[]), \
             patch.object(economics, "context", return_value={"sources": {}}), \
             patch.object(economics, "current_inputs", return_value={"values": VALUES}):
            result = economics_history.product_history("tris", "sku", "COMMON", today=date(2026, 10, 12))
        self.assertEqual(result["history"][0]["net_orders_count"], 0)
        self.assertEqual(result["history"][0]["profit"], -33.9)
        point = next(r for r in result["chart"] if r["date"] == DAY)
        self.assertEqual(point["orders_count"], 0)
        self.assertEqual(point["turnover_rub"], 0)
        self.assertEqual(point["margin_rub"], -33.9)
        self.assertIsNone(point["drr_percent"])

    def test_report_totals_do_not_invent_drr_for_zero_turnover(self):
        row = dict(orders_count=1, orders_amount=1000, cancel_count=1, cancel_amount=1000,
                   net_orders_count=0, net_orders_amount=0, expected_buyout_amount=0,
                   advertising_spend=33.9, margin=-33.9, purchase_value=0, roi_purchase_value=0,
                   stock=0, stock_fbs=0, stock_fbo=0, stock_fulfillment=0)
        result = reports.totals([row])
        self.assertEqual(result["margin"], -33.9)
        self.assertIsNone(result["drr"])
        self.assertIsNone(result["roi"])

    def test_report_period_uses_net_money_and_counts_but_displays_gross(self):
        identity = dict(store_slug="tris", article="sku", subject="test", name="test")
        product = {**identity, "stock": dict(total=10, fbs=5, fbo=5, fulfillment=0, days=5, average_daily_orders=2), "advertising": {}}
        order = dict(article="sku", day=DAY, orders_count=3, cancel_count=1, orders_amount=3000, cancel_amount=1200)
        ad = dict(article="sku", day=DAY, spend=144)
        def history(store, source, start, end):
            return [order if source == "orders" else ad], {DAY}
        with patch.object(reports, "catalog", return_value=[identity]), \
             patch.object(reports, "load_products", return_value=[product]), \
             patch.object(reports, "get_products_with_stock_history", return_value=set()), \
             patch.object(economics, "context", return_value={}), \
             patch.object(economics, "effective", return_value={"values": VALUES}), \
             patch.object(reports.metrics, "get_history", side_effect=history), \
             patch.object(reports.repository, "history", return_value=[dict(article="sku", day=DAY, scheme="COMMON", data={"inputs": VALUES, "day": DAY})]):
            row = reports.load_rows(["tris"], {}, date(2026, 10, 11), date(2026, 10, 11), today=date(2026, 10, 12), include_details=True)[0]
        self.assertEqual(row["orders_count"], 3)
        self.assertEqual(row["net_orders_count"], 2)
        self.assertEqual(row["orders_amount"], 3000)
        self.assertEqual(row["expected_buyout_amount"], 1440)
        self.assertEqual(row["margin_orders_count"], 1.6)
        self.assertEqual(row["drr"], 10)
        self.assertEqual(reports.totals([row])["drr"], 10)


class CancellationSyncTests(unittest.TestCase):
    def setUp(self):
        self.database = Database(url="sqlite+pysqlite:///:memory:")
        self.addCleanup(self.database.dispose)
        patcher = patch.object(ledger, "get_connection", self.database.connect)
        patcher.start()
        self.addCleanup(patcher.stop)
        with self.database.connect() as conn:
            conn.execute("CREATE TABLE economics_daily (marketplace TEXT,store_slug TEXT,article TEXT,day TEXT,revision INTEGER,payload_json TEXT,updated_at TEXT,PRIMARY KEY(marketplace,store_slug,article,day))")
            conn.execute("CREATE TABLE economics_daily_events (id TEXT,marketplace TEXT,store_slug TEXT,article TEXT,day TEXT,revision INTEGER,payload_json TEXT,created_at TEXT,actor TEXT)")
            conn.execute("CREATE TABLE yandex_economics_daily (store_slug TEXT,article TEXT,scheme TEXT,day TEXT,payload_json TEXT,captured_at TEXT)")
            source = ledger.observation({**VALUES, "orders_count": 1, "advertising_spend": 33.9}, raw={}, version=15)
            self.original = copy.deepcopy(source)
            self.key = ("YANDEX MARKET", "tris", "sku", DAY)
            payload = ledger.resolved("YANDEX MARKET", source, {"purchase_price": 120})
            conn.execute("INSERT INTO economics_daily VALUES (?,?,?,?,?,?,?)", (*self.key, 1, json.dumps(payload), "now"))
            conn.commit()

    def test_cancellation_only_sync_preserves_prices_overrides_original_and_is_idempotent(self):
        orders = [dict(article="sku", day=DAY, orders_count=1, cancel_count=1)]
        ads = [dict(article="sku", day=DAY, spend=33.9)]
        def history(store, source, start, end):
            return (orders if source == "orders" else ads), {DAY}
        with patch.object(economics.repository, "sources", return_value={}), \
             patch.object(economics.metrics, "get_history", side_effect=history):
            self.assertEqual(economics.close_days(["tris"], today=date(2026, 10, 12)), {"closed": 1})
            self.assertEqual(economics.close_days(["tris"], today=date(2026, 10, 12)), {"closed": 0})
        saved = ledger.get(self.key)
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["overrides"], {"purchase_price": 120})
        self.assertEqual(saved["original"], self.original)
        self.assertEqual(saved["values"]["seller_price"], 1000)
        self.assertEqual(saved["result"]["day_profit"], -33.9)
        with self.database.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM economics_daily_events").fetchone()["n"], 1)

    def test_wb_rejects_cancellation_source_update(self):
        with self.assertRaises(ValueError):
            ledger.refresh_metrics(("WB", *self.key[1:]), {"cancel_count": 1}, {})


if __name__ == "__main__":
    unittest.main()
