import unittest
from unittest import mock

from app.ozon import api as ozon_api
from app.wb import api as wb_api
from app.yandex import api as yandex_api


class WildberriesApiTests(unittest.TestCase):
    def test_errors_expectations_and_warehouses(self) -> None:
        self.assertIn("нет доступа", wb_api.WBApiError(401, detail="denied").friendly)
        self.assertIn("ошибка 418", wb_api.WBApiError(418, detail="teapot").friendly)
        self.assertEqual(wb_api._parse_error_body("bad"), ("", "bad"))
        self.assertEqual(wb_api._parse_error_body('{"title":"t","detail":"d"}'), ("t", "d"))
        self.assertEqual(wb_api._expect({"data": {"id": 1}}, "data", "id", context="test"), 1)
        with self.assertRaises(wb_api.WBApiError):
            wb_api._expect({}, "data", "id", context="test")

        with mock.patch.object(wb_api, "_request", return_value=None):
            self.assertEqual(wb_api.get_own_warehouses("token"), [])
        with mock.patch.object(wb_api, "_request", return_value=[{"id": 1}]):
            self.assertEqual(wb_api.get_own_warehouses("token"), [{"id": 1}])
        with mock.patch.object(wb_api, "_request", return_value={"id": 1}):
            with self.assertRaises(wb_api.WBApiError):
                wb_api.get_own_warehouses("token")

    def test_fbs_and_fbo_stock_parsing(self) -> None:
        self.assertEqual(wb_api.get_fbs_stock("token", 1, []), {})
        with mock.patch.object(
            wb_api,
            "_request",
            return_value={"stocks": [{"sku": "1", "amount": 3}, {"sku": "2"}]},
        ) as request:
            result = wb_api.get_fbs_stock("token", 10, ["1", "1", "", "2"])
        self.assertEqual(result, {"1": 3, "2": 0})
        self.assertEqual(request.call_args.kwargs["json_body"], {"skus": ["1", "2"]})
        with mock.patch.object(wb_api, "_request", return_value={"stocks": [None]}):
            with self.assertRaises(wb_api.WBApiError):
                wb_api.get_fbs_stock("token", 10, ["1"])

        rows = [
            {
                "barcode": "1",
                "warehouses": [
                    {"warehouseName": "Коледино", "quantity": 2},
                    {"warehouseName": "Коледино", "quantity": 3},
                    {"warehouseName": "Всего находится на складах", "quantity": 100},
                ],
            },
            {"barcode": "", "warehouses": []},
        ]
        with (
            mock.patch.object(wb_api, "_create_warehouse_remains_task", return_value="task"),
            mock.patch.object(wb_api, "_get_warehouse_remains_status", return_value="done"),
            mock.patch.object(wb_api, "_download_warehouse_remains", return_value=rows),
            mock.patch.object(wb_api.time, "sleep"),
        ):
            result = wb_api.get_fbo_stock_by_warehouse("token", poll_interval=0, max_wait=1)
        self.assertEqual(result, {("1", "Коледино"): 5})
        with (
            mock.patch.object(wb_api, "_create_warehouse_remains_task", return_value="task"),
            mock.patch.object(wb_api, "_get_warehouse_remains_status", return_value="error"),
            mock.patch.object(wb_api.time, "sleep"),
        ):
            with self.assertRaises(wb_api.WBApiError):
                wb_api.get_fbo_stock_by_warehouse("token", poll_interval=0, max_wait=1)

    def test_catalog_prices_and_statistics_pagination(self) -> None:
        pages = [
            {
                "cards": [{"nmID": 1}, {"nmID": 2}],
                "cursor": {"total": 2, "updatedAt": "u", "nmID": 2},
            },
            {"cards": [{"nmID": 2}, {"nmID": 3}], "cursor": {"total": 1}},
        ]
        with mock.patch.object(wb_api, "_request", side_effect=pages):
            cards = wb_api.get_cards_list("token", page_limit=2)
        self.assertEqual([card["nmID"] for card in cards], [1, 2, 3])

        normalized = wb_api.normalize_card(
            {
                "nmID": 1,
                "vendorCode": "A",
                "title": "Name",
                "subjectID": 5,
                "subjectName": "Subject",
                "updatedAt": "now",
                "photos": [{"big": "https://img.test/a.jpg"}],
                "sizes": [
                    {"techSize": "M", "skus": ["11", "12"]},
                    {"techSize": "L", "skus": []},
                ],
            }
        )
        self.assertEqual(normalized["sizes"][0]["extra_barcodes"], ["12"])
        self.assertEqual(normalized["image_url"], "https://img.test/a.jpg")

        with mock.patch.object(
            wb_api,
            "_request",
            return_value={"data": {"listGoods": [{"nmID": 1}]}},
        ):
            self.assertEqual(wb_api.get_products_with_prices("token", [1, 1, -1]), [{"nmID": 1}])
        with mock.patch.object(wb_api, "_request", return_value={"report": [{"kgvpMarketplace": 15}]}):
            self.assertEqual(len(wb_api.get_category_commissions("token")), 1)

        page_one = [
            {"srid": "1", "lastChangeDate": "2026-08-10T00:00:00"},
            {"srid": "2", "lastChangeDate": "2026-08-11T00:00:00"},
        ]
        page_two = [
            {"srid": "2", "lastChangeDate": "2026-08-11T00:00:00"},
            {"srid": "3", "lastChangeDate": "2026-08-12T00:00:00"},
        ]
        with (
            mock.patch.object(wb_api, "ORDERS_PAGE_SIZE", 2),
            mock.patch.object(wb_api, "_request", side_effect=[page_one, page_two, []]),
            mock.patch.object(wb_api.time, "sleep"),
        ):
            rows = wb_api.get_orders("token", "2026-08-01", max_pages=3)
        self.assertEqual([row["srid"] for row in rows], ["1", "2", "3"])
        with mock.patch.object(wb_api, "_request", return_value=[]):
            self.assertEqual(wb_api.get_sales("token", "2026-08-01"), [])


class OzonApiTests(unittest.TestCase):
    def setUp(self) -> None:
        ozon_api.clear_store_context()
        ozon_api._last_call_at.clear()
        ozon_api._interval.clear()
        ozon_api._calm_streak.clear()

    def test_context_errors_and_throttling(self) -> None:
        ozon_api.set_store_context("STORE")
        self.assertEqual(ozon_api._store_label(), "[STORE] ")
        self.assertIn("нет доступа", ozon_api.OzonApiError(403, "denied").friendly)
        self.assertIn("ошибку 418", ozon_api.OzonApiError(418, "teapot").friendly)
        self.assertEqual(ozon_api._parse_error_body("plain"), "plain")
        self.assertIn("код 8", ozon_api._parse_error_body('{"code":8,"message":"limit"}'))
        self.assertEqual(ozon_api._retry_after({"Retry-After": "2"}), 2)
        self.assertIsNone(ozon_api._retry_after({"Retry-After": "bad"}))
        self.assertGreaterEqual(ozon_api._backoff_pause(1), 5)

        path = "/v1/analytics/stocks"
        with (
            mock.patch.object(ozon_api.time, "monotonic", side_effect=[10.0, 10.0]),
            mock.patch.object(ozon_api.time, "sleep") as sleep,
        ):
            ozon_api._last_call_at[path] = 9.5
            ozon_api._throttle(path)
        sleep.assert_called_once()
        increased = ozon_api._note_rate_limit(path)
        self.assertGreater(increased, ozon_api.THROTTLED_PATHS[path])
        ozon_api._calm_streak[path] = ozon_api.THROTTLE_RELAX_AFTER - 1
        ozon_api._note_success(path)
        self.assertLessEqual(ozon_api._interval[path], increased)

    def test_warehouse_stock_and_analytics_pagination(self) -> None:
        with mock.patch.object(
            ozon_api,
            "_request",
            side_effect=[
                {"warehouses": [{"warehouse_id": 1}], "has_next": True, "cursor": "next"},
                {"result": [{"warehouse_id": 2}], "has_next": False},
            ],
        ):
            warehouses = ozon_api.get_own_warehouses("client", "key")
        self.assertEqual(len(warehouses), 2)

        with (
            mock.patch.object(ozon_api, "PAGE_SIZE", 2),
            mock.patch.object(
                ozon_api,
                "_request",
                side_effect=[
                    {"result": {"rows": [{"sku": 1}, {"sku": 2}]}},
                    {"result": {"rows": [{"sku": 3}]}},
                ],
            ),
        ):
            rows = ozon_api.get_fbo_stock_by_warehouse("client", "key")
        self.assertEqual(len(rows), 3)
        self.assertEqual(ozon_api.get_stock_analytics("client", "key", []), [])
        with mock.patch.object(
            ozon_api,
            "_request",
            side_effect=[{"items": [{"sku": 1}]}, {"result": {"items": [{"sku": 2}]}}],
        ):
            analytics = ozon_api.get_stock_analytics("client", "key", list(range(101)))
        self.assertEqual(len(analytics), 2)
        normalized = ozon_api.normalize_analytics_row(
            {"sku": 1, "available_stock_count": "3", "transit_stock_count": "bad"}
        )
        self.assertEqual(normalized["available"], 3)
        self.assertEqual(normalized["transit"], 0)

    def test_product_and_posting_pagination_and_normalization(self) -> None:
        with (
            mock.patch.object(ozon_api, "PAGE_SIZE", 1),
            mock.patch.object(
                ozon_api,
                "_request",
                side_effect=[
                    {"items": [{"id": 1}], "cursor": "next"},
                    {"items": [{"id": 2}], "cursor": ""},
                ],
            ),
        ):
            stocks = ozon_api.get_product_stocks("client", "key")
        self.assertEqual(len(stocks), 2)

        with (
            mock.patch.object(ozon_api, "PAGE_SIZE", 1),
            mock.patch.object(
                ozon_api,
                "_request",
                side_effect=[
                    {"result": {"items": [{"product_id": 1}], "last_id": "next"}},
                    {"result": {"items": [], "last_id": ""}},
                ],
            ),
        ):
            products = ozon_api.get_product_list("client", "key")
        self.assertEqual(len(products), 1)
        self.assertEqual(ozon_api.get_product_info("client", "key", []), [])
        with mock.patch.object(
            ozon_api,
            "_request",
            return_value={"result": {"items": [{"id": 1}]}},
        ):
            self.assertEqual(len(ozon_api.get_product_info("client", "key", [1])), 1)

        with mock.patch.object(
            ozon_api,
            "_request",
            side_effect=[
                {"result": {"postings": [{"id": 1}], "has_next": True}},
            ],
        ):
            self.assertEqual(len(ozon_api.get_fbo_postings("client", "key", "from", "to")), 1)
        with mock.patch.object(
            ozon_api,
            "_request",
            return_value={"result": {"items": [{"id": 2}], "has_next": False}},
        ):
            self.assertEqual(len(ozon_api.get_fbs_postings("client", "key", "from", "to")), 1)

        product = ozon_api.normalize_product(
            {
                "id": 10,
                "offer_id": " A-1 ",
                "sources": [{"sku": "123"}],
                "barcode": "1",
                "barcodes": ["2"],
                "images": [{"url": "https://img.test/1.jpg"}],
                "is_archived": True,
            }
        )
        self.assertEqual(product["sku"], 123)
        self.assertEqual(product["barcodes"], ["1", "2"])
        self.assertEqual(product["image_url"], "https://img.test/1.jpg")


class YandexApiTests(unittest.TestCase):
    def test_errors_campaigns_and_orders(self) -> None:
        self.assertIn("нет доступа", yandex_api.YandexApiError(403, "denied").friendly)
        self.assertIn("ошибку 418", yandex_api.YandexApiError(418, "teapot").friendly)
        self.assertEqual(yandex_api._parse_error_body("plain"), "plain")
        parsed = yandex_api._parse_error_body(
            '{"errors":[{"code":"BAD","message":"wrong"},{"message":"again"}]}'
        )
        self.assertIn("BAD: wrong", parsed)

        with mock.patch.object(
            yandex_api,
            "_request",
            side_effect=[
                {"campaigns": [{"id": 1}], "pager": {"pagesCount": 2}},
                {"campaigns": [{"id": 2}], "pager": {"pagesCount": 2}},
            ],
        ):
            campaigns = yandex_api.get_campaigns("key")
        self.assertEqual(len(campaigns), 2)
        normalized = yandex_api.normalize_campaign(
            {
                "id": 1,
                "business": {"id": 2, "name": "Business"},
                "domain": "shop.test",
                "placementType": "FBS",
            }
        )
        self.assertEqual(normalized["scheme"], "fbs")

        with mock.patch.object(
            yandex_api,
            "_request",
            side_effect=[
                {"orders": [{"id": 1}], "paging": {"nextPageToken": "next"}},
                {"orders": [{"id": 2}], "paging": {}},
            ],
        ):
            orders = yandex_api.get_business_orders("key", 2, "from", "to")
        self.assertEqual(len(orders), 2)

    def test_catalog_stocks_and_helpers(self) -> None:
        with mock.patch.object(yandex_api, "_request", return_value={"warehouses": [{"id": 1}]}):
            self.assertEqual(len(yandex_api.get_fulfillment_warehouses("key")), 1)
        with mock.patch.object(
            yandex_api,
            "_request",
            side_effect=[
                {"offerMappings": [{"offer": {"offerId": "A"}}], "paging": {"nextPageToken": "n"}},
                {"offerMappings": [{"offer": {"offerId": "B"}}], "paging": {}},
            ],
        ):
            self.assertEqual(len(yandex_api.get_catalog("key", 1)), 2)
        item = yandex_api.normalize_catalog_item(
            {
                "offer": {
                    "offerId": " A ",
                    "name": "Name",
                    "barcodes": ["1", ""],
                    "pictures": [{"url": "https://img.test/a.jpg"}],
                    "archived": True,
                },
                "mapping": {"marketSku": 10},
            }
        )
        self.assertEqual(item["barcode"], "1")
        self.assertEqual(item["image_url"], "https://img.test/a.jpg")

        with mock.patch.object(
            yandex_api,
            "_request",
            side_effect=[
                {
                    "warehouses": [
                        {
                            "warehouseId": 7,
                            "offers": [
                                {
                                    "offerId": "A",
                                    "stocks": [{"type": "AVAILABLE", "count": 4}],
                                }
                            ],
                        }
                    ],
                    "paging": {"nextPageToken": "n"},
                },
                {"warehouses": [], "paging": {}},
            ],
        ):
            rows = yandex_api.get_stocks("key", 1)
        self.assertEqual(rows[0]["warehouse_id"], 7)
        self.assertEqual(yandex_api.available_quantity(rows[0]["stocks"]), 4)
        self.assertEqual(yandex_api.available_quantity([{"type": "AVAILABLE", "count": "bad"}]), 0)
        self.assertEqual(yandex_api.available_quantity([]), 0)
        self.assertEqual(
            yandex_api.stock_by_type(
                [
                    {"type": "AVAILABLE", "count": 2},
                    {"type": "AVAILABLE", "count": 3},
                    {"type": "DEFECT", "count": "bad"},
                ]
            ),
            {"AVAILABLE": 5},
        )


if __name__ == "__main__":
    unittest.main()
