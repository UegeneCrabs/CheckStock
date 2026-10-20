"""External API failures must never look like a complete source collection."""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.config import WbStandardCostsSettings
from app.wb import standard_costs as source


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.start, self.end = date(2026, 10, 12), date(2026, 10, 18)

    def test_finance_reads_until_204_and_advances_cursor(self):
        with patch.object(
            source.api,
            "request",
            side_effect=[
                [{"rrdId": 10}],
                [{"rrdId": 20}],
                None,
            ],
        ) as request:
            self.assertEqual(source._finance("test", self.start, self.end), [{"rrdId": 10}, {"rrdId": 20}])
        self.assertEqual([c.kwargs["json_body"]["rrdId"] for c in request.call_args_list], [0, 10, 20])

    def test_finance_rejects_duplicates_stalled_cursor_and_invalid_shape(self):
        for responses in (
            [[{"rrdId": 10}, {"rrdId": 10}]],
            [[{"rrdId": 10}], [{"rrdId": 9}]],
            [[{"rrdId": 10}], [{"rrdId": 10}]],
            [{"error": "not a report"}],
        ):
            with (
                self.subTest(responses=responses),
                patch.object(source.api, "request", side_effect=responses),
            ):
                with self.assertRaises(ValueError):
                    source._finance("test", self.start, self.end)

    def test_finance_propagates_failure_after_first_page(self):
        with patch.object(source.api, "request", side_effect=[[{"rrdId": 10}], TimeoutError]):
            with self.assertRaises(TimeoutError):
                source._finance("test", self.start, self.end)

    def test_pagination_limits_fail_instead_of_returning_partial_data(self):
        limits = WbStandardCostsSettings(finance_max_pages=1, card_max_pages=1, card_page_size=1)
        with patch.object(source, "settings", SimpleNamespace(wb_standard_costs=limits)):
            with patch.object(source.api, "request", return_value=[{"rrdId": 10}]):
                with self.assertRaises(ValueError):
                    source._finance("test", self.start, self.end)
            with patch.object(
                source.api,
                "request",
                return_value={
                    "cards": [{"nmID": 1}],
                    "cursor": {"total": 1, "nmID": 1, "updatedAt": "date"},
                },
            ):
                with self.assertRaises(ValueError):
                    source._cards("test")

    def test_cards_follow_cursor_and_reject_duplicate_page(self):
        first = {"cards": [{"nmID": 1}], "cursor": {"total": 1, "nmID": 1, "updatedAt": "date"}}
        last = {"cards": [], "cursor": {"total": 0}}
        with patch.object(
            source, "settings", SimpleNamespace(wb_standard_costs=WbStandardCostsSettings(card_page_size=1))
        ):
            with patch.object(source.api, "request", side_effect=[first, last]) as request:
                self.assertEqual(source._cards("test"), [{"nmID": 1}])
                self.assertEqual(request.call_args.kwargs["json_body"]["settings"]["cursor"]["nmID"], 1)
            with patch.object(source.api, "request", side_effect=[first, first]):
                with self.assertRaises(ValueError):
                    source._cards("test")
            with patch.object(
                source.api, "request", return_value={"cards": [{"nmID": 1}], "cursor": {"total": 1}}
            ):
                with self.assertRaises(ValueError):
                    source._cards("test")

    def test_storage_waits_for_done_then_downloads(self):
        replies = [{"data": {"taskId": "task"}}]
        replies += [{"data": {"status": status}} for status in ("new", "processing", "done")]
        replies += [[{"nmId": 1}]]
        with (
            patch.object(source.api, "request", side_effect=replies) as request,
            patch.object(source, "sleep"),
        ):
            self.assertEqual(source._storage("test", self.start, self.end), [{"nmId": 1}])
            self.assertTrue(request.call_args.args[1].endswith("/download"))

    def test_storage_error_and_timeout_do_not_download(self):
        with patch.object(
            source,
            "settings",
            SimpleNamespace(wb_standard_costs=WbStandardCostsSettings(storage_poll_attempts=1)),
        ):
            for status, error in (("error", ValueError), ("processing", TimeoutError)):
                with (
                    self.subTest(status=status),
                    patch.object(
                        source.api,
                        "request",
                        side_effect=[
                            {"data": {"taskId": "task"}},
                            {"data": {"status": status}},
                        ],
                    ) as request,
                    patch.object(source, "sleep"),
                ):
                    with self.assertRaises(error):
                        source._storage("test", self.start, self.end)
                    self.assertEqual(request.call_count, 2)
