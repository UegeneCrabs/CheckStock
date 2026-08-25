import unittest
from unittest import mock

from app import background


class BackgroundSyncTests(unittest.TestCase):
    def test_sync_group_isolates_failed_targets(self) -> None:
        calls = []

        def failed():
            calls.append("failed")
            raise RuntimeError("unavailable")

        def successful():
            calls.append("successful")
            return {"updated": 3}

        with mock.patch.object(background.logger, "exception") as log_exception:
            report = background._run_sync_group(
                "stocks",
                (("WB", failed), ("OZON", successful)),
            )

        self.assertEqual(calls, ["failed", "successful"])
        self.assertEqual(report.succeeded, ("OZON",))
        self.assertEqual(report.failed[0].target, "WB")
        self.assertEqual(report.failed[0].error_type, "RuntimeError")
        log_exception.assert_called_once()

    def test_sales_sync_refreshes_orders_and_wb_advertising(self) -> None:
        calls = []

        with (
            mock.patch.object(
                background.sales_service,
                "sync_all",
                side_effect=lambda: calls.append("orders"),
            ),
            mock.patch.object(
                background.advertising_sync,
                "sync_all",
                side_effect=lambda: calls.append("advertising"),
            ),
        ):
            report = background._sync_sales_and_advertising()

        self.assertEqual(calls, ["orders", "advertising"])
        self.assertEqual(report.succeeded, ("orders", "WB advertising"))
        self.assertEqual(report.failed, ())

    def test_sales_background_job_uses_combined_refresh(self) -> None:
        jobs = {job.name: job for job in background._jobs(mock.Mock())}

        self.assertIs(jobs["sales_sync"].callback, background._sync_sales_and_advertising)
        self.assertEqual(jobs["sales_sync"].next_delay(), background.settings.sales_sync_interval_seconds)

    def test_wb_stock_jobs_run_at_fixed_moscow_hours(self) -> None:
        ready = mock.Mock()
        with mock.patch.object(
            background,
            "_seconds_until_next_moscow_run",
            side_effect=lambda hour: hour * 100,
        ):
            jobs = {job.name: job for job in background._wb_stock_history_jobs(ready)}

        self.assertIs(jobs["wb_stock_sync_10_msk"].callback, background.wb_sync.sync_all)
        self.assertEqual(jobs["wb_stock_sync_10_msk"].startup_delay_seconds, 1000)
        self.assertIs(
            jobs["marketplace_stock_sync_and_history_23_msk"].callback,
            background.stock_history.sync_marketplaces_and_save_daily_history,
        )
        self.assertEqual(
            jobs["marketplace_stock_sync_and_history_23_msk"].startup_delay_seconds,
            2300,
        )
        self.assertIs(
            jobs["fulfillment_stock_history_00_msk"].callback,
            background.stock_history.save_previous_day_fulfillment_history,
        )
        self.assertEqual(jobs["fulfillment_stock_history_00_msk"].startup_delay_seconds, 0)

    def test_reference_data_job_checks_for_weekly_refresh_daily(self) -> None:
        jobs = {job.name: job for job in background._jobs(mock.Mock())}

        job = jobs["unit_economics_1c_reference_sync"]
        self.assertIs(job.callback, background.unit_reference_sync.sync_due)
        self.assertEqual(job.next_delay(), 24 * 60 * 60)

    def test_wallet_price_job_runs_every_five_minutes_without_replacing_full_sync(self) -> None:
        jobs = {job.name: job for job in background._unit_economics_1c_price_jobs()}

        full_sync = jobs["unit_economics_1c_sync"]
        wallet_sync = jobs["unit_economics_1c_wallet_sync"]
        self.assertIs(full_sync.callback, background.unit_economics_1c.sync_prices_due)
        self.assertEqual(
            full_sync.next_delay(),
            background.settings.unit_economics_1c_price_sync_interval_seconds,
        )
        self.assertIs(wallet_sync.callback, background.unit_economics_1c.sync_wallet_prices)
        self.assertEqual(wallet_sync.next_delay(), 5 * 60)

    def test_source_data_job_runs_nightly(self) -> None:
        jobs = {job.name: job for job in background._jobs(mock.Mock())}

        job = jobs["unit_economics_1c_source_sync"]
        self.assertIs(job.callback, background.unit_source_sync.sync_all)
        with mock.patch.object(background, "_seconds_until_next_moscow_run", return_value=123) as delay:
            self.assertEqual(job.next_delay(), 123)
        delay.assert_called_once_with(background.settings.unit_economics_1c_source_sync_hour)

    def test_token_refresh_runs_only_when_due(self) -> None:
        with (
            mock.patch.object(background.db, "get_last_token_check", return_value="timestamp"),
            mock.patch.object(background.token_watch, "should_refresh", return_value=False),
            mock.patch.object(background.token_watch, "refresh_token_info") as refresh,
        ):
            result = background._refresh_token_info()

        self.assertFalse(result.refreshed)
        refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
