import io
import unittest
import urllib.error
from unittest import mock

from app.ozon import api as ozon_api
from app.wb import api as wb_api
from app.yandex import api as yandex_api


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return self.payload


def http_error(url: str, status: int, payload: bytes, headers: dict | None = None):
    return urllib.error.HTTPError(url, status, "failed", headers or {}, io.BytesIO(payload))


class MarketplaceRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        wb_api._WB_LAST_REQUEST_AT.clear()
        wb_api._WB_RATE_LOCKS.clear()

    def test_wb_retries_rate_limit_and_returns_json(self) -> None:
        error = http_error("https://wb.test", 429, b'{"detail":"slow down"}', {"Retry-After": "0"})
        with (
            mock.patch.object(wb_api, "REQUEST_ATTEMPTS", 2),
            mock.patch.object(
                wb_api.urllib.request,
                "urlopen",
                side_effect=[error, FakeResponse(b'{"ok":true}')],
            ) as urlopen,
            mock.patch.object(wb_api.time, "sleep") as sleep,
            mock.patch.object(wb_api.logger, "warning"),
        ):
            result = wb_api._request("GET", "https://wb.test", "token")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0.5)

    def test_wb_spaces_all_sales_funnel_requests_for_one_account(self) -> None:
        url = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products"
        with (
            mock.patch.object(wb_api, "REQUEST_ATTEMPTS", 1),
            mock.patch.object(wb_api.time, "monotonic", side_effect=[100.0, 105.0, 120.1]),
            mock.patch.object(wb_api.time, "sleep") as sleep,
            mock.patch.object(
                wb_api.urllib.request,
                "urlopen",
                side_effect=[FakeResponse(b'{}'), FakeResponse(b'{}')],
            ) as urlopen,
        ):
            wb_api._request("POST", url, "seller-token", json_body={"request": 1})
            wb_api._request("POST", url, "seller-token", json_body={"request": 2})

        self.assertAlmostEqual(sleep.call_args.args[0], 15.1)
        self.assertEqual(urlopen.call_count, 2)

    def test_wb_spaces_warehouse_stock_requests_across_callers(self) -> None:
        url = (
            "https://seller-analytics-api.wildberries.ru/"
            "api/analytics/v1/stocks-report/wb-warehouses"
        )
        with (
            mock.patch.object(wb_api, "REQUEST_ATTEMPTS", 1),
            mock.patch.object(wb_api.time, "monotonic", side_effect=[100.0, 105.0, 120.1]),
            mock.patch.object(wb_api.time, "sleep") as sleep,
            mock.patch.object(
                wb_api.urllib.request,
                "urlopen",
                side_effect=[FakeResponse(b"{}"), FakeResponse(b"{}")],
            ),
        ):
            wb_api._request("POST", url, "seller-token", json_body={})
            wb_api._request("POST", url, "seller-token", json_body={})

        self.assertAlmostEqual(sleep.call_args.args[0], 15.1)

    def test_wb_rate_limits_every_used_api_group(self) -> None:
        expected = {
            "https://advert-api.wildberries.ru/adv/v3/fullstats": ("advert_stats", 20.1),
            "https://advert-api.wildberries.ru/adv/v1/promotion/count": ("advertising", 0.21),
            "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products": (
                "analytics",
                20.1,
            ),
            "https://statistics-api.wildberries.ru/api/v1/supplier/orders": (
                "statistics",
                60.1,
            ),
            "https://supplies-api.wildberries.ru/api/v1/supplies": ("supplies", 2.05),
            "https://content-api.wildberries.ru/content/v2/get/cards/list": ("content", 0.61),
            "https://discounts-prices-api.wildberries.ru/api/v2/list/goods/filter": (
                "prices",
                0.61,
            ),
            "https://marketplace-api.wildberries.ru/api/v3/orders": ("marketplace", 0.21),
        }

        for url, rate_limit in expected.items():
            with self.subTest(url=url):
                self.assertEqual(wb_api._rate_limit_for_url(url), rate_limit)

    def test_ozon_retries_server_error(self) -> None:
        error = http_error("https://ozon.test", 503, b'{"message":"unavailable"}')
        with (
            mock.patch.object(ozon_api, "MAX_ATTEMPTS", 2),
            mock.patch.object(ozon_api, "_backoff_pause", return_value=0),
            mock.patch.object(
                ozon_api.urllib.request,
                "urlopen",
                side_effect=[error, FakeResponse(b'{"result":"ok"}')],
            ) as urlopen,
            mock.patch.object(ozon_api.time, "sleep") as sleep,
            mock.patch.object(ozon_api.logger, "warning"),
        ):
            result = ozon_api._request("/v1/test", "client", "key", {})

        self.assertEqual(result, {"result": "ok"})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0)

    def test_yandex_stops_after_configured_attempts(self) -> None:
        errors = [
            http_error("https://yandex.test", 503, b'{"message":"unavailable"}'),
            http_error("https://yandex.test", 503, b'{"message":"unavailable"}'),
        ]
        with (
            mock.patch.object(yandex_api, "MAX_ATTEMPTS", 2),
            mock.patch.object(yandex_api, "RETRY_BACKOFF_SECONDS", 0),
            mock.patch.object(yandex_api.urllib.request, "urlopen", side_effect=errors) as urlopen,
            mock.patch.object(yandex_api.time, "sleep") as sleep,
            mock.patch.object(yandex_api.logger, "warning"),
        ):
            with self.assertRaises(yandex_api.YandexApiError) as error:
                yandex_api._request("/v2/test", "key")

        self.assertEqual(error.exception.status, 503)
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
