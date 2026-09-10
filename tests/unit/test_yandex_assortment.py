import json
from collections import Counter
from unittest import mock

import pytest

from app import db, unit_economics_yandex
from app.repositories import core, yandex_assortment

NOW = "2026-09-09T10:00:00+00:00"
MARKETPLACE = "YANDEX MARKET"
ACTIVE = "153985484"
MISSING = "1464755629"


def marks():
    with core.get_connection() as connection:
        return {
            (row["store_slug"], row["article"]): (row["is_legacy"], row["updated_at"])
            for row in connection.execute("SELECT * FROM unit_economics_yandex_assortment")
        }


def test_original_selection_has_all_155_store_article_pairs():
    active = yandex_assortment.load_active_products()
    assert len(active) == 155
    assert Counter(store for store, _ in active) == {
        "tris": 76, "gogol": 6, "rimili": 26, "sokoloff": 29, "trusthome": 12, "rockkiddo": 6,
    }


def test_only_unit_economics_hides_legacy_and_unknown_products(database_path):
    db.replace_catalog("tris", MARKETPLACE, [{"article": ACTIVE}, {"article": "OLD"}], NOW)
    db.replace_catalog("rimili", MARKETPLACE, [{"article": ACTIVE}], NOW)
    db.replace_catalog("tris", "WB", [{"article": "OLD"}], NOW)
    db.upsert_mp_stock("tris", "OLD", MARKETPLACE, "fbs", 17, NOW)
    db.upsert_ff_stock("tris", "OLD", "FF", 9, NOW, MARKETPLACE)
    state = marks()
    assert state[("tris", ACTIVE)][0] == 0
    assert state[("tris", "OLD")][0] == 1
    assert state[("rimili", ACTIVE)][0] == 1
    assert [row["article"] for row in unit_economics_yandex.load_products(("tris", "rimili"))] == [ACTIVE]
    assert unit_economics_yandex.load_products(("tris",), article="OLD") == []
    assert {row["article"] for row in db.get_catalog_items("tris", MARKETPLACE)} == {ACTIVE, "OLD"}
    assert [row["article"] for row in db.get_catalog_items("tris", "WB")] == ["OLD"]
    old_stock = next(row for row in db.get_stock_items("tris", MARKETPLACE) if row["article"] == "OLD")
    assert old_stock["fbs_stock"] == 17
    assert old_stock["ff_available"] == 9


def test_missing_selected_products_appear_when_catalog_arrives_and_marks_survive_sync(database_path):
    assert marks()[("tris", MISSING)][0] == 0
    assert unit_economics_yandex.load_products(("tris",)) == []
    db.replace_catalog("tris", MARKETPLACE, [{"article": ACTIVE}, {"article": "OLD"}], NOW)
    before = marks()
    later = "2026-09-10T10:00:00+00:00"
    db.replace_catalog(
        "tris", MARKETPLACE,
        [{"article": ACTIVE, "name": "Updated"}, {"article": MISSING}, {"article": "NEW-UNLISTED"}], later,
    )
    after = marks()
    assert after[("tris", ACTIVE)] == before[("tris", ACTIVE)]
    assert after[("tris", "OLD")] == before[("tris", "OLD")]
    assert after[("tris", "NEW-UNLISTED")][0] == 1
    assert {row["article"] for row in unit_economics_yandex.load_products(("tris",))} == {ACTIVE, MISSING}
    db.init_db()
    assert marks() == after


def test_expanding_and_reducing_selection_updates_marks_without_deleting_catalog(database_path):
    db.replace_catalog("tris", MARKETPLACE, [{"article": ACTIVE}, {"article": "NEW"}], NOW)
    with mock.patch.object(yandex_assortment, "load_active_products", return_value={("tris", "NEW")}):
        db.init_db()
        assert marks()[("tris", ACTIVE)][0] == 1
        assert marks()[("tris", "NEW")][0] == 0
        assert [row["article"] for row in unit_economics_yandex.load_products(("tris",))] == ["NEW"]
    assert {row["article"] for row in db.get_catalog_items("tris", MARKETPLACE)} == {ACTIVE, "NEW"}


@pytest.mark.parametrize("value", [
    [],
    [{"store_slug": "unknown", "article": "1"}],
    [{"store_slug": "tris", "article": ""}],
    [{"store_slug": "tris", "article": "1"}, {"store_slug": "tris", "article": "1"}],
])
def test_invalid_selection_does_not_reclassify_or_replace_existing_catalog(database_path, tmp_path, value):
    db.replace_catalog("tris", MARKETPLACE, [{"article": ACTIVE}], NOW)
    before = marks()
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(value), encoding="utf-8")
    with mock.patch.object(yandex_assortment, "ASSORTMENT_PATH", invalid), pytest.raises(ValueError):
        db.replace_catalog("tris", MARKETPLACE, [{"article": "OTHER"}], NOW)
    assert marks() == before
    assert [row["article"] for row in db.get_catalog_items("tris", MARKETPLACE)] == [ACTIVE]
