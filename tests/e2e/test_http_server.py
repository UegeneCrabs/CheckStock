import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from app import db
from app.repositories import core


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class HttpServerEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary_directory = tempfile.TemporaryDirectory()
        seed_path = Path(cls.temporary_directory.name) / "admin.json"
        seed_path.write_text(
            json.dumps(
                {
                    "full_name": "E2E Admin",
                    "google_email": "e2e@example.com",
                    "login": "e2e-admin",
                    "password": "e2e-password",
                }
            ),
            encoding="utf-8",
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]

        environment = os.environ.copy()
        environment.update(
            {
                "CHECKSTOCK_DB_PATH": str(Path(cls.temporary_directory.name) / "e2e.db"),
                "CHECKSTOCK_ADMIN_SEED_PATH": str(seed_path),
                "CHECKSTOCK_DISABLE_BACKGROUND_SYNC": "1",
                "CHECKSTOCK_LOG_LEVEL": "WARNING",
            }
        )
        cls.database_path = Path(cls.temporary_directory.name) / "e2e.db"
        cls.database_patch = mock.patch.object(core, "DB_PATH", cls.database_path)
        cls.database_patch.start()
        db.init_db()
        db.replace_catalog(
            "rimili",
            "WB",
            [
                {
                    "article": "A-1",
                    "barcode": "460000000001",
                    "name": "E2E Product",
                    "mp_sku": "1001",
                    "mp_product_id": "P-1",
                    "image_url": "",
                }
            ],
            "2026-08-12T10:00:00+00:00",
        )
        cls.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
                "--no-access-log",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        startup_error = None
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                startup_error = f"server exited with code {cls.process.returncode}"
                break
            try:
                with urllib.request.urlopen(cls.url("/healthz"), timeout=0.5) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        cls.tearDownClass()
        raise RuntimeError(startup_error or "server did not become ready")

    @classmethod
    def tearDownClass(cls) -> None:
        process = getattr(cls, "process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        temporary_directory = getattr(cls, "temporary_directory", None)
        database_patch = getattr(cls, "database_patch", None)
        if database_patch is not None:
            database_patch.stop()
        if temporary_directory is not None:
            temporary_directory.cleanup()

    @classmethod
    def url(cls, path: str) -> str:
        return f"http://127.0.0.1:{cls.port}{path}"

    @classmethod
    def authenticated_opener(cls):
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        credentials = urllib.parse.urlencode({"login": "e2e-admin", "password": "e2e-password"}).encode()
        with opener.open(cls.url("/login"), data=credentials, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError(f"login failed: {response.status}")
        return opener

    @classmethod
    def post_json(cls, opener, path: str, payload: dict):
        request = urllib.request.Request(
            cls.url(path),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with opener.open(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    @classmethod
    def post_form(cls, opener, path: str, payload: dict):
        request = urllib.request.Request(cls.url(path), data=urllib.parse.urlencode(payload).encode())
        with opener.open(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_login_page_is_served_with_request_id(self) -> None:
        request = urllib.request.Request(self.url("/login"), headers={"X-Request-ID": "e2e-login"})

        with urllib.request.urlopen(request, timeout=3) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["X-Request-ID"], "e2e-login")
        self.assertIn("<form", body)

    def test_healthcheck_is_public(self) -> None:
        with urllib.request.urlopen(self.url("/healthz"), timeout=3) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertEqual(body, '{"status":"ok"}')

    def test_readinesscheck_includes_database(self) -> None:
        with urllib.request.urlopen(self.url("/readyz"), timeout=3) as response:
            body = response.read().decode("utf-8")

        self.assertEqual(response.status, 200)
        self.assertEqual(body, '{"status":"ok","database":"ok"}')

    def test_protected_page_redirects_to_login(self) -> None:
        opener = urllib.request.build_opener(NoRedirectHandler())

        with self.assertRaises(urllib.error.HTTPError) as error:
            opener.open(self.url("/"), timeout=3)

        self.assertEqual(error.exception.code, 303)
        self.assertEqual(error.exception.headers["Location"], "/login")

    def test_protected_api_returns_json_unauthorized(self) -> None:
        request = urllib.request.Request(
            self.url("/api/sales"),
            headers={"Accept": "application/json", "X-Request-ID": "e2e-unauthorized"},
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)

        payload = json.loads(error.exception.read().decode("utf-8"))
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(error.exception.headers["X-Request-ID"], "e2e-unauthorized")
        self.assertFalse(payload["ok"])

    def test_invalid_credentials_are_rejected(self) -> None:
        credentials = urllib.parse.urlencode({"login": "e2e-admin", "password": "wrong-password"}).encode()
        request = urllib.request.Request(self.url("/login"), data=credentials)

        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)

        body = error.exception.read().decode("utf-8")
        self.assertEqual(error.exception.code, 401)
        self.assertIn("Неверный логин или пароль", body)

    def test_authenticated_user_can_open_core_pages(self) -> None:
        opener = self.authenticated_opener()

        for path in ("/", "/stock", "/stock-2", "/sales", "/admin"):
            with self.subTest(path=path):
                with opener.open(self.url(path), timeout=5) as response:
                    body = response.read()
                self.assertEqual(response.status, 200)
                self.assertTrue(body)

    def test_full_stock_service_chain(self) -> None:
        opener = self.authenticated_opener()
        status, added = self.post_json(
            opener,
            "/stock/rimili/add-ff-items",
            {
                "fulfillment": "E2E Source",
                "marketplace": "WB",
                "note": "E2E initial stock",
                "confirmed": True,
                "items": [{"code": "A-1", "quantity": 6}],
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(added["ok"])

        with opener.open(self.url("/stock/rimili/ff-cell?ff=E2E%20Source&mp=WB"), timeout=5) as response:
            source_stock = json.loads(response.read().decode("utf-8"))["stock"]
        self.assertEqual(source_stock["A-1"], 6)

        status, transferred = self.post_form(
            opener,
            "/stock/rimili/transfer",
            {
                "from_fulfillment": "E2E Source",
                "from_marketplace": "WB",
                "to_fulfillment": "E2E Destination",
                "to_marketplace": "WB",
                "note": "E2E transfer",
                "items": json.dumps([{"code": "A-1", "quantity": 4}]),
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(transferred["results"][0]["quantity"], 4)
        with opener.open(
            self.url("/stock/rimili/transfers/in-transit?mp=WB"), timeout=5
        ) as response:
            transit = json.loads(response.read().decode("utf-8"))["batches"][0]
        status, received = self.post_json(
            opener,
            f"/stock/rimili/transfers/{transferred['transfer_id']}/receive",
            {
                "items": [{"item_id": transit["items"][0]["id"], "quantity": 4}],
                "note": "E2E received",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(received["status"], "received")

        status, shipped = self.post_form(
            opener,
            "/stock/rimili/shipment",
            {
                "fulfillment": "E2E Destination",
                "marketplace": "WB",
                "items": json.dumps([{"code": "A-1", "quantity": 2}]),
                "note": "E2E shipment",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(shipped["ok"])

        with opener.open(self.url("/stock/rimili/ff-cell?ff=E2E%20Source&mp=WB"), timeout=5) as response:
            source_stock = json.loads(response.read().decode("utf-8"))["stock"]
        with opener.open(self.url("/stock/rimili/ff-cell?ff=E2E%20Destination&mp=WB"), timeout=5) as response:
            destination_stock = json.loads(response.read().decode("utf-8"))["stock"]
        self.assertEqual(source_stock["A-1"], 2)
        self.assertEqual(destination_stock["A-1"], 2)

        with opener.open(self.url("/stock/rimili/operations"), timeout=5) as response:
            history = response.read().decode("utf-8")
        self.assertIn("E2E shipment", history)
        with opener.open(self.url("/stock/rimili/operations/xlsx"), timeout=5) as response:
            workbook = response.read()
        self.assertTrue(workbook.startswith(b"PK"))

    def test_full_rnp_strategy_and_action_chain(self) -> None:
        opener = self.authenticated_opener()
        today = time.strftime("%Y-%m-%d")
        status, strategy = self.post_json(
            opener,
            "/api/rnp/strategy",
            {
                "store": "rimili",
                "marketplace": "WB",
                "article": "A-1",
                "strategy": "E2E growth",
                "date_from": today,
                "date_to": today,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(strategy["ok"])
        status, action = self.post_json(
            opener,
            "/api/rnp/action",
            {
                "store": "rimili",
                "marketplace": "WB",
                "article": "A-1",
                "note": "E2E action",
                "action_date": today,
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(action["ok"])
        month = today[:7]
        with opener.open(
            self.url(f"/api/rnp?month={month}&marketplace=WB&store=rimili"), timeout=5
        ) as response:
            dashboard = json.loads(response.read().decode("utf-8"))
        product = next(item for item in dashboard["products"] if item["article"] == "A-1")
        self.assertEqual(product["strategy"]["strategy"], "E2E growth")
        self.assertEqual(product["actions"][today][0]["note"], "E2E action")

    def test_full_admin_user_chain(self) -> None:
        opener = self.authenticated_opener()
        status, created = self.post_form(
            opener,
            "/admin/users",
            {
                "full_name": "E2E Employee",
                "google_email": "employee-e2e@example.com",
                "login": "e2e-employee",
                "password": "employee-password",
                "role": "user",
                "stores": "rimili",
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(created["ok"])

        employee = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        credentials = urllib.parse.urlencode(
            {"login": "e2e-employee", "password": "employee-password"}
        ).encode()
        with employee.open(self.url("/login"), data=credentials, timeout=5) as response:
            self.assertEqual(response.status, 200)
        with employee.open(self.url("/stock/rimili"), timeout=5) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as denied:
            employee.open(self.url("/admin"), timeout=5)
        self.assertEqual(denied.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
