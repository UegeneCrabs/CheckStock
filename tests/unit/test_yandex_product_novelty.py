import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from app import db, unit_economics_yandex
from app.repositories import core, yandex_assortment
from app.repositories import unit_economics_yandex as snapshots
from app.repositories import yandex_product_statuses as repository
from app.yandex import product_novelty as sync

TODAY = date(2026, 8, 31)
NOW = "2026-08-31T08:00:00+00:00"


def order(article, day, count=1):
    return {"article": article, "day": day, "orders_count": count}


class YandexProductNoveltyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.articles = {"A", "B", "C", "D", "E"}
        for patcher in (
            mock.patch.object(core, "DB_PATH", Path(temp.name) / "novelty.db"),
            mock.patch.object(
                yandex_assortment,
                "load_active_products",
                return_value={("rimili", article) for article in self.articles} | {("tris", "A")},
            ),
            mock.patch.object(sync.tokens, "has_credentials", return_value=True),
            mock.patch.object(sync.tokens, "get_api_key", return_value="test-key"),
            mock.patch.object(sync.unit_economics_sync, "resolve_business_id", return_value=123),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        db.init_db()
        db.replace_catalog("rimili", "YANDEX MARKET", [{"article": a} for a in self.articles], NOW)

    def test_exact_seven_day_window_and_month_year_boundaries(self):
        self.assertEqual(repository.check_period(TODAY), (date(2026, 8, 3), date(2026, 8, 9)))
        self.assertEqual(repository.check_period(date(2026, 1, 10)), (date(2025, 12, 13), date(2025, 12, 19)))
        self.assertEqual(repository.check_period(date(2024, 3, 22)), (date(2024, 2, 23), date(2024, 2, 29)))
        rows = [
            order("A", "2026-08-03"),
            order("B", "2026-08-09"),
            order("C", "2026-08-02"),
            order("D", "2026-08-10"),
            order("E", "2026-08-06", 0),
        ]
        repository.save_check("rimili", self.articles, rows, TODAY, NOW)
        statuses = repository.get_statuses("rimili")
        self.assertEqual(
            {a: row["status"] for a, row in statuses.items()},
            {"A": "old", "B": "old", "C": "new", "D": "new", "E": "new"},
        )
        self.assertEqual(statuses["A"]["order_date"], "2026-08-03")

    def test_sync_loads_complete_week_with_exclusive_api_end_and_counts_cancelled_orders(self):
        raw = [
            {
                "id": 1,
                "creationDate": "2026-08-02T21:00:00Z",
                "status": "CANCELLED",
                "items": [{"offerId": "A", "count": 1, "prices": {"payment": 100}}],
            }
        ]
        with mock.patch.object(sync.unit_economics_sync.api, "get_business_orders", return_value=raw) as api:
            result = sync.sync_store("rimili", TODAY)
        self.assertTrue(result["ok"])
        api.assert_called_once_with("test-key", 123, "2026-08-03", "2026-08-10")
        self.assertEqual(repository.get_statuses("rimili")["A"]["status"], "old")
        self.assertEqual(repository.get_statuses("rimili")["B"]["status"], "new")

    def test_new_products_are_rechecked_next_day_and_can_become_ordinary(self):
        with mock.patch.object(sync.unit_economics_sync, "load_orders", return_value=[]) as loader:
            sync.sync_store("rimili", TODAY)
            self.assertTrue(sync.sync_store("rimili", TODAY)["skipped"])
            loader.assert_called_once()
        with mock.patch.object(
            sync.unit_economics_sync, "load_orders", return_value=[order("A", "2026-08-10")]
        ):
            sync.sync_store("rimili", TODAY + timedelta(days=1))
        statuses = repository.get_statuses("rimili")
        self.assertEqual(statuses["A"]["status"], "old")
        self.assertEqual(statuses["B"]["status"], "new")
        self.assertEqual(statuses["B"]["checked_on"], "2026-09-01")

    def test_ordinary_status_survives_future_checks_restart_and_catalog_refresh(self):
        repository.save_check("rimili", {"A"}, [order("A", "2026-08-03")], TODAY, NOW)
        original = repository.get_statuses("rimili")["A"]
        future = TODAY + timedelta(days=40)
        self.assertNotIn("A", repository.pending_articles("rimili", future))
        repository.save_check("rimili", self.articles, [], future, "later")
        db.init_db()
        db.replace_catalog("rimili", "YANDEX MARKET", [{"article": "A"}], "later")
        self.assertEqual(repository.get_statuses("rimili")["A"], original)
        self.assertIn("A", yandex_assortment.active_articles("rimili"))

    def test_all_ordinary_skips_api_entirely(self):
        repository.save_check(
            "rimili", self.articles, [order(a, "2026-08-05") for a in self.articles], TODAY, NOW
        )
        with mock.patch.object(sync.unit_economics_sync, "load_orders") as loader:
            self.assertTrue(sync.sync_store("rimili", TODAY + timedelta(days=30))["skipped"])
        loader.assert_not_called()

    def test_api_failure_preserves_status_and_does_not_mark_unchecked_products_new(self):
        repository.save_check("rimili", {"A"}, [], TODAY, NOW)
        before = repository.get_statuses("rimili")
        with mock.patch.object(
            sync.unit_economics_sync, "load_orders", side_effect=RuntimeError("page failed")
        ):
            self.assertFalse(sync.sync_store("rimili", TODAY + timedelta(days=1))["ok"])
        self.assertEqual(repository.get_statuses("rimili"), before)
        self.assertEqual(snapshots.get_snapshots("rimili")["novelty"]["error"], "page failed")

    def test_partial_pagination_failure_does_not_commit_classification(self):
        with mock.patch.object(
            sync.unit_economics_sync.api,
            "_request",
            side_effect=[
                {"orders": [{"id": 1}], "paging": {"nextPageToken": "next"}},
                RuntimeError("page failed"),
            ],
        ):
            self.assertFalse(sync.sync_store("rimili", TODAY)["ok"])
        self.assertEqual(repository.get_statuses("rimili"), {})

    def test_status_is_scoped_to_store_and_only_active_assortment_is_checked(self):
        db.replace_catalog("rimili", "YANDEX MARKET", [{"article": "LEGACY"}], NOW)
        self.assertNotIn("LEGACY", repository.pending_articles("rimili", TODAY))
        repository.save_check("rimili", {"A"}, [order("A", "2026-08-03")], TODAY, NOW)
        self.assertEqual(repository.get_statuses("tris"), {})
        self.assertIn("A", repository.pending_articles("tris", TODAY))

    def test_status_in_listing_does_not_change_economics_or_existing_snapshots(self):
        snapshots.save_snapshot("rimili", "advertising", [], "2026-08-24", "2026-08-30", NOW)
        snapshots.save_snapshot("rimili", "orders", [], "2026-08-11", "2026-08-31", NOW)
        before = unit_economics_yandex.load_products(("rimili",), today=TODAY)
        previous_snapshots = snapshots.get_snapshots("rimili")
        repository.save_check("rimili", {"A", "B"}, [order("A", "2026-08-03")], TODAY, NOW)
        after = unit_economics_yandex.load_products(("rimili",), today=TODAY)
        for original, updated in zip(before, after, strict=True):
            expected = {"A": False, "B": True}.get(updated["article"])
            self.assertIs(updated["is_new"], expected)
            updated["is_new"] = None
            self.assertEqual(original, updated)
        self.assertEqual(snapshots.get_snapshots("rimili"), previous_snapshots)

    def test_older_check_cannot_override_a_newer_check(self):
        future = TODAY + timedelta(days=1)
        repository.save_check("rimili", {"A"}, [], future, "later")
        before = repository.get_statuses("rimili")
        repository.save_check("rimili", {"A"}, [order("A", "2026-08-03")], TODAY, NOW)
        self.assertEqual(repository.get_statuses("rimili"), before)
