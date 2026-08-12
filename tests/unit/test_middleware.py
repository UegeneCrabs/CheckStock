import unittest
from unittest import mock

from fastapi import Request
from starlette.responses import Response

from app.web import middleware


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 10000),
            "server": ("test", 80),
        }
    )


class RequestLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, method: str, path: str, status: int) -> Response:
        async def call_next(request: Request) -> Response:
            return Response(status_code=status)

        return await middleware.request_logging_middleware(_request(method, path), call_next)

    async def test_successful_read_is_debug_and_has_request_id(self) -> None:
        with mock.patch.object(middleware.logger, "debug") as debug:
            response = await self._run("GET", "/stock", 200)

        debug.assert_called_once()
        self.assertTrue(response.headers["X-Request-ID"])

    async def test_mutation_and_errors_use_visible_levels(self) -> None:
        with mock.patch.object(middleware.logger, "info") as info:
            await self._run("POST", "/api/rnp/sync", 200)
        with mock.patch.object(middleware.logger, "warning") as warning:
            await self._run("GET", "/missing", 404)
        with mock.patch.object(middleware.logger, "error") as error:
            await self._run("GET", "/failed", 500)

        info.assert_called_once()
        warning.assert_called_once()
        error.assert_called_once()

    async def test_health_and_static_requests_are_quiet(self) -> None:
        with (
            mock.patch.object(middleware.logger, "debug") as debug,
            mock.patch.object(middleware.logger, "info") as info,
        ):
            await self._run("GET", "/healthz", 200)
            await self._run("GET", "/static/style.css", 200)

        debug.assert_not_called()
        info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
