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

    def test_advertising_sync_refreshes_only_wb_advertising(self) -> None:
        calls = []

        with mock.patch.object(
            background.advertising_sync,
            "sync_all",
            side_effect=lambda: calls.append("advertising"),
        ):
            report = background._sync_wb_advertising()

        self.assertEqual(calls, ["advertising"])
        self.assertEqual(report.succeeded, ("WB advertising",))
        self.assertEqual(report.failed, ())

    def test_advertising_runs_every_fifteen_minutes_without_sales_or_rnp_jobs(self) -> None:
        jobs = {job.name: job for job in background._jobs(mock.Mock())}

        self.assertNotIn("sales_sync", jobs)
        self.assertNotIn("rnp_analytics_sync", jobs)
        self.assertIs(jobs["wb_advertising_sync"].callback, background._sync_wb_advertising)
        self.assertEqual(jobs["wb_advertising_sync"].next_delay(), 15 * 60)

    def test_stock_history_jobs_run_at_fixed_moscow_hours(self) -> None:
        ready = mock.Mock()
        with mock.patch.object(
            background,
            "_seconds_until_next_moscow_run",
            side_effect=lambda hour: hour * 100,
        ):
            jobs = {job.name: job for job in background._wb_stock_history_jobs(ready)}

        self.assertNotIn("wb_stock_sync_10_msk", jobs)
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

    def test_current_stock_job_refreshes_all_marketplaces_every_thirty_minutes(self) -> None:
        jobs = {job.name: job for job in background._jobs(mock.Mock())}

        self.assertIs(jobs["stock_sync"].callback, background._sync_stocks)
        self.assertEqual(jobs["stock_sync"].next_delay(), 30 * 60)
        with (
            mock.patch.object(background.wb_sync, "sync_all") as wb,
            mock.patch.object(background.ozon_sync, "sync_all") as ozon,
            mock.patch.object(background.ya_sync, "sync_all") as yandex,
        ):
            report = background._sync_stocks()

        for sync in (wb, ozon, yandex):
            sync.assert_called_once_with()
        self.assertEqual(report.succeeded, ("WB", "OZON", "YANDEX MARKET"))

    def test_configured_stock_job_only_passes_enabled_targets(self) -> None:
        selected = {
            "WB": ("rimili", "tris"),
            "OZON": (),
            "YANDEX MARKET": ("tris",),
        }
        with (
            mock.patch.object(
                background.sync_settings,
                "enabled_stores",
                side_effect=lambda name, marketplace: selected[marketplace],
            ),
            mock.patch.object(background.wb_sync, "sync_all", return_value={}) as wb,
            mock.patch.object(background.ozon_sync, "sync_all", return_value={}) as ozon,
            mock.patch.object(background.ya_sync, "sync_all", return_value={}) as yandex,
        ):
            background._sync_stocks_configured()

        wb.assert_called_once_with(("rimili", "tris"))
        ozon.assert_called_once_with(())
        yandex.assert_called_once_with(("tris",))

    def test_decision_center_has_no_background_job(self) -> None:
        jobs = {job.name: job for job in background._jobs(mock.Mock())}

        self.assertNotIn("decision_center_sync", jobs)

    def test_reference_data_job_checks_for_weekly_refresh_daily(self) -> None:
        jobs = {job.name: job for job in background._jobs(mock.Mock())}

        job = jobs["unit_economics_1c_reference_sync"]
        self.assertIs(job.callback, background.unit_reference_sync.sync_due)
        self.assertEqual(job.next_delay(), 24 * 60 * 60)

    def test_funnel_buyout_metrics_run_at_startup_and_every_four_hours(self) -> None:
        with mock.patch.object(background, "_seconds_until_next_moscow_run", return_value=123) as delay:
            jobs = {job.name: job for job in background._funnel_jobs()}
            next_delay = jobs["wb_funnel_weekly_metrics_sync"].next_delay()

        job = jobs["wb_funnel_weekly_metrics_sync"]
        self.assertIs(job.callback, background.wb_funnel_orders.sync_weekly_metrics_all)
        self.assertEqual(job.startup_delay_seconds, 0)
        self.assertEqual(next_delay, 4 * 60 * 60)
        self.assertTrue(job.interval_from_start)
        delay.assert_called_once_with(0)

        close_job = jobs["wb_funnel_previous_day_close_00_msk"]
        self.assertIs(close_job.callback, background.wb_funnel_orders.sync_previous_day_all)
        self.assertEqual(close_job.startup_delay_seconds, 123)

        refresh_job = jobs["wb_funnel_orders_sync"]
        self.assertEqual(refresh_job.next_delay(), 15 * 60)
        self.assertTrue(refresh_job.interval_from_start)

    def test_daily_margin_snapshot_runs_at_midnight_moscow(self) -> None:
        with mock.patch.object(background, "_seconds_until_next_moscow_run", return_value=123):
            jobs = {job.name: job for job in background._jobs(mock.Mock())}

        job = jobs["unit_economics_1c_daily_margin_snapshot_00_msk"]
        self.assertIs(job.callback, background.unit_margin_history.save_daily_margin_snapshots)
        self.assertEqual(job.startup_delay_seconds, 123)

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

    def test_ftp_exports_are_independent_nightly_jobs(self) -> None:
        with (
            mock.patch.object(background.ftp_export_schedule, "startup_delay_seconds", return_value=0),
            mock.patch.object(background.ftp_export_schedule, "next_delay_seconds", return_value=1200),
            mock.patch.object(background.ftp_export, "run_platform", return_value={"ok": True}) as run,
        ):
            jobs = {job.name: job for job in background._ftp_export_jobs()}
            wb_result = jobs["ftp_wb_export"].callback()
            ozon_result = jobs["ftp_ozon_export"].callback()
            wb_delay = jobs["ftp_wb_export"].next_delay()

        self.assertEqual(set(jobs), {"ftp_wb_export", "ftp_ozon_export"})
        self.assertEqual(wb_result, {"ok": True})
        self.assertEqual(ozon_result, {"ok": True})
        self.assertEqual(wb_delay, 1200)
        self.assertEqual(run.call_args_list, [mock.call("wb"), mock.call("ozon")])

    def test_ftp_job_requires_enabled_setting_and_open_retry_window(self) -> None:
        with (
            mock.patch.object(background.ftp_export_schedule, "startup_delay_seconds", return_value=0),
            mock.patch.object(background, "_job_enabled", return_value=True) as enabled,
            mock.patch.object(background.ftp_export_schedule, "should_attempt", return_value=True) as due,
        ):
            job = {item.name: item for item in background._ftp_export_jobs()}["ftp_wb_export"]
            is_enabled = job.is_enabled()

        self.assertTrue(is_enabled)
        enabled.assert_called_once_with("ftp_wb_export")
        due.assert_called_once_with("ftp_wb_export")


if __name__ == "__main__":
    unittest.main()
