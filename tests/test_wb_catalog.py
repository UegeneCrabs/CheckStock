import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db
from app.repositories import core
from app.wb import catalog


def _card(nm_id: int, tag: str | None = None, tech_size: str = "") -> dict:
    card = {
        "nmID": nm_id,
        "vendorCode": f"Товар_{nm_id}",
        "title": f"Товар {nm_id}",
        "updatedAt": "2026-08-10T10:00:00Z",
        "sizes": [{"techSize": tech_size, "skus": [f"460000000{nm_id}"]}],
    }
    if tag is not None:
        card["tags"] = [{"id": nm_id, "name": tag, "color": "D1CFD7"}]
    return card


class WBCatalogTests(unittest.TestCase):
    def test_build_items_includes_cards_regardless_of_tags(self) -> None:
        cards = [_card(101, "  СТАРЬЁ  "), _card(102, "Активный")]

        items, stats = catalog.build_items(cards)

        self.assertEqual([item["article"] for item in items], ["101", "102"])
        self.assertNotIn("excluded_tag", stats)

    @mock.patch.object(catalog.db, "replace_catalog")
    @mock.patch.object(catalog.wb_api, "get_cards_list")
    @mock.patch.object(catalog.wb_tokens, "get_token", return_value="token")
    def test_sync_includes_tagged_cards_for_every_store(
        self,
        _get_token: mock.Mock,
        get_cards_list: mock.Mock,
        replace_catalog: mock.Mock,
    ) -> None:
        get_cards_list.return_value = [_card(201, "Старье"), _card(202)]
        replace_catalog.return_value = {
            "added": 0,
            "updated": 0,
            "removed": 0,
            "kept": 0,
        }

        for store_slug in ("rimili", "tris", "toyka", "rockkiddo", "trusthome"):
            replace_catalog.reset_mock()
            catalog.sync_store(store_slug)
            target_call = replace_catalog.call_args
            self.assertEqual(
                [item["article"] for item in target_call.args[2]],
                ["201", "202"],
            )
            self.assertNotIn("force_remove_articles", target_call.kwargs)

    def test_forced_removal_overrides_own_stock_protection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "catalog.db"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE stock_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_slug TEXT NOT NULL,
                    marketplace TEXT NOT NULL,
                    article TEXT NOT NULL,
                    barcode TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mp_sku TEXT,
                    mp_product_id TEXT,
                    image_url TEXT,
                    is_service INTEGER NOT NULL DEFAULT 0,
                    mp_updated_at TEXT,
                    updated_at TEXT,
                    UNIQUE(store_slug, marketplace, article)
                );
                CREATE TABLE ff_stock (
                    store_slug TEXT NOT NULL,
                    article TEXT NOT NULL,
                    fulfillment TEXT NOT NULL,
                    marketplace TEXT NOT NULL,
                    quantity INTEGER NOT NULL
                );
                CREATE TABLE trash_stock (
                    store_slug TEXT NOT NULL,
                    article TEXT NOT NULL,
                    marketplace TEXT NOT NULL,
                    fulfillment TEXT NOT NULL,
                    quantity INTEGER NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT INTO stock_items "
                "(store_slug, marketplace, article, barcode, name) "
                "VALUES ('rimili', 'WB', ?, ?, ?)",
                [
                    ("301 / 42", "460000000301", "Старый товар"),
                    ("302", "460000000302", "Пропавший товар"),
                ],
            )
            connection.executemany(
                "INSERT INTO ff_stock "
                "(store_slug, article, fulfillment, marketplace, quantity) "
                "VALUES ('rimili', ?, 'ФФ', 'WB', 1)",
                [("301 / 42",), ("302",)],
            )
            connection.commit()
            connection.close()

            with mock.patch.object(core, "DB_PATH", database):
                report = db.replace_catalog(
                    "rimili",
                    "WB",
                    [],
                    "2026-08-10T10:00:00Z",
                    force_remove_articles={"301 / 42"},
                )
                remaining = db.get_catalog_items("rimili", "WB")

        self.assertEqual([item["article"] for item in remaining], ["302"])
        self.assertEqual(report["forced_removed"], 1)
        self.assertEqual(report["kept"], 1)


if __name__ == "__main__":
    unittest.main()
