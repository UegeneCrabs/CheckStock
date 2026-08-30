import unittest
from datetime import UTC, date, datetime
from unittest import mock

from app import stock_history


class StockHistoryTests(unittest.TestCase):
    def test_evening_wb_sync_saves_only_successful_scopes(self) -> None:
        report = {
            "rimili": {
                "token": True,
                "fbs": {"ok": True, "count": 2},
                "fbo": {"ok": False, "error": "api"},
            },
            "tris": {
                "token": True,
                "fbs": {"ok": True, "count": 1},
                "fbo": {"ok": True, "count": 1},
            },
        }
        with (
            mock.patch.object(stock_history.wb_sync, "sync_all", return_value=report),
            mock.patch.object(
                stock_history.db,
                "replace_marketplace_stock_daily_history",
                side_effect=[2, 1, 1],
            ) as save,
        ):
            result = stock_history.sync_wb_and_save_daily_history(
                date(2026, 8, 20),
                datetime(2026, 8, 20, 20, tzinfo=UTC),
            )

        self.assertEqual(result["day"], "2026-08-20")
        self.assertEqual(result["saved"], {"rimili": {"fbs": 2}, "tris": {"fbs": 1, "fbo": 1}})
        self.assertEqual(save.call_count, 3)
        self.assertEqual(save.call_args_list[0].args[:4], ("rimili", "WB", "fbs", "2026-08-20"))

    def test_midnight_ff_snapshot_is_written_for_previous_moscow_day(self) -> None:
        with mock.patch.object(
            stock_history.db,
            "replace_fulfillment_stock_daily_history",
            return_value=42,
        ) as save:
            result = stock_history.save_previous_day_fulfillment_history(
                datetime(2026, 8, 21, 0, 0, tzinfo=stock_history.MOSCOW_TIMEZONE)
            )

        self.assertEqual(result, {"day": "2026-08-20", "saved": 42})
        self.assertEqual(save.call_args.args[0], "2026-08-20")

    def test_evening_history_covers_ozon_and_yandex(self) -> None:
        with (
            mock.patch.object(
                stock_history,
                "sync_wb_and_save_daily_history",
                return_value={"sync": {"rimili": {}}, "saved": {"rimili": {"fbs": 1}}},
            ),
            mock.patch.object(
                stock_history.ozon_sync,
                "sync_all",
                return_value={"rimili": {"ozon": {"ok": True}}},
            ),
            mock.patch.object(
                stock_history.yandex_sync,
                "sync_all",
                return_value={"rimili": {"yandex": {"ok": True}}},
            ),
            mock.patch.object(
                stock_history.db,
                "replace_marketplace_stock_daily_history",
                side_effect=[2, 3, 4, 5],
            ) as save,
        ):
            result = stock_history.sync_marketplaces_and_save_daily_history(
                date(2026, 8, 20),
                datetime(2026, 8, 20, 20, tzinfo=UTC),
            )

        self.assertEqual(result["saved"]["OZON"]["rimili"], {"fbs": 2, "fbo": 3})
        self.assertEqual(result["saved"]["YANDEX MARKET"]["rimili"], {"fbs": 4, "fbo": 5})
        self.assertEqual(save.call_count, 4)


if __name__ == "__main__":
    unittest.main()
