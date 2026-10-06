"""FBO export contracts with synthetic marketplace replies; no live credentials or sheets."""

import importlib
import os
import threading
import unittest
from unittest.mock import patch


class FboTransitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.transit = importlib.import_module("app.exports.fbo_transit")

    def mock(self, obj, name, **kwargs):
        patcher = patch.object(obj, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def setUp(self):
        self.mock(self.transit.wb_tokens, "get_token", return_value="test-token")
        self.mock(self.transit.ozon_tokens, "get_credentials", return_value=("test-client", "test-token"))
        self.mock(self.transit.yandex_tokens, "get_api_key", return_value="test-token")
        self.mock(self.transit, "resolve_business_id", return_value=100)
        self.wb = self.mock(self.transit.wb_api, "_request", side_effect=AssertionError("Unmocked WB API"))
        self.oz = self.mock(
            self.transit.ozon_api, "_request", side_effect=AssertionError("Unmocked Ozon API")
        )
        self.ya = self.mock(
            self.transit.yandex_api, "_request", side_effect=AssertionError("Unmocked Yandex API")
        )

    def test_all_seven_accounts_run_in_parallel_and_each_export_fetches_again(self):
        barrier = threading.Barrier(len(self.transit.STORES), timeout=5)
        threads = set()
        lock = threading.Lock()

        def load(store):
            with lock:
                threads.add(threading.get_ident())
            barrier.wait()
            return self.transit.TransitSnapshot({store: 1}, {})

        loader = self.mock(self.transit, "_wb", side_effect=load)
        for _ in range(2):
            result = self.transit.load("WB")
            self.assertEqual(list(result), list(self.transit.STORES))
            self.assertEqual(result["rimili"].to_customer, {"rimili": 1})
        self.assertGreaterEqual(len(threads), len(self.transit.STORES))
        self.assertEqual(loader.call_count, 2 * len(self.transit.STORES))

    def test_failed_account_never_returns_partial_snapshot(self):
        def load(store):
            if store == "tris":
                raise ValueError("Нет страницы API")
            return self.transit.TransitSnapshot()

        self.mock(self.transit, "_wb", side_effect=load)
        with self.assertRaisesRegex(self.transit.StockSheetExportError, "TRIS / WB / FBO"):
            self.transit.load("WB")

    def test_wb_creates_fresh_report_waits_and_reads_only_customer_transit(self):
        self.mock(self.transit.wb_api.time, "sleep")
        self.wb.side_effect = [
            {"data": {"taskId": "task-1"}},
            {"data": {"status": "processing"}},
            {"data": {"status": "done"}},
            [
                {
                    "nmId": 123,
                    "warehouses": [
                        {"warehouseName": "В пути до получателей", "quantity": 5},
                        {"warehouseName": "В пути возвраты на склад WB", "quantity": 2},
                        {"warehouseName": "Всего находится на складах", "quantity": 400},
                        {"warehouseName": "Коледино", "quantity": 100},
                    ],
                }
            ],
        ]
        result = self.transit._wb("rimili")
        self.assertEqual(result.to_customer, {"123": 5})
        self.assertEqual(result.from_customer, {"123": 2})
        self.assertEqual(self.wb.call_args_list[0].kwargs["params"], {"locale": "ru", "groupByNm": "true"})
        self.assertTrue(self.wb.call_args.args[1].endswith("/tasks/task-1/download"))

    def test_wb_report_empty_204_failure_timeout_and_bad_counts(self):
        self.wb.side_effect = [{"data": {"taskId": "task"}}, {"data": {"status": "done"}}, None]
        self.assertEqual(self.transit._wb("rimili").to_customer, {})
        self.wb.side_effect = [{"data": {"taskId": "task"}}, {"data": {"status": "failed"}}]
        with self.assertRaises(self.transit.wb_api.WBApiError):
            self.transit._wb("rimili")
        self.wb.side_effect = [{"data": {"taskId": "task"}}]
        with self.assertRaisesRegex(self.transit.wb_api.WBApiError, "не подготовил"):
            self.transit.wb_api.get_warehouse_remains("test", timeout_seconds=0)
        source = self.mock(self.transit.wb_api, "get_warehouse_remains")
        for count in (None, -1, 1.5, "unknown", True):
            source.return_value = [
                {"nmId": 123, "warehouses": [{"warehouseName": "В пути до получателей", "quantity": count}]}
            ]
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.transit._wb("rimili")

    def test_ozon_uses_live_fbo_skus_and_sums_customer_counters_across_warehouses(self):
        def request(path, *args):
            if path == "/v4/product/info/stocks":
                return {
                    "items": [
                        {
                            "product_id": 1,
                            "offer_id": "sku",
                            "stocks": [{"type": "fbo", "sku": 10}, {"type": "fbs", "sku": 20}],
                        }
                    ],
                    "total_items": 1,
                }
            self.assertEqual(path, "/v1/analytics/stocks")
            self.assertEqual(args[-1], {"skus": ["10"]})
            return {
                "items": [
                    {
                        "sku": 10,
                        "offer_id": "sku",
                        "warehouse_id": warehouse,
                        "outbound_pending_delivery": 3,
                        "return_from_customer_stock_count": 2,
                        "transit_stock_count": 900,
                        "inbound_replenishment": 800,
                        "return_to_seller_stock_count": 700,
                    }
                    for warehouse in (1, 2)
                ]
            }

        self.oz.side_effect = request
        result = self.transit._ozon("rimili")
        self.assertEqual(result.to_customer, {"sku": 6})
        self.assertEqual(result.from_customer, {"sku": 4})
        self.assertEqual(self.oz.call_args_list[0].args[-1]["filter"], {"visibility": "ALL"})

    def test_ozon_pagination_follows_short_pages_and_rejects_repeated_cursor(self):
        self.oz.side_effect = [
            {"items": [{"product_id": 1}], "cursor": "next", "total_items": 2},
            {"items": [{"product_id": 2}], "cursor": "", "total_items": 2},
        ]
        self.assertEqual(len(self.transit.ozon_api.get_product_stocks("client", "key")), 2)
        self.oz.side_effect = [
            {"items": [{"product_id": 1}], "cursor": "next"},
            {"items": [{"product_id": 2}], "cursor": "next"},
        ]
        with self.assertRaisesRegex(self.transit.ozon_api.OzonApiError, "пагинацию"):
            self.transit.ozon_api.get_product_stocks("client", "key")

    def test_ozon_missing_customer_counter_is_not_replaced_with_supply_counter(self):
        self.mock(
            self.transit.ozon_api,
            "get_product_stocks",
            return_value=[{"offer_id": "sku", "stocks": [{"type": "fbo", "sku": 10}]}],
        )
        source = self.mock(
            self.transit.ozon_api,
            "get_stock_analytics",
            return_value=[
                {
                    "sku": 10,
                    "offer_id": "sku",
                    "warehouse_id": 1,
                    "transit_stock_count": 999,
                    "return_from_customer_stock_count": 2,
                }
            ],
        )
        with self.assertRaises(ValueError):
            self.transit._ozon("rimili")
        source.return_value = [
            {
                "sku": 99,
                "offer_id": "other",
                "warehouse_id": 1,
                "outbound_pending_delivery": 1,
                "return_from_customer_stock_count": 1,
            }
        ]
        with self.assertRaisesRegex(ValueError, "неизвестный SKU"):
            self.transit._ozon("rimili")

    def test_ozon_pacing_and_backoff_are_isolated_per_account(self):
        api = self.transit.ozon_api
        self.mock(api, "_last_call_at", new={})
        self.mock(api, "_interval", new={})
        self.mock(api, "_calm_streak", new={})
        self.mock(api.time, "monotonic", return_value=100.0)
        sleep = self.mock(api.time, "sleep")
        path = "/v1/analytics/stocks"
        api._throttle(path, "first")
        api._note_rate_limit(path, "first")
        api._throttle(path, "second")
        sleep.assert_not_called()
        api._throttle(path, "first")
        sleep.assert_called_once_with(3.0)
        self.assertEqual(api._interval[("second", path)], 1.5)
        for _ in range(api.THROTTLE_RELAX_AFTER):
            api._note_success(path, "second")
        self.assertEqual(api._interval[("first", path)], 3.0)

    def test_ozon_waiting_does_not_hold_the_global_throttle_lock(self):
        api = self.transit.ozon_api
        path = "/v1/analytics/stocks"
        self.mock(api, "_last_call_at", new={("first", path): 100.0})
        self.mock(api, "_interval", new={})
        self.mock(api.time, "monotonic", return_value=100.0)

        def waiting(_seconds):
            acquired = api._throttle_lock.acquire(blocking=False)
            if acquired:
                api._throttle_lock.release()
            self.assertTrue(acquired)

        self.mock(api.time, "sleep", side_effect=waiting)
        api._throttle(path, "first")

    def test_wb_report_endpoints_have_independent_documented_rate_limits(self):
        api = self.transit.wb_api
        base = api.ANALYTICS_BASE + "/api/v1/warehouse_remains"
        self.assertEqual(api._rate_limit_for_url(base), ("warehouse_remains_create", 60.1))
        self.assertEqual(
            api._rate_limit_for_url(base + "/tasks/id/status"), ("warehouse_remains_status", 5.1)
        )
        self.assertEqual(
            api._rate_limit_for_url(base + "/tasks/id/download"), ("warehouse_remains_download", 60.1)
        )

    def configure_yandex(self):
        self.mock(
            self.transit.yandex_api,
            "get_campaigns",
            return_value=[
                {"id": 11, "placementType": "FBY", "business": {"id": 100}},
                {"id": 12, "placementType": "FBS", "business": {"id": 100}},
                {"id": 13, "placementType": "FBY", "business": {"id": 999}},
            ],
        )

    @staticmethod
    def order(order_id, status="DELIVERY", **changes):
        return {
            "orderId": order_id,
            "campaignId": 11,
            "programType": "FBY",
            "status": status,
            "items": [{"offerId": "sku", "count": 5}],
            **changes,
        }

    def test_yandex_only_own_fby_and_current_item_statuses_and_partial_returns(self):
        self.configure_yandex()
        loader = self.mock(
            self.transit.yandex_api,
            "get_fby_delivery_orders",
            return_value=[
                self.order(
                    1,
                    items=[
                        {
                            "offerId": "sku",
                            "count": 5,
                            "itemStatuses": [
                                {"status": "SHIPPED", "count": 2},
                                {"status": "DELIVERED_TO_BUYER", "count": 3},
                            ],
                        }
                    ],
                ),
                self.order(2, "DELIVERED"),
                self.order(3, "CANCELLED"),
                self.order(4, "PICKUP", fake=True),
                self.order(5, "PICKUP"),
            ],
        )
        returns = self.mock(
            self.transit.yandex_api,
            "get_fby_customer_returns",
            return_value=[
                {
                    "shipmentStatus": "IN_TRANSIT",
                    "items": [
                        {
                            "shopSku": "sku",
                            "count": 3,
                            "instances": [
                                {"status": "IN_TRANSIT"},
                                {"status": "RECEIVED_ON_FULFILLMENT"},
                                {"status": "CANCELLED"},
                            ],
                        }
                    ],
                },
                {"shipmentStatus": "RECEIVED", "items": [{"shopSku": "sku", "count": 2}]},
                {"shipmentStatus": "FULFILMENT_RECEIVED", "items": [{"shopSku": "sku", "count": 20}]},
                {
                    "shipmentStatus": "IN_TRANSIT",
                    "fastReturn": True,
                    "items": [{"shopSku": "sku", "count": 30}],
                },
            ],
        )
        result = self.transit._yandex("rimili")
        loader.assert_called_once_with("test-token", 100, [11])
        returns.assert_called_once_with("test-token", 11)
        self.assertEqual(result.to_customer, {"sku": 7})
        self.assertEqual(result.from_customer, {"sku": 3})

    def test_yandex_refreshes_old_active_order_ids_and_paginates_current_orders(self):
        self.ya.side_effect = [
            {"orders": [self.order(1)], "paging": {"nextPageToken": "page2"}},
            {"orders": [self.order(2)]},
            {"orders": [{"id": 1}, {"id": 3}, {"id": 4}]},
            {"orders": [self.order(3), self.order(4, "DELIVERED")]},
        ]
        rows = self.transit.yandex_api.get_fby_delivery_orders("key", 100, [11])
        self.assertEqual([row["orderId"] for row in rows], [1, 2, 3, 4])
        self.assertEqual(self.ya.call_args.kwargs["payload"], {"orderIds": [3, 4]})
        self.assertEqual(self.ya.call_args_list[2].kwargs["payload"], {"statuses": ["DELIVERY", "PICKUP"]})
        self.assertEqual(self.ya.call_args_list[1].kwargs["params"]["pageToken"], "page2")

    def test_yandex_missing_old_order_status_and_broken_pagination_fail(self):
        self.ya.side_effect = [{"orders": []}, {"orders": [{"id": 3}]}, {"orders": []}]
        with self.assertRaisesRegex(self.transit.yandex_api.YandexApiError, "не подтвердил"):
            self.transit.yandex_api.get_fby_delivery_orders("key", 100, [11])
        for replies in (
            [{"returns": [], "paging": {"nextPageToken": "next"}}],
            [
                {"returns": [{"id": 1, "orderId": 1}], "paging": {"nextPageToken": "next"}},
                {"returns": [{"id": 2, "orderId": 2}], "paging": {"nextPageToken": "next"}},
            ],
            [{"wrong-key": []}],
        ):
            with self.subTest(replies=replies), self.assertRaises(self.transit.yandex_api.YandexApiError):
                self.ya.side_effect = replies
                self.transit.yandex_api.get_fby_customer_returns("key", 11)


if __name__ == "__main__":
    unittest.main()
