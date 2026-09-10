import json
import tempfile
import unittest
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest import mock

from app import db, sales, sync_settings, unit_economics_yandex
from app.repositories import core, yandex_assortment
from app.repositories import unit_economics_yandex as repository
from app.yandex import unit_economics_sync as sync

TODAY = date(2026, 9, 9)
NOW = "2026-09-09T08:00:00+00:00"
MARKETPLACE = "YANDEX MARKET"


def daily(article="YM-1", day="2026-09-08", count=10, amount=10000, cancelled=2, sold=6):
    return {
        "article": article,
        "day": day,
        "orders_count": count,
        "orders_amount": amount,
        "cancel_count": cancelled,
        "cancel_amount": cancelled * 1000,
        "sold_count": sold,
    }


class YandexMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(core, "DB_PATH", Path(self.temp.name) / "yandex.db")
        patch.start()
        self.addCleanup(patch.stop)
        assortment_patch = mock.patch.object(
            yandex_assortment, "load_active_products",
            return_value={("rimili", "YM-1"), ("rimili", "YM-zero"), ("tris", "YM-1")},
        )
        assortment_patch.start()
        self.addCleanup(assortment_patch.stop)
        db.init_db()
        db.replace_catalog(
            "rimili", MARKETPLACE, [{"article": "YM-1", "mp_sku": "123"}, {"article": "YM-zero"}], NOW
        )

    def save(self, source, rows, start="2026-09-02", end="2026-09-08", store="rimili"):
        repository.save_snapshot(store, source, rows, start, end, NOW)

    def products(self, store="rimili"):
        return {row["article"]: row for row in unit_economics_yandex.load_products((store,), today=TODAY)}

    def test_stock_turnover_buyout_and_weighted_ads_use_exact_period(self):
        db.upsert_mp_stock("rimili", "YM-1", MARKETPLACE, "fbs", 10, NOW)
        db.upsert_mp_stock("rimili", "YM-1", MARKETPLACE, "fbo", 20, NOW)
        db.upsert_ff_stock("rimili", "YM-1", "FF-a", 30, NOW, MARKETPLACE)
        db.upsert_ff_stock("rimili", "YM-1", "FF-b", 40, NOW, MARKETPLACE)
        db.upsert_mp_stock("rimili", "YM-1", "WB", "fbs", 9999, NOW)
        db.upsert_ff_stock("rimili", "YM-1", "FF-a", 9999, NOW, "WB")
        self.save(
            "orders",
            [
                daily(),
                daily(day="2026-08-20", count=6, amount=6000),
                daily(day="2026-09-09", count=5, amount=5000),
            ],
            "2026-08-20",
            "2026-09-09",
        )
        self.save("advertising", [{"article": "YM-1", "spend": 750, "impressions": 2000, "clicks": 50}])
        self.save("reputation", [{"sku": "123", "rating": 4.8, "reviews_count": 125}])
        product = self.products()["YM-1"]
        self.assertEqual(
            [product["stock"][key] for key in ("total", "fbs", "fbo", "fulfillment", "days")],
            [100, 10, 20, 70, 100],
        )
        self.assertEqual(product["stock"]["orders_21d"], 21)
        self.assertEqual(product["economics_7d"]["turnover"], 8000)
        self.assertEqual(product["economics_7d"]["turnover_coverage"], {
            "dates": [f"2026-09-{day:02d}" for day in range(2, 9)],
            "days": 7,
            "expected_days": 7,
            "complete": True,
            "period_from": "2026-09-02",
            "period_to": "2026-09-08",
            "missing_dates": [],
        })
        self.assertEqual(product["advertising"]["orders_amount"], 10000)
        self.assertEqual(product["advertising"]["buyout_percent"], 75)
        self.assertEqual(
            [product["advertising"][key] for key in ("drr", "spend", "ctr", "cpc")], [10, 750, 2.5, 15]
        )
        self.assertEqual((product["rating"], product["reviews_count"]), (4.8, 125))
        self.assertIsNone(product["economics_7d"]["margin"])
        self.assertTrue(all(value is None for value in product["current_economics"].values()))
        self.assertEqual(self.products()["YM-zero"]["economics_7d"]["turnover"], 0)
        self.assertEqual(self.products()["YM-zero"]["stock"]["days"], 0)

    def test_no_data_differs_from_successful_empty_reports(self):
        missing = self.products()["YM-1"]
        self.assertEqual(missing["stock"]["total"], 0)
        self.assertIsNone(missing["stock"]["days"])
        self.assertIsNone(missing["economics_7d"]["turnover"])
        self.assertIsNone(missing["economics_7d"]["turnover_coverage"])
        self.assertIsNone(missing["advertising"]["spend"])
        self.save("orders", [], "2026-08-20", "2026-09-09")
        self.save("advertising", [])
        empty = self.products()["YM-1"]
        self.assertEqual(empty["stock"]["days"], 0)
        self.assertEqual(empty["economics_7d"]["turnover"], 0)
        self.assertEqual(empty["economics_7d"]["turnover_coverage"]["dates"],
                         [f"2026-09-{day:02d}" for day in range(2, 9)])
        self.assertTrue(empty["economics_7d"]["turnover_coverage"]["complete"])
        self.assertEqual([empty["advertising"][key] for key in ("drr", "spend", "ctr", "cpc")], [0, 0, 0, 0])
        self.assertIsNone(empty["rating"])

    def test_outdated_ad_report_is_not_mislabeled_as_current_week(self):
        self.save("advertising", [{"article": "YM-1", "spend": 999}], "2026-09-01", "2026-09-07")
        self.save("reputation", [{"sku": "YM-1", "rating": 4, "reviews_count": 0}])
        repository.record_error("rimili", "reputation", "temporary failure", NOW)
        product = self.products()["YM-1"]
        self.assertIsNone(product["advertising"]["spend"])
        self.assertEqual((product["rating"], product["reviews_count"]), (4, 0))
        self.assertEqual(repository.get_snapshots("rimili")["advertising"]["data"][0]["spend"], 999)

    def test_local_orders_are_read_without_duplication_or_cross_store_data(self):
        raw = [
            {
                "id": 1,
                "creationDate": "2026-09-08T10:00:00+03:00",
                "status": "DELIVERED",
                "items": [{"offerId": "YM-1", "count": 2, "prices": {"payment": 2000}}],
            }
        ]
        lines = sales._normalize_yandex("rimili", raw)
        db.upsert_sales_order_lines(lines, NOW)
        db.record_sales_sync("rimili", MARKETPLACE, True, None, len(lines), 21, NOW)
        before = repository.get_daily_orders("rimili", "2026-08-20", "2026-09-10")
        local_product = self.products()["YM-1"]
        self.assertEqual(local_product["economics_7d"]["turnover"], 2000)
        self.assertEqual(local_product["economics_7d"]["turnover_coverage"]["days"], 7)
        self.save("orders", [daily()], "2026-08-20", "2026-09-09")
        self.save("orders", [daily(amount=99999)], "2026-08-20", "2026-09-09", store="tris")
        self.assertEqual(self.products()["YM-1"]["economics_7d"]["turnover"], 8000)
        self.assertEqual(repository.get_daily_orders("rimili", "2026-08-20", "2026-09-10"), before)

    def test_seven_day_refresh_replaces_days_and_keeps_history_for_calculations(self):
        db.upsert_mp_stock("rimili", "YM-1", MARKETPLACE, "fbs", 100, NOW)
        old_day = daily(day="2026-08-20", count=6, amount=6000, cancelled=0)
        closed_day = daily(day="2026-09-02", count=4, amount=4000, cancelled=0)
        self.save("orders", [old_day, closed_day, daily(), daily(article="YM-zero")], "2026-08-20", "2026-09-09")
        fresh = daily(count=11, amount=11000, cancelled=1)
        for _ in range(2):
            self.save("orders", [fresh], "2026-09-03", "2026-09-09")
            snapshot = repository.get_snapshots("rimili")["orders"]
            self.assertEqual(snapshot["data"], [old_day, closed_day, fresh])
            self.assertEqual((snapshot["period_from"], snapshot["period_to"]), ("2026-08-20", "2026-09-09"))
            products = self.products()
            self.assertEqual(products["YM-1"]["stock"]["orders_21d"], 21)
            self.assertEqual(products["YM-1"]["stock"]["days"], 100)
            self.assertEqual(products["YM-1"]["economics_7d"]["turnover"], 14000)
            self.assertEqual(products["YM-zero"]["economics_7d"]["turnover"], 0)
        # A successful empty response clears all refreshed days, without restoring stale orders.
        self.save("orders", [], "2026-09-03", "2026-09-09")
        self.assertEqual(repository.get_snapshots("rimili")["orders"]["data"], [old_day, closed_day])
        self.assertEqual(self.products()["YM-1"]["economics_7d"]["turnover"], 4000)

    def test_next_day_refresh_retains_closed_week_and_trims_only_expired_history(self):
        expired = daily(day="2026-08-20")
        oldest = daily(day="2026-08-21")
        closed = daily(day="2026-09-03")
        self.save("orders", [expired, oldest, closed, daily()], "2026-08-20", "2026-09-09")
        self.save("orders", [], "2026-09-04", "2026-09-10")
        snapshot = repository.get_snapshots("rimili")["orders"]
        self.assertEqual(snapshot["data"], [oldest, closed])
        self.assertEqual((snapshot["period_from"], snapshot["period_to"]), ("2026-08-21", "2026-09-10"))
        product = unit_economics_yandex.load_products(("rimili",), article="YM-1", today=date(2026, 9, 10))[0]
        self.assertEqual(product["stock"]["orders_21d"], 20)
        self.assertEqual(product["economics_7d"]["turnover"], 8000)

    def test_refresh_does_not_mark_gaps_between_windows_as_loaded(self):
        self.save("orders", [], "2026-08-20", "2026-08-26")
        self.save("orders", [], "2026-09-03", "2026-09-09")
        snapshot = repository.get_snapshots("rimili")["orders"]
        self.assertEqual((snapshot["period_from"], snapshot["period_to"]), ("2026-09-03", "2026-09-09"))
        self.assertIsNone(self.products()["YM-1"]["economics_7d"]["turnover"])
        self.assertIsNone(self.products()["YM-1"]["economics_7d"]["turnover_coverage"])
        self.assertIsNone(self.products()["YM-1"]["stock"]["days"])

    def test_short_snapshot_combines_with_older_local_orders_without_reusing_refreshed_days(self):
        raw = [
            {
                "id": number,
                "creationDate": day + "T10:00:00+03:00",
                "status": "DELIVERED",
                "items": [{"offerId": "YM-1", "count": count, "prices": {"payment": count * 1000}}],
            }
            for number, day, count in (
                (1, "2026-08-20", 6),
                (2, "2026-09-02", 4),
                (3, "2026-09-08", 20),
                (4, "2026-09-09", 30),
            )
        ]
        db.upsert_sales_order_lines(sales._normalize_yandex("rimili", raw), NOW)
        db.record_sales_sync("rimili", MARKETPLACE, True, None, len(raw), 21, "2026-09-08T08:00:00+00:00")
        before = repository.get_daily_orders("rimili", "2026-08-20", "2026-09-10")
        self.save("orders", [daily()], "2026-09-03", "2026-09-09")
        product = self.products()["YM-1"]
        self.assertEqual(product["stock"]["orders_21d"], 20)
        self.assertEqual(product["economics_7d"]["turnover"], 12000)
        self.assertEqual(product["advertising"]["orders_amount"], 14000)
        self.assertEqual(repository.get_daily_orders("rimili", "2026-08-20", "2026-09-10"), before)

    def test_sync_failures_preserve_successful_snapshot_and_isolate_sources(self):
        self.save("orders", [], "2026-08-20", "2026-09-08")
        self.save("advertising", [{"article": "YM-1", "spend": 750, "impressions": 2000, "clicks": 50}])
        with (
            mock.patch.object(sync.tokens, "has_credentials", return_value=True),
            mock.patch.object(sync.tokens, "get_api_key", return_value="test-key"),
            mock.patch.object(sync, "resolve_business_id", return_value=123),
            mock.patch.object(sync, "load_orders", return_value=[daily()]),
            mock.patch.object(
                sync, "load_reputation", return_value=[{"sku": "123", "rating": 4.5, "reviews_count": 12}]
            ),
            mock.patch.object(sync, "load_advertising", side_effect=RuntimeError("unavailable")),
        ):
            result = sync.sync_store("rimili", "advertising", TODAY)
            orders_result = sync.sync_store("rimili", "orders", TODAY)
        self.assertFalse(result["ok"])
        self.assertTrue(orders_result["ok"])
        snapshots = repository.get_snapshots("rimili")
        self.assertNotIn("reputation", snapshots)
        self.assertEqual(snapshots["advertising"]["data"][0]["spend"], 750)
        self.assertEqual(snapshots["advertising"]["error"], "unavailable")
        self.assertEqual(self.products()["YM-1"]["advertising"]["drr"], 10)

    def test_new_cabinet_waits_for_complete_history_for_each_metric(self):
        self.save("orders", [daily()], "2026-09-03", "2026-09-09")
        self.save("advertising", [{"article": "YM-1", "spend": 750, "impressions": 2000, "clicks": 50}])
        product = self.products()["YM-1"]
        self.assertIsNone(product["stock"]["days"])
        self.assertIsNone(product["stock"]["orders_21d"])
        self.assertIsNone(product["economics_7d"]["turnover"])
        self.assertIsNone(product["advertising"]["drr"])
        self.assertEqual(product["advertising"]["spend"], 750)
        self.save("orders", [daily()], "2026-09-04", "2026-09-10")
        product = unit_economics_yandex.load_products(("rimili",), article="YM-1", today=date(2026, 9, 10))[0]
        self.assertIsNone(product["stock"]["days"])
        self.assertEqual(product["economics_7d"]["turnover"], 8000)

    def test_each_source_updates_only_its_own_snapshot(self):
        rows_by_source = {
            "orders": [daily()],
            "reputation": [{"sku": "123", "rating": 4.5, "reviews_count": 12}],
            "advertising": [{"article": "YM-1", "spend": 750, "impressions": 2000, "clicks": 50}],
        }
        for source in sync.SOURCES:
            with (
                self.subTest(source=source),
                mock.patch.object(sync.tokens, "has_credentials", return_value=True),
                mock.patch.object(sync.tokens, "get_api_key", return_value="test-key"),
                mock.patch.object(sync, "resolve_business_id", return_value=123),
                mock.patch.object(sync, "load_orders", return_value=rows_by_source["orders"]) as orders,
                mock.patch.object(
                    sync, "load_reputation", return_value=rows_by_source["reputation"]
                ) as reputation,
                mock.patch.object(
                    sync, "load_advertising", return_value=rows_by_source["advertising"]
                ) as advertising,
            ):
                before = repository.get_snapshots("rimili")
                result = sync.sync_store("rimili", source, TODAY)
                after = repository.get_snapshots("rimili")
                self.assertEqual(result, {"ok": True, "source": source, "rows": 1})
                self.assertEqual(after[source]["data"], rows_by_source[source])
                for name, loader in zip(sync.SOURCES, (orders, reputation, advertising), strict=True):
                    self.assertEqual(loader.call_count, int(name == source))
                    if name != source:
                        self.assertEqual(after.get(name), before.get(name))
                if source == "orders":
                    orders.assert_called_once_with("rimili", "test-key", 123, date(2026, 9, 3), TODAY)
                elif source == "reputation":
                    reputation.assert_called_once_with("test-key", 123)
                else:
                    advertising.assert_called_once_with("test-key", 123, date(2026, 9, 2), date(2026, 9, 8))

    def test_credentials_failure_only_marks_the_requested_source(self):
        self.save("orders", [daily()], "2026-08-20", "2026-09-09")
        self.save("advertising", [])
        before = repository.get_snapshots("rimili")
        with (
            mock.patch.object(sync.tokens, "has_credentials", return_value=True),
            mock.patch.object(sync.tokens, "get_api_key", side_effect=ValueError("invalid key")),
        ):
            result = sync.sync_store("rimili", "reputation", TODAY)
        self.assertFalse(result["ok"])
        after = repository.get_snapshots("rimili")
        self.assertEqual(after["orders"], before["orders"])
        self.assertEqual(after["advertising"], before["advertising"])
        self.assertEqual(after["reputation"]["error"], "invalid key")

    def test_split_settings_preserve_legacy_switches_and_stay_independent(self):
        legacy = "yandex_unit_economics_sync"
        names = ("yandex_orders_sync", "yandex_reputation_sync", "yandex_advertising_sync")
        db.set_sync_job_setting(legacy, "", "", False, NOW)
        db.set_sync_job_setting(legacy, "", MARKETPLACE, True, NOW)
        db.set_sync_job_setting(legacy, "tris", MARKETPLACE, False, NOW)
        db.set_sync_job_setting(names[1], "", "", True, NOW)
        db.init_db()
        for name in names:
            rows = {
                (row["store_slug"], row["marketplace"]): row["enabled"]
                for row in db.list_sync_job_settings(name)
            }
            self.assertEqual(bool(rows[("", "")]), name == names[1])
            self.assertTrue(rows[("", MARKETPLACE)])
            self.assertFalse(rows[("tris", MARKETPLACE)])
        sync_settings.save_setting(names[0], enabled=True)
        sync_settings.save_setting(names[0], enabled=True, store_slug="tris", marketplace=MARKETPLACE)
        db.init_db()
        self.assertIn("tris", sync_settings.enabled_stores(names[0], MARKETPLACE))
        self.assertNotIn("tris", sync_settings.enabled_stores(names[1], MARKETPLACE))
        self.assertEqual(sync_settings.enabled_stores(names[2], MARKETPLACE), ())


class YandexReportTests(unittest.TestCase):
    def test_scheduled_sync_skips_cabinets_without_credentials(self):
        with (
            mock.patch.object(sync.tokens, "has_credentials", side_effect=lambda slug: slug == "tris"),
            mock.patch.object(sync, "sync_store", return_value={"ok": True}) as sync_store,
        ):
            self.assertEqual(sync.sync_all("orders", ("rimili", "tris")), {"tris": {"ok": True}})
        sync_store.assert_called_once_with("tris", "orders")

    def test_advertising_uses_promoted_traffic_and_both_cost_sources(self):
        boost = [
            {
                "shopSku": "A",
                "billedAmount": "90,5",
                "showsWithFee": 100,
                "clicksVendorWithFee": 5,
                "shows": 99999,
                "clicksVendor": 99999,
            }
        ]
        shows = [
            {"offerId": "A", "cost": 9.5, "shows": 900, "clicks": 5},
            {"offerId": "B", "cost": 10, "shows": 20, "clicks": 2},
        ]
        with mock.patch.object(sync, "load_report", side_effect=[boost, shows]) as report:
            rows = sync.load_advertising("key", 1, date(2026, 9, 2), date(2026, 9, 8))
        self.assertEqual(rows[0], {"article": "A", "spend": 100, "impressions": 1000, "clicks": 10})
        self.assertEqual(report.call_args_list[1].args[2]["attributionType"], "CLICKS")
        with mock.patch.object(sync, "load_report", side_effect=[boost, RuntimeError("failed")]):
            with self.assertRaises(RuntimeError):
                sync.load_advertising("key", 1, TODAY, TODAY)

    def test_advertising_keeps_spend_and_impressions_when_clicks_are_null(self):
        shows = [{"offerId": "A", "cost": 36.39, "shows": 58, "clicks": None}]
        with mock.patch.object(sync, "load_report", side_effect=[[], shows]):
            rows = sync.load_advertising("key", 1, TODAY, TODAY)
        self.assertEqual(rows, [{"article": "A", "spend": 36.39, "impressions": 58, "clicks": 0}])

    def test_advertising_rejects_missing_clicks_or_invalid_metrics(self):
        valid = {"offerId": "A", "cost": 36.39, "shows": 58, "clicks": None}
        missing_clicks = {key: value for key, value in valid.items() if key != "clicks"}
        invalid_rows = [
            missing_clicks,
            {**valid, "cost": None},
            {**valid, "shows": None},
            {**valid, "clicks": "invalid"},
            {**valid, "clicks": -1},
        ]
        for row in invalid_rows:
            with self.subTest(row=row), mock.patch.object(sync, "load_report", side_effect=[[], [row]]):
                with self.assertRaises((KeyError, ValueError)):
                    sync.load_advertising("key", 1, TODAY, TODAY)

    def test_reputation_uses_api_rating_and_deduplicates_campaign_rows(self):
        row = {"sku": "00123", "rating": 4.7, "currentOpinionCount": 80}
        with mock.patch.object(sync, "load_report", return_value=[row, row]):
            self.assertEqual(
                sync.load_reputation("key", 1), [{"sku": "00123", "rating": 4.7, "reviews_count": 80}]
            )

    def test_download_only_reads_product_sheet(self):
        content = BytesIO()
        with zipfile.ZipFile(content, "w") as archive:
            archive.writestr(
                "business_shows_boost_consolidated_offers.json",
                json.dumps({"rows": [{"offerId": "A", "cost": 12}]}),
            )
            archive.writestr(
                "business_shows_boost_consolidated_campaigns.json",
                json.dumps([{"offerId": "A", "cost": 999}]),
            )
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = content.getvalue()
        with mock.patch.object(sync.urllib.request, "urlopen", return_value=response):
            self.assertEqual(
                sync.download_report(
                    "https://example.test/report", "business_shows_boost_consolidated_offers", "offerId"
                ),
                [{"offerId": "A", "cost": 12}],
            )
            with self.assertRaises(ValueError):
                sync.download_report("https://example.test/report", "missing", "offerId")

    def test_report_generation_polling_and_failure(self):
        with (
            mock.patch.object(
                sync.api,
                "request",
                side_effect=[
                    {"reportId": "r1"},
                    {"status": "PROCESSING"},
                    {"status": "DONE", "file": "https://example.test/report"},
                ],
            ) as request,
            mock.patch.object(sync, "download_report", return_value=[]) as download,
            mock.patch.object(sync.time, "sleep"),
        ):
            self.assertEqual(
                sync.load_report("key", "goods-feedback", {"businessId": 7}, "paid_opinion_models", "sku"), []
            )
        self.assertEqual(request.call_args_list[0].kwargs["params"], {"format": "JSON"})
        download.assert_called_once_with("https://example.test/report", "paid_opinion_models", "sku")
        with mock.patch.object(sync.api, "request", side_effect=[{"reportId": "r1"}, {"status": "FAILED"}]):
            with self.assertRaises(RuntimeError):
                sync.load_report("key", "goods-feedback", {"businessId": 7}, "paid_opinion_models", "sku")

    def test_ambiguous_business_does_not_mix_cabinets(self):
        with (
            mock.patch.object(sync.tokens, "get_business_id", return_value=None),
            mock.patch.object(sync.tokens, "get_campaigns", return_value=[]),
            mock.patch.object(
                sync.api,
                "get_campaigns",
                return_value=[{"id": 1, "business": {"id": 10}}, {"id": 2, "business": {"id": 20}}],
            ),
        ):
            with self.assertRaises(ValueError):
                sync.resolve_business_id("rimili", "key")

    def test_orders_use_moscow_dates_and_deduplicate_api_pages(self):
        order = {
            "id": 1,
            "creationDate": "2026-09-01T21:10:00Z",
            "status": "DELIVERED",
            "items": [{"offerId": "A", "count": 2, "prices": {"payment": 100}}],
        }
        with mock.patch.object(sync.api, "get_business_orders", return_value=[order, order]):
            rows = sync.load_orders("rimili", "key", 1, date(2026, 9, 2), date(2026, 9, 8))
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            (rows[0]["day"], rows[0]["orders_count"], rows[0]["orders_amount"], rows[0]["sold_count"]),
            ("2026-09-02", 2, 100, 2),
        )
