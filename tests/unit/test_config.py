import os
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_environment_overrides_runtime_values(self) -> None:
        environment = {
            "CHECKSTOCK_DB_PATH": "custom/checkstock.db",
            "CHECKSTOCK_LOG_LEVEL": "debug",
            "CHECKSTOCK_DISABLE_BACKGROUND_SYNC": "true",
            "CHECKSTOCK_STOCK_DETAIL_PAGE_SIZE": "42",
            "CHECKSTOCK_OZON_REQUEST_ATTEMPTS": "7",
            "CHECKSTOCK_RNP_REPORT_POLL_ATTEMPTS": "25",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            configured = Settings.from_env()

        self.assertEqual(configured.database_path, Path("custom/checkstock.db"))
        self.assertEqual(configured.log_level, "DEBUG")
        self.assertFalse(configured.background_sync_enabled)
        self.assertEqual(configured.stock_detail_page_size, 42)
        self.assertEqual(configured.ozon_request_attempts, 7)
        self.assertEqual(configured.rnp_report_poll_attempts, 25)

    def test_invalid_integer_fails_during_startup(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CHECKSTOCK_SESSION_TTL_DAYS": "not-a-number"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "CHECKSTOCK_SESSION_TTL_DAYS"):
                Settings.from_env()

    def test_security_iterations_have_a_safe_minimum(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CHECKSTOCK_PBKDF2_ITERATIONS": "1000"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "at least 100000"):
                Settings.from_env()

    def test_schedule_hour_is_validated(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CHECKSTOCK_CATALOG_SYNC_HOUR": "24"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "at most 23"):
                Settings.from_env()

    def test_invalid_boolean_fails_during_startup(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CHECKSTOCK_DISABLE_BACKGROUND_SYNC": "ture"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "CHECKSTOCK_DISABLE_BACKGROUND_SYNC"):
                Settings.from_env()

    def test_retry_attempts_have_a_safe_upper_bound(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CHECKSTOCK_OZON_REQUEST_ATTEMPTS": "1000"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "at most 20"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
