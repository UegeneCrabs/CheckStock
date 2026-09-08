import json
import tempfile
import unittest
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest import mock

from app import db, sales, unit_economics_yandex
from app.repositories import core
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
        self.assertIsNone(missing["advertising"]["spend"])
        self.save("orders", [], "2026-08-20", "2026-09-09")
        self.save("advertising", [])
        empty = self.products()["YM-1"]
        self.assertEqual(empty["stock"]["days"], 0)
        self.assertEqual(empty["economics_7d"]["turnover"], 0)
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
        before = repository.get_daily_orders("rimili", "2026-08-20", "2026-09-10")
        self.assertEqual(self.products()["YM-1"]["economics_7d"]["turnover"], 2000)
        self.save("orders", [daily()], "2026-08-20", "2026-09-09")
        self.save("orders", [daily(amount=99999)], "2026-08-20", "2026-09-09", store="tris")
        self.assertEqual(self.products()["YM-1"]["economics_7d"]["turnover"], 8000)
        self.assertEqual(repository.get_daily_orders("rimili", "2026-08-20", "2026-09-10"), before)

    def test_sync_failures_preserve_successful_snapshot_and_isolate_sources(self):
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
            result = sync.sync_store("rimili", TODAY)
        self.assertFalse(result["ok"])
        self.assertTrue(result["sources"]["orders"]["ok"])
        snapshots = repository.get_snapshots("rimili")
        self.assertEqual(snapshots["advertising"]["data"][0]["spend"], 750)
        self.assertEqual(snapshots["advertising"]["error"], "unavailable")
        self.assertEqual(self.products()["YM-1"]["advertising"]["drr"], 10)


class YandexReportTests(unittest.TestCase):
    def test_scheduled_sync_skips_cabinets_without_credentials(self):
        with (
            mock.patch.object(sync.tokens, "has_credentials", side_effect=lambda slug: slug == "tris"),
            mock.patch.object(sync, "sync_store", return_value={"ok": True}) as sync_store,
        ):
            self.assertEqual(sync.sync_all(("rimili", "tris")), {"tris": {"ok": True}})
        sync_store.assert_called_once_with("tris")

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
                sync.load_report("key", "goods-feedback", {}, "paid_opinion_models", "sku")

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
