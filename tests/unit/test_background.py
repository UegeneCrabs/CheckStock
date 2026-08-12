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
