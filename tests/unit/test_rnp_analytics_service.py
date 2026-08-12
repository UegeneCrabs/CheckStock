import io
import json
import logging
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from app import db, rnp_analytics
from app.repositories import core


def sales_line(**overrides) -> dict:
    row = {
        "store_slug": "rimili",
        "marketplace": "WB",
        "order_key": "order-1",
        "line_key": "line-1",
        "external_order_id": "external-1",
        "scheme": "fbs",
        "status": "sold",
        "substatus": "",
        "article": "1001",
        "barcode": "bc",
        "name": "Product",
        "ordered_at": "2026-08-10T10:00:00+00:00",
        "source_updated_at": "2026-08-12T10:00:00+00:00",
        "cancelled_at": None,
        "sold_at": "2026-08-11T10:00:00+00:00",
        "returned_at": None,
        "quantity": 2,
        "cancelled_quantity": 0,
        "sold_quantity": 2,
        "return_quantity": 0,
        "order_amount": 2000.0,
        "cancelled_amount": 0.0,
        "sale_amount": 1800.0,
        "return_amount": 0.0,
        "currency": "RUB",
        "raw_json": json.dumps({"nmId": 1001, "priceWithDisc": 1000, "finishedPrice": 900, "spp": 10}),
    }
    row.update(overrides)
    return row


class RnpAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "rnp.sqlite3"
        self.path_patch = mock.patch.object(core, "DB_PATH", self.db_path)
        self.path_patch.start()
        db.init_db()
        rnp_analytics._SCHEMA_READY = False
        rnp_analytics.init_schema()
        db.replace_catalog(
            "rimili",
            "WB",
            [
                {
                    "article": "1001",
                    "barcode": "bc",
                    "name": "Product",
                    "mp_sku": "sku-1",
                    "mp_product_id": "pid-1",
                    "image_url": "",
                }
            ],
            "2026-08-12T10:00:00+00:00",
        )

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp.cleanup()
        rnp_analytics._SCHEMA_READY = False
        logging.disable(logging.NOTSET)

    def test_schema_lookup_upsert_state_and_source_runner(self) -> None:
        rnp_analytics.init_schema()
        self.assertEqual(rnp_analytics._number("2.5"), 2.5)
        self.assertEqual(rnp_analytics._number(float("nan"), 3), 3)
        self.assertEqual(rnp_analytics._integer("bad", 4), 4)
        self.assertEqual(rnp_analytics._mapping([]), {})
        self.assertEqual(rnp_analytics._items({}), [])
        self.assertEqual(rnp_analytics._day("2026-01-02T10:00:00"), "2026-01-02")
        lookup = rnp_analytics._article_lookup("rimili", "WB")
        self.assertEqual(lookup["sku-1"], "1001")

        rows = [{"article": "1001", "day": "2026-08-10", "traffic_orders": 2}, {}]
        self.assertEqual(
            rnp_analytics._upsert("rimili", "WB", rows, rnp_analytics.FUNNEL_COLUMNS, "funnel_synced_at"),
            1,
        )
        self.assertEqual(rnp_analytics._upsert("rimili", "WB", [], (), "snapshot_synced_at"), 0)
        rnp_analytics._record_state(
            "rimili", "WB", "funnel", date(2026, 8, 1), date(2026, 8, 12), "success", 1
        )
        self.assertTrue(
            rnp_analytics._fresh("rimili", "WB", "funnel", date(2026, 8, 1), date(2026, 8, 12), False)
        )
        self.assertFalse(
            rnp_analytics._fresh("rimili", "WB", "funnel", date(2026, 8, 1), date(2026, 8, 12), True)
        )
        fresh = rnp_analytics._run_source(
            "rimili", "WB", "funnel", date(2026, 8, 1), date(2026, 8, 12), lambda: 2, False
        )
        self.assertEqual(fresh["status"], "fresh")
        success = rnp_analytics._run_source(
            "rimili", "WB", "snapshot", date(2026, 8, 1), date(2026, 8, 12), lambda: 2, True
        )
        self.assertEqual(success["rows"], 2)
        failed = rnp_analytics._run_source(
            "rimili",
            "WB",
            "bad",
            date(2026, 8, 1),
            date(2026, 8, 12),
            lambda: (_ for _ in ()).throw(ValueError("boom")),
            True,
        )
        self.assertEqual(failed["status"], "error")
        self.assertEqual(len(rnp_analytics.get_states("rimili", "WB")), 3)
        self.assertEqual(len(rnp_analytics.get_daily("rimili", "WB", "2026-08-01", "2026-09-01")), 1)
        self.assertEqual(rnp_analytics.get_daily("rimili", "WB", "x", "y", []), [])

    def test_wb_funnel_campaigns_and_advertising(self) -> None:
        today = datetime.now(rnp_analytics.MOSCOW).date()
        history = [
            {
                "product": {"nmId": 1001},
                "history": [
                    {
                        "date": today.isoformat(),
                        "openCount": 100,
                        "cartCount": 20,
                        "orderCount": 10,
                        "buyoutCount": 7,
                        "buyoutSum": 6000,
                        "buyoutPercent": 70,
                        "returnCount": 1,
                        "returnSum": 500,
                    }
                ],
            }
        ]
        with (
            mock.patch.object(rnp_analytics.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(rnp_analytics.wb_api, "request", return_value=history),
        ):
            self.assertEqual(rnp_analytics._sync_wb_funnel("rimili", today - timedelta(days=2), today), 1)
        self.assertEqual(
            rnp_analytics._sync_wb_funnel("rimili", today - timedelta(days=20), today - timedelta(days=10)), 0
        )
        with mock.patch.object(rnp_analytics, "_article_lookup", return_value={}):
            self.assertEqual(rnp_analytics._sync_wb_funnel("rimili", today, today), 0)

        campaign_count = {
            "adverts": [
                {"status": 9, "advert_list": [{"advertId": 1, "changeTime": "2"}, {"advertId": 0}]},
                {"status": 1, "advert_list": [{"advertId": 2}]},
            ]
        }
        with mock.patch.object(rnp_analytics.wb_api, "request", return_value=campaign_count):
            self.assertEqual(rnp_analytics._wb_campaign_ids("token"), ["1"])

        adverts = [
            {"id": 1, "bid_type": "unified"},
            {"id": 2, "bidType": "manual", "settings": {"placements": {"search": True}}},
            {"id": 3, "bidType": "manual", "settings": {"placements": {"recommendations": True}}},
            {"id": 4, "settings": {"payment_type": "cpc"}},
            {"id": 0},
        ]
        with mock.patch.object(rnp_analytics.wb_api, "request", return_value=adverts):
            kinds = rnp_analytics._wb_campaign_kinds("token", ["1", "2", "3", "4"])
        self.assertEqual(set(kinds.values()), set(rnp_analytics.CAMPAIGN_PREFIXES))
        self.assertEqual(rnp_analytics._wb_campaign_kinds("token", []), {})
        with mock.patch.object(rnp_analytics.wb_api, "request", side_effect=ValueError("bad")):
            self.assertEqual(rnp_analytics._wb_campaign_kinds("token", ["1"]), {})

        stats = [
            {
                "advertId": 1,
                "days": [
                    {
                        "date": today.isoformat(),
                        "apps": [
                            {
                                "nms": [
                                    {
                                        "nmId": 1001,
                                        "views": 1000,
                                        "clicks": 20,
                                        "sum": 300,
                                        "orders": 4,
                                        "atbs": 10,
                                        "sum_price": 4000,
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        ]
        with (
            mock.patch.object(rnp_analytics.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(rnp_analytics, "_wb_campaign_ids", return_value=["1"]),
            mock.patch.object(rnp_analytics, "_wb_campaign_kinds", return_value={"1": "unified"}),
            mock.patch.object(rnp_analytics.wb_api, "request", return_value=stats),
        ):
            self.assertEqual(rnp_analytics._sync_wb_advertising("rimili", today, today), 1)
        with (
            mock.patch.object(rnp_analytics.wb_tokens, "get_token", return_value="token"),
            mock.patch.object(rnp_analytics, "_wb_campaign_ids", return_value=[]),
        ):
            self.assertEqual(rnp_analytics._sync_wb_advertising("rimili", today, today), 0)

    def test_ozon_and_yandex_funnels(self) -> None:
        self.assertEqual(
            rnp_analytics._ozon_dimensions(
                {"dimensions": [{"key": "day", "value": "2026-08-01"}, {"key": "sku", "id": "123"}]}
            ),
            {"day": "2026-08-01", "sku": "123"},
        )
        with (
            mock.patch.object(rnp_analytics.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(rnp_analytics, "_article_lookup", return_value={"123": "1001"}),
            mock.patch.object(
                rnp_analytics.ozon_api,
                "request",
                return_value={
                    "result": {
                        "data": [
                            {
                                "dimensions": [
                                    {"key": "day", "value": "2026-08-01"},
                                    {"key": "sku", "id": "123"},
                                ],
                                "metrics": [4],
                            }
                        ]
                    }
                },
            ),
        ):
            self.assertEqual(rnp_analytics._sync_ozon_funnel("rimili", date(2026, 8, 1), date(2026, 8, 2)), 1)

        campaign = {"business": {"id": 7}}
        report_rows = [
            {
                "offerId": "1001",
                "day": "2026-08-01",
                "clicks": 10,
                "toCart": 3,
                "orderItems": 2,
                "orderItemsDeliveredCount": 1,
                "orderItemsDeliveredTotalAmount": 900,
                "orderItemsReturnedCount": 1,
            }
        ]
        requests = [
            {"reportId": "report"},
            {"status": "PROCESSING"},
            {"status": "DONE", "file": "file"},
        ]
        with (
            mock.patch.object(rnp_analytics.yandex_tokens, "get_api_key", return_value="key"),
            mock.patch.object(rnp_analytics.yandex_api, "get_campaigns", return_value=[campaign]),
            mock.patch.object(rnp_analytics.yandex_api, "request", side_effect=requests),
            mock.patch.object(rnp_analytics, "_download_json_report", return_value=report_rows),
            mock.patch.object(rnp_analytics.time, "sleep"),
        ):
            self.assertEqual(
                rnp_analytics._sync_yandex_funnel("rimili", date(2026, 8, 1), date(2026, 8, 2)), 1
            )

        with (
            mock.patch.object(rnp_analytics.yandex_tokens, "get_api_key", return_value="key"),
            mock.patch.object(rnp_analytics.yandex_api, "get_campaigns", return_value=[campaign]),
            mock.patch.object(rnp_analytics.yandex_api, "request", return_value={}),
        ):
            with self.assertRaises(RuntimeError):
                rnp_analytics._sync_yandex_funnel("rimili", date(2026, 8, 1), date(2026, 8, 2))

    def test_report_walk_and_zip_download(self) -> None:
        nested = {"data": [{"offerId": "A", "date": "2026-01-01"}, {"x": 1}]}
        self.assertEqual(list(rnp_analytics._walk_report_rows(nested))[0]["offerId"], "A")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("report.json", json.dumps(nested))
            archive.writestr("ignored.txt", "x")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return buffer.getvalue()

        with mock.patch.object(rnp_analytics.urllib.request, "urlopen", return_value=Response()):
            self.assertEqual(rnp_analytics._download_json_report("url")[0]["offerId"], "A")

    def test_price_sources_reputation_and_snapshots(self) -> None:
        db.upsert_sales_order_lines([sales_line()], "2026-08-12T10:00:00+00:00")
        prices = rnp_analytics._history_price_rows(
            "rimili", "WB", date(2026, 8, 1), date(2026, 8, 12), ["1001"]
        )
        self.assertEqual(prices[0]["price_before_spp"], 1000)
        self.assertEqual(prices[0]["price_after_spp"], 900)

        with (
            mock.patch.object(rnp_analytics.ozon_tokens, "get_credentials", return_value=("id", "key")),
            mock.patch.object(
                rnp_analytics.ozon_api,
                "request",
                side_effect=[
                    {
                        "items": [
                            {"offer_id": "A", "price": {"old_price": "100", "marketing_seller_price": "80"}}
                        ],
                        "cursor": "next",
                    },
                    {"items": [], "cursor": ""},
                ],
            ),
        ):
            self.assertEqual(rnp_analytics._ozon_current_prices("rimili")["A"], (100, 80))
        with mock.patch.object(rnp_analytics.ozon_tokens, "get_credentials", side_effect=ValueError("bad")):
            self.assertEqual(rnp_analytics._ozon_current_prices("rimili"), {})

        with (
            mock.patch.object(rnp_analytics.yandex_tokens, "get_api_key", return_value="key"),
            mock.patch.object(rnp_analytics.yandex_api, "get_campaigns", return_value=[{"id": 1}, {"id": 0}]),
            mock.patch.object(
                rnp_analytics.yandex_api,
                "request",
                side_effect=[
                    {
                        "offers": [{"id": "A", "price": {"discountBase": 100, "value": 90}}],
                        "paging": {"nextPageToken": "next"},
                    },
                    {"offers": [], "paging": {}},
                ],
            ),
        ):
            self.assertEqual(rnp_analytics._yandex_current_prices("rimili")["A"], (100, 90))

        payload = {"data": {"products": [{"id": 1001, "reviewRating": 4.8, "feedbacks": 25}]}}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(payload).encode()

        with mock.patch.object(rnp_analytics.urllib.request, "urlopen", return_value=Response()):
            reputation = rnp_analytics._wb_current_reputation([{"article": "1001", "nm_id": 1001}])
        self.assertEqual(reputation["1001"], (4.8, 25))

        db.upsert_mp_stock("rimili", "1001", "WB", "fbs", 14, "2026-08-12T10:00:00+00:00")
        db.replace_mp_warehouse_stock(
            "rimili", "WB", "fbs", [("1001", "WH", "Region", 14, "2026-08-12T10:00:00+00:00")]
        )
        db.upsert_wb_unit_prices(
            "rimili",
            [
                {
                    "article": "1001",
                    "nm_id": 1001,
                    "list_price": 1000,
                    "discounted_price": 900,
                    "buyer_price": 850,
                    "spp_percent": 10,
                }
            ],
            "2026-08-12T10:00:00+00:00",
        )
        snapshot_day = date(2026, 8, 12)
        with mock.patch.object(rnp_analytics, "_wb_current_reputation", return_value={"1001": (4.8, 25)}):
            snapshot = rnp_analytics._current_snapshot_rows("rimili", "WB", snapshot_day, None)[0]
        self.assertEqual(snapshot["stock_units"], 14)
        self.assertEqual(snapshot["price_after_spp"], 850)
        self.assertEqual(snapshot["stock_turnover_days"], 48.28)
        self.assertIn("Region", snapshot["stock_regions"])

    def test_daily_snapshot_and_orchestration(self) -> None:
        today = datetime.now(rnp_analytics.MOSCOW).date()
        with (
            mock.patch.object(
                rnp_analytics,
                "_history_price_rows",
                return_value=[{"article": "1001", "day": today.isoformat()}],
            ),
            mock.patch.object(
                rnp_analytics,
                "_current_snapshot_rows",
                return_value=[{"article": "1001", "day": today.isoformat()}],
            ),
            mock.patch.object(rnp_analytics, "_upsert", side_effect=[1, 1]),
        ):
            self.assertEqual(rnp_analytics._sync_daily_snapshots("rimili", "WB", today, today, None), 2)

        with mock.patch.object(rnp_analytics, "STORES", {"rimili": {}}):
            with self.assertRaises(ValueError):
                rnp_analytics.sync_store("wrong", "WB", today, today)
            with self.assertRaises(ValueError):
                rnp_analytics.sync_store("rimili", "wrong", today, today)

            token_checks = {
                "WB": (rnp_analytics.wb_tokens, "has_token"),
                "OZON": (rnp_analytics.ozon_tokens, "has_credentials"),
                "YANDEX MARKET": (rnp_analytics.yandex_tokens, "has_credentials"),
            }
            for marketplace, (module, attribute) in token_checks.items():
                with (
                    mock.patch.object(module, attribute, return_value=True),
                    mock.patch.object(
                        rnp_analytics, "_run_source", return_value={"status": "success"}
                    ) as run,
                ):
                    result = rnp_analytics.sync_store("rimili", marketplace, today, today, True)
                self.assertTrue(result["configured"])
                self.assertGreaterEqual(run.call_count, 2)

            with mock.patch.object(
                rnp_analytics, "sync_store", return_value={"results": [{"status": "success"}]}
            ) as sync:
                current = rnp_analytics.sync_current(True)
            self.assertEqual(sync.call_count, 3)
            self.assertIn("rimili", current["WB"])


if __name__ == "__main__":
    unittest.main()
