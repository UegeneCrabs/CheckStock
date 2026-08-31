import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings, _load_local_env


class SettingsTests(unittest.TestCase):
    def test_price_sync_defaults_to_two_hours(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            configured = Settings.from_env()

        self.assertEqual(configured.unit_economics_1c_price_sync_interval_seconds, 2 * 60 * 60)
        self.assertEqual(configured.unit_economics_1c_wallet_sync_interval_seconds, 5 * 60)
        self.assertEqual(configured.unit_economics_1c_source_sync_hour, 2)
        self.assertEqual(configured.token_check_interval_seconds, 24 * 60 * 60)
        self.assertEqual(configured.wb_advertising_sync_interval_seconds, 15 * 60)
        self.assertEqual(configured.wb_funnel_orders_sync_interval_seconds, 15 * 60)
        self.assertEqual(configured.wb_storefront_dest, "-1257786")
        self.assertEqual(configured.wb_storefront_batch_size, 1_000)
        self.assertTrue(configured.ftp_export_enabled)
        self.assertEqual(configured.ftp_export_start_hour, 3)
        self.assertEqual(configured.ftp_export_start_minute, 15)
        self.assertEqual(configured.ftp_export_deadline_hour, 6)
        self.assertEqual(configured.ftp_export_retry_interval_seconds, 20 * 60)
        self.assertEqual(configured.ftp_tls, "auto")

    def test_local_env_sets_defaults_without_overriding_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                "CHECKSTOCK_DISABLE_BACKGROUND_SYNC=1\nCHECKSTOCK_FUNNEL_ORDERS_SYNC_ENABLED=0\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"CHECKSTOCK_DISABLE_BACKGROUND_SYNC": "0"},
                clear=True,
            ):
                _load_local_env(env_path)

                self.assertEqual(os.environ["CHECKSTOCK_DISABLE_BACKGROUND_SYNC"], "0")
                self.assertEqual(os.environ["CHECKSTOCK_FUNNEL_ORDERS_SYNC_ENABLED"], "0")

    def test_environment_overrides_runtime_values(self) -> None:
        environment = {
            "CHECKSTOCK_DB_PATH": "custom/checkstock.db",
            "CHECKSTOCK_DATABASE_URL": "postgresql+psycopg://checkstock:secret@db/checkstock",
            "CHECKSTOCK_LOG_LEVEL": "debug",
            "CHECKSTOCK_DISABLE_BACKGROUND_SYNC": "true",
            "CHECKSTOCK_UNIT_ECONOMICS_1C_PRICE_SYNC_ENABLED": "true",
            "CHECKSTOCK_UNIT_ECONOMICS_1C_PRICE_SYNC_INTERVAL_SECONDS": "7200",
            "CHECKSTOCK_UNIT_ECONOMICS_1C_WALLET_SYNC_INTERVAL_SECONDS": "600",
            "CHECKSTOCK_UNIT_ECONOMICS_1C_SOURCE_SYNC_HOUR": "1",
            "CHECKSTOCK_WB_STOREFRONT_DEST": "-7777777",
            "CHECKSTOCK_WB_STOREFRONT_BATCH_SIZE": "40",
            "CHECKSTOCK_STOCK_DETAIL_PAGE_SIZE": "42",
            "CHECKSTOCK_OZON_REQUEST_ATTEMPTS": "7",
            "CHECKSTOCK_RNP_REPORT_POLL_ATTEMPTS": "25",
            "CHECKSTOCK_FTP_EXPORT_START_HOUR": "4",
            "CHECKSTOCK_FTP_EXPORT_START_MINUTE": "30",
            "CHECKSTOCK_FTP_EXPORT_DEADLINE_HOUR": "7",
            "CHECKSTOCK_FTP_EXPORT_RETRY_INTERVAL_SECONDS": "600",
            "CHECKSTOCK_FTP_HOST_WB": "ftp.example.test",
            "CHECKSTOCK_FTP_TLS": "true",
            "CHECKSTOCK_FTP_MODE": "active",
            "CHECKSTOCK_FTP_PROT": "clear",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            configured = Settings.from_env()

        self.assertEqual(configured.database_path, Path("custom/checkstock.db"))
        self.assertEqual(
            configured.database_url,
            "postgresql+psycopg://checkstock:secret@db/checkstock",
        )
        self.assertEqual(configured.log_level, "DEBUG")
        self.assertFalse(configured.background_sync_enabled)
        self.assertTrue(configured.unit_economics_1c_price_sync_enabled)
        self.assertEqual(configured.unit_economics_1c_price_sync_interval_seconds, 7200)
        self.assertEqual(configured.unit_economics_1c_wallet_sync_interval_seconds, 600)
        self.assertEqual(configured.unit_economics_1c_source_sync_hour, 1)
        self.assertEqual(configured.wb_storefront_dest, "-7777777")
        self.assertEqual(configured.wb_storefront_batch_size, 40)
        self.assertEqual(configured.stock_detail_page_size, 42)
        self.assertEqual(configured.ozon_request_attempts, 7)
        self.assertEqual(configured.rnp_report_poll_attempts, 25)
        self.assertEqual(configured.ftp_export_start_hour, 4)
        self.assertEqual(configured.ftp_export_start_minute, 30)
        self.assertEqual(configured.ftp_export_deadline_hour, 7)
        self.assertEqual(configured.ftp_export_retry_interval_seconds, 600)
        self.assertEqual(configured.ftp_host_wb, "ftp.example.test")
        self.assertEqual(configured.ftp_tls, "on")
        self.assertEqual(configured.ftp_mode, "active")
        self.assertEqual(configured.ftp_prot, "clear")

    def test_invalid_integer_fails_during_startup(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CHECKSTOCK_SESSION_TTL_DAYS": "not-a-number"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "CHECKSTOCK_SESSION_TTL_DAYS"):
                Settings.from_env()

    def test_non_postgresql_database_url_is_rejected(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CHECKSTOCK_DATABASE_URL": "sqlite:///unexpected.db"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "CHECKSTOCK_DATABASE_URL"):
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

    def test_ftp_window_requires_start_before_deadline(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "CHECKSTOCK_FTP_EXPORT_START_HOUR": "6",
                "CHECKSTOCK_FTP_EXPORT_DEADLINE_HOUR": "6",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "FTP export start"):
                Settings.from_env()

    def test_invalid_ftp_mode_fails_during_startup(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CHECKSTOCK_FTP_MODE": "sometimes"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "CHECKSTOCK_FTP_MODE"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
