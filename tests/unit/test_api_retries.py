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
