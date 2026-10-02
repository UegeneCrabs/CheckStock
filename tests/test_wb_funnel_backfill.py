"""Backfill safety checks: no network or production/local application database."""

import importlib
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

DAY = date(2026, 7, 11)


def product(article=123, **metrics):
    return {
        "product": {"nmId": article, "vendorCode": "ARTICLE", "title": "Product"},
        "statistic": {
            "selected": {
                "orderCount": 10,
                "orderSum": 1000,
                "cancelCount": 3,
                "cancelSum": 300,
                "buyoutCount": 7,
                "buyoutSum": 700,
                "conversions": {"buyoutPercent": 70},
                **metrics,
            }
        },
    }


def page(*products):
    return {"data": {"products": list(products)}}


class BackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch("dotenv.dotenv_values", return_value={}), patch.dict(os.environ, {}, clear=True):
            cls.script = importlib.import_module("scripts.sync.backfill_wb_funnel")

    def setUp(self):
        self.script.STOP.clear()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "state.json"
        self.checkpoint = self.script.Checkpoint(self.path, DAY, DAY, ["rimili"])

    def test_offset_pagination_and_deleted_products(self):
        client = Mock()
        client.request.side_effect = [page(product(1), product(2)), page(product(3))]
        with patch.object(self.script, "PAGE_SIZE", 2):
            values = self.script.fetch_day(client, DAY)
        self.assertEqual([row[0] for row in values], ["1", "2", "3"])
        payloads = [call.args[0] for call in client.request.call_args_list]
        self.assertEqual([p["offset"] for p in payloads], [0, 2])
        self.assertTrue(all(p["skipDeletedNm"] is False and p["nmIds"] == [] for p in payloads))
        self.assertEqual(payloads[0]["selectedPeriod"], {"start": str(DAY), "end": str(DAY)})
        self.assertEqual(values[0][3:9], (10, 1000, 3, 300, 7, 700))

    def test_full_last_page_requires_empty_page(self):
        client = Mock()
        client.request.side_effect = [page(product()), page()]
        with patch.object(self.script, "PAGE_SIZE", 1):
            self.assertEqual(len(self.script.fetch_day(client, DAY)), 1)
        self.assertEqual(client.request.call_count, 2)

    def test_second_page_failure_does_not_write_or_checkpoint(self):
        client = Mock()
        client.request.side_effect = [page(product()), RuntimeError("network")]
        with (
            patch.object(self.script, "PAGE_SIZE", 1),
            patch.object(self.script.funnel_orders, "_replace_day") as save,
        ):
            with self.assertRaises(RuntimeError):
                self.script.load_store("rimili", client, [DAY], self.checkpoint)
        save.assert_not_called()
        self.assertFalse(self.checkpoint.saved("rimili", DAY))

    def test_malformed_response_and_missing_metrics_do_not_become_zero(self):
        for response in (
            {},
            {"data": {}},
            page({}),
            page(product(cancelCount=None)),
            page(product(orderCount=1.5)),
        ):
            with self.subTest(response=response):
                client = Mock()
                client.request.return_value = response
                with self.assertRaises(self.script.PermanentError):
                    self.script.fetch_day(client, DAY)

    def test_repeated_page_is_rejected(self):
        client = Mock()
        client.request.return_value = page(product())
        with patch.object(self.script, "PAGE_SIZE", 1), self.assertRaises(self.script.PermanentError):
            self.script.fetch_day(client, DAY)

    def test_resume_skips_only_committed_days(self):
        client = Mock()
        client.request.return_value = page(product())
        with patch.object(self.script.funnel_orders, "_replace_day") as save:
            self.script.load_store("rimili", client, [DAY], self.checkpoint)
            reloaded = self.script.Checkpoint(self.path, DAY, DAY, ["rimili"])
            self.script.load_store("rimili", client, [DAY], reloaded)
        save.assert_called_once()
        client.request.assert_called_once()
        self.assertEqual(json.loads(self.path.read_text())["completed"]["rimili"][str(DAY)], 1)

    def test_database_failure_does_not_checkpoint(self):
        client = Mock()
        client.request.return_value = page(product())
        with patch.object(self.script.funnel_orders, "_replace_day", side_effect=RuntimeError("db")):
            with self.assertRaises(RuntimeError):
                self.script.load_store("rimili", client, [DAY], self.checkpoint)
        self.assertFalse(self.checkpoint.saved("rimili", DAY))

    def test_checkpoint_cannot_be_reused_for_other_store_or_period(self):
        self.checkpoint.update(status="running")
        with self.assertRaises(self.script.PermanentError):
            self.script.Checkpoint(self.path, DAY, DAY, ["tris"])

    def test_retry_after_and_reset_are_respected(self):
        delay = self.script.retry_delay
        self.assertEqual(delay({"X-Ratelimit-Retry": "120"}, 1), 122)
        self.assertEqual(delay({"X-Ratelimit-Reset": "1800000600"}, 1, now=1800000000), 602)
        self.assertGreaterEqual(delay({"Retry-After": "Thu, 01 Jan 1970 00:05:00 GMT"}, 1, now=0), 302)
        self.assertEqual(delay({}, 100), 900)

    def test_retry_is_paced_and_auth_failure_is_not_retried(self):
        pace = Mock()
        client = self.script.Client("synthetic-token", pace, "rimili")
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(page()).encode()
        error = urllib.error.HTTPError(
            "https://example.invalid", 429, "limited", {"Retry-After": "120"}, None
        )
        with patch.object(self.script.urllib.request, "urlopen", side_effect=[error, response]):
            self.assertEqual(client.request({}), page())
        self.assertEqual(pace.wait.call_count, 2)
        pace.defer.assert_called_once_with(122)
        with patch.object(
            self.script.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError("https://example.invalid", 401, "auth", {}, None),
        ) as request:
            with self.assertRaises(self.script.PermanentError):
                client.request({})
        request.assert_called_once()

    def test_pace_waits_for_minimum_interval(self):
        pace = self.script.Pace()
        with (
            patch.object(self.script.time, "monotonic", return_value=100),
            patch.object(self.script, "pause") as wait,
        ):
            pace.wait()
            pace.wait()
        self.assertEqual(wait.call_args.args[0], 25)

    def test_partial_job_lock_acquisition_is_released_before_wait(self):
        import contextlib

        held, attempts = set(), [0]

        @contextlib.contextmanager
        def hold(name):
            if name == self.script.JOBS[1] and attempts[0] == 0:
                attempts[0] += 1
                raise self.script.locks.SyncJobBusyError()
            held.add(name)
            try:
                yield
            finally:
                held.remove(name)

        def pause(seconds):
            if seconds == 30:
                self.assertEqual(held, {"wb_funnel_daily_backfill"})

        with patch.object(self.script.locks, "hold", hold), patch.object(self.script, "pause", pause):
            with self.script.exclusive_funnel():
                self.assertEqual(held, {"wb_funnel_daily_backfill", *self.script.JOBS})
        self.assertEqual(held, set())

    def test_database_atomic_replacement_coverage_and_verification(self):
        from sqlalchemy.exc import IntegrityError

        from app.infrastructure.database import Database
        from app.infrastructure.orm import OrmBase
        from app.repositories import core

        database = Database(self.path.parent / "test.sqlite3")
        self.addCleanup(database.dispose)
        OrmBase.metadata.create_all(database.engine)
        with (
            patch.object(core, "database_for_path", return_value=database),
            patch("app.economics.daily_metrics.refresh_wb"),
        ):
            values = [self.script.funnel_orders._product_values(product())]
            self.script.funnel_orders._replace_day("rimili", DAY, values)
            self.script.funnel_orders._replace_day("tris", DAY, values)
            client = Mock()
            client.request.return_value = page(product(456))
            self.script.load_store("rimili", client, [DAY], self.checkpoint)
            self.script.verify(self.checkpoint)
            with self.script.db.get_connection() as conn:
                rows = conn.execute(
                    "SELECT store_slug,article,orders_count,cancel_count FROM wb_funnel_daily_orders ORDER BY store_slug"
                ).fetchall()
            self.assertEqual(
                [(r["store_slug"], r["article"]) for r in rows], [("rimili", "456"), ("tris", "123")]
            )
            self.assertEqual(rows[0]["orders_count"] - rows[0]["cancel_count"], 7)
            # A failed insert rolls back deletion of the previously committed day.
            with self.assertRaises(IntegrityError):
                self.script.funnel_orders._replace_day("rimili", DAY, [(None, "", "", 1, 1)])
            self.script.verify(self.checkpoint)


if __name__ == "__main__":
    unittest.main()
