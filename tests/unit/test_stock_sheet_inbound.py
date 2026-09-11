from datetime import UTC, datetime, timedelta

import pytest

from app import db, stock_sheet_export, stock_sheet_inbound
from app.dto.inbound_supplies import InboundItem, InboundSnapshot, InboundSupply
from app.infrastructure.database import database_for_path
from app.infrastructure.inbound_repository import SqlAlchemyInboundRepository
from app.wb import inbound as wb_inbound

NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
CATALOG = [{"article": "A", "barcode": "000123", "name": "Товар"}, {"article": "B", "name": "Другой товар"}]


def supply(stage="transit", article="A", **quantities):
    return InboundSupply(
        key=stage,
        supply_id=stage,
        stage=stage,
        status=stage,
        status_label=stage,
        items=(InboundItem(article=article, quantity=100, **quantities),),
    )


def snapshot(*supplies, marketplace="WB", **updates):
    return InboundSnapshot(
        store_slug="rimili",
        marketplace=marketplace,
        status="ok",
        last_success=NOW.isoformat(),
        supplies=supplies,
    ).model_copy(update=updates)


@pytest.mark.parametrize("marketplace", ["WB", "OZON", "YANDEX MARKET"])
def test_only_unfinished_dispatched_supply_units_are_exported(marketplace):
    result = stock_sheet_inbound.summarize(
        snapshot(
            supply("planned"),
            supply("transit", accepted_quantity=100),
            supply("acceptance", accepted_quantity=40, ready_quantity=40 if marketplace == "WB" else None),
            supply("placement", ready_quantity=60),
            supply("completed"),
            supply("cancelled"),
            marketplace=marketplace,
        ),
        CATALOG,
        NOW,
    )
    assert result.quantities == {"A": 200, "B": 0}
    assert result.available and not result.warnings


def wb_supply(status, *goods, **detail):
    return wb_inbound.normalize(
        {"supplyID": 123, "statusID": status},
        detail,
        [
            dict({"nmID": article, "barcode": f"barcode-{article}", "quantity": 100}, **counters)
            for article, counters in goods
        ],
    )


def test_wb_shortage_does_not_hide_confirmed_items_or_remain_in_transit():
    accepted = wb_supply(
        5,
        ("A", {"quantity": 50, "acceptedQuantity": 49, "readyForSaleQuantity": 49}),
        ("B", {"quantity": 150, "acceptedQuantity": 150, "readyForSaleQuantity": 149}),
        ("OLD", {"acceptedQuantity": 100, "readyForSaleQuantity": 100}),
    )
    assert accepted.stage == "discrepancy"
    result = stock_sheet_inbound.summarize(snapshot(accepted), CATALOG, NOW)
    assert result.quantities == {"A": 0, "B": 1}
    assert result.available and not result.warnings
    # The supply still shows its discrepancy in the FBO page; export doesn't rewrite it.
    assert accepted.remaining_quantity is None
    assert accepted.items[0].shortage_quantity == 1


def test_wb_old_shortage_does_not_taint_a_new_shipment_of_the_same_article():
    accepted = wb_supply(5, ("A", {"acceptedQuantity": 99, "readyForSaleQuantity": 99}))
    result = stock_sheet_inbound.summarize(snapshot(accepted, supply()), CATALOG, NOW)
    assert result.quantities == {"A": 100, "B": 0}
    assert not result.warnings


@pytest.mark.parametrize("counters", [{"acceptedQuantity": 99}, {"readyForSaleQuantity": 99}, {}])
def test_wb_missing_counts_leave_only_that_article_unknown(counters):
    accepted = wb_supply(
        5,
        ("A", counters),
        ("B", {"acceptedQuantity": 100, "readyForSaleQuantity": 90}),
    )
    result = stock_sheet_inbound.summarize(snapshot(accepted), CATALOG, NOW)
    assert result.quantities == {"A": None, "B": 10}
    assert result.available and result.warnings
    assert "ТОТАЛ" in result.warnings[0]


@pytest.mark.parametrize(
    ("status", "accepted", "ready", "expected"),
    [(4, 40, 20, 80), (6, 100, 20, 80), (5, 100, 70, 30), (5, 99, 99, 0), (5, 110, 100, 10)],
)
def test_wb_export_excludes_only_ready_units_and_closed_shortage(status, accepted, ready, expected):
    batch = wb_supply(status, ("A", {"acceptedQuantity": accepted, "readyForSaleQuantity": ready}))
    result = stock_sheet_inbound.summarize(snapshot(batch), CATALOG, NOW)
    assert result.quantities == {"A": expected, "B": 0}
    assert not result.warnings


def test_wb_transit_center_receipt_does_not_finish_the_supply():
    batch = wb_supply(6, ("A", {"acceptedQuantity": 100, "readyForSaleQuantity": 0}), transitWarehouseID=507)
    assert batch.stage == "transit"
    result = stock_sheet_inbound.summarize(snapshot(batch), CATALOG, NOW)
    assert result.quantities == {"A": 100, "B": 0}
    assert not result.warnings


@pytest.mark.parametrize(("marketplace", "status"), [("OZON", "COMPLETED"), ("YANDEX MARKET", "FINISHED")])
def test_finished_receipt_discrepancy_is_not_an_unconfirmed_open_supply(marketplace, status):
    finished = supply("discrepancy", accepted_quantity=99, shortage_quantity=1).model_copy(
        update={"status": status}
    )
    result = stock_sheet_inbound.summarize(
        snapshot(finished, supply(), marketplace=marketplace), CATALOG, NOW
    )
    assert result.quantities == {"A": 100, "B": 0}
    assert not result.warnings


@pytest.mark.parametrize(
    ("marketplace", "status"), [("OZON", "REPORT_REJECTED"), ("YANDEX MARKET", "INVALID")]
)
def test_unresolved_source_status_still_requires_confirmation(marketplace, status):
    disputed = supply("discrepancy", accepted_quantity=99).model_copy(update={"status": status})
    result = stock_sheet_inbound.summarize(snapshot(disputed, marketplace=marketplace), CATALOG, NOW)
    assert result.quantities == {"A": None, "B": 0}
    assert result.warnings


def test_unavailable_wb_receipt_does_not_reuse_old_confirmed_counts():
    missing = wb_supply(5, ("A", {"acceptedQuantity": 99, "readyForSaleQuantity": 99})).model_copy(
        update={"unavailable": True}
    )
    result = stock_sheet_inbound.summarize(snapshot(missing), CATALOG, NOW)
    assert result.quantities == {"A": None, "B": 0}
    assert result.warnings


@pytest.mark.parametrize(
    "updates",
    [
        {"last_success": None, "status": "never"},
        {"status": "not_configured"},
        {"status": "error"},
        {"status": "running", "error": "Предыдущая ошибка"},
        {"last_success": (NOW - timedelta(hours=3)).isoformat()},
        {"last_success": "invalid-date"},
    ],
)
def test_missing_or_stale_snapshot_produces_blanks_not_zeroes(updates):
    result = stock_sheet_inbound.summarize(snapshot(supply(), **updates), CATALOG, NOW)
    assert result.quantities == {"A": None, "B": None}
    assert not result.available
    assert result.warnings


def test_unknown_quantity_taints_only_affected_article_and_does_not_add_a_partial_sum():
    result = stock_sheet_inbound.summarize(
        snapshot(supply("transit"), supply("acceptance"), status="partial"), CATALOG, NOW
    )
    assert result.quantities == {"A": None, "B": 0}
    assert result.available and result.warnings


def test_disappeared_planned_supply_is_unknown_instead_of_silently_omitted():
    missing = supply("planned").model_copy(update={"unavailable": True})
    result = stock_sheet_inbound.summarize(snapshot(missing), CATALOG, NOW)
    assert result.quantities == {"A": None, "B": 0}


def test_wb_barcodes_resolve_sizes_and_ambiguous_articles_are_not_assigned_arbitrarily():
    catalog = [{"article": "123 / S", "barcode": "000123"}, {"article": "123 / M", "barcode": "000456"}]
    exact = supply(article="123", barcode="000123")
    result = stock_sheet_inbound.summarize(snapshot(exact), catalog, NOW)
    assert result.quantities == {"123 / S": 100, "123 / M": 0}
    ambiguous = stock_sheet_inbound.summarize(snapshot(supply(article="123")), catalog, NOW)
    assert ambiguous.quantities == {"123 / S": None, "123 / M": None}
    assert ambiguous.warnings


def test_supply_item_missing_from_catalog_is_exported_with_its_identifiers():
    result = stock_sheet_inbound.summarize(snapshot(supply(article="ORPHAN", barcode="000111")), CATALOG, NOW)
    assert result.quantities == {"A": 0, "B": 0, "ORPHAN": 100}
    assert result.catalog[-1]["article"] == "ORPHAN"
    assert result.catalog[-1]["barcode"] == "000111"
    assert len(CATALOG) == 2


def save_snapshot(database_path, store, marketplace, items):
    repository = SqlAlchemyInboundRepository(database_for_path(database_path).session_factory)
    assert repository.claim((store, marketplace), "test", NOW)
    assert repository.finish((store, marketplace), "test", NOW, supplies=tuple(items))


@pytest.mark.parametrize(
    ("status", "ff_stock", "accepted", "ready", "expected_total"),
    [
        (2, 100, 0, 0, 100),
        (4, 0, 40, 0, 100),
        (4, 0, 40, 30, 100),
        (5, 0, 100, 70, 100),
        (5, 0, 99, 99, 99),
        (5, 0, 100, 100, 100),
    ],
)
def test_total_does_not_count_ready_wb_units_twice(
    database_path, status, ff_stock, accepted, ready, expected_total
):
    db.replace_catalog("rimili", "WB", CATALOG, NOW.isoformat())
    db.upsert_ff_stock("rimili", "A", "ФФ", ff_stock, NOW.isoformat(), "WB")
    db.upsert_mp_stock("rimili", "A", "WB", "fbo", ready, NOW.isoformat())
    batch = wb_supply(status, ("A", {"acceptedQuantity": accepted, "readyForSaleQuantity": ready}))
    save_snapshot(database_path, "rimili", "WB", [batch])
    ff_before = db.get_ff_available_totals("rimili", marketplace="WB")
    fbo_before = db.get_mp_stock_totals("rimili", "WB", "fbo")
    _, values, warnings = stock_sheet_export._combined_stock_snapshot(("rimili",), "WB", now=NOW)
    assert sum(values[metric]["A"] for metric in stock_sheet_export.STOCK_EXPORT_METRICS) == expected_total
    assert not warnings
    assert db.get_ff_available_totals("rimili", marketplace="WB") == ff_before
    assert db.get_mp_stock_totals("rimili", "WB", "fbo") == fbo_before


def add_transfer(store="rimili", marketplace="OZON", status="partial", article="A"):
    with db.get_connection() as connection:
        row = connection.execute(
            """INSERT INTO ff_transit_batches
                (store_slug, from_fulfillment, from_marketplace, to_fulfillment, to_marketplace,
                 status, note, sent_by_name, sent_at)
               VALUES (?, 'Source', 'WB', 'Target', ?, ?, '', 'Test', ?) RETURNING id""",
            (store, marketplace, status, NOW.isoformat()),
        )
        connection.execute(
            """INSERT INTO ff_transit_items
                (batch_id, from_article, to_article, barcode, name, sent_quantity, received_quantity, cancelled_quantity)
               VALUES (?, 'FROM', ?, '000123', 'Товар', 100, 25, 5)""",
            (row.lastrowid, article),
        )
        connection.commit()


def test_export_reads_ff_transit_from_destination_scope_and_keeps_stock_unchanged(database_path):
    db.replace_catalog("rimili", "OZON", CATALOG, NOW.isoformat())
    db.upsert_ff_stock("rimili", "A", "ФФ", 20, NOW.isoformat(), "OZON")
    db.upsert_mp_stock("rimili", "A", "OZON", "fbo", 30, NOW.isoformat())
    add_transfer()
    add_transfer(store="toyka")
    add_transfer(marketplace="WB")
    add_transfer(status="received")
    add_transfer(article="IN-TRANSIT-ONLY")
    save_snapshot(database_path, "rimili", "OZON", [supply("acceptance", accepted_quantity=40)])
    catalog, values, warnings = stock_sheet_export._combined_stock_snapshot(("rimili",), "OZON", now=NOW)
    assert values["ff_transit"] == {"A": 70, "B": 0, "IN-TRANSIT-ONLY": 70}
    assert values["mp_inbound"] == {"A": 60, "B": 0, "IN-TRANSIT-ONLY": 0}
    assert catalog[-1]["article"] == "IN-TRANSIT-ONLY"
    assert not warnings
    assert db.get_ff_available_totals("rimili", marketplace="OZON") == {"A": 20}
    assert db.get_mp_stock_totals("rimili", "OZON", "fbo") == {"A": 30}
    assert db.get_ff_transit_totals("rimili", "OZON")["A"] == 70


def test_combined_cabinets_sum_transit_and_mark_whole_column_unknown_if_one_source_missing(database_path):
    for store, article in (("rockkiddo", "A"), ("toyka", "a")):
        db.replace_catalog(store, "WB", [{"article": article, "barcode": "000123"}], NOW.isoformat())
        add_transfer(store=store, marketplace="WB", article=article)
    save_snapshot(database_path, "rockkiddo", "WB", [supply()])
    _, values, warnings = stock_sheet_export._combined_stock_snapshot(("rockkiddo", "toyka"), "WB", now=NOW)
    assert values["ff_transit"] == {"A": 140}
    assert values["mp_inbound"] == {"A": None}
    assert warnings
    save_snapshot(database_path, "toyka", "WB", [supply(article="a")])
    _, values, warnings = stock_sheet_export._combined_stock_snapshot(("rockkiddo", "toyka"), "WB", now=NOW)
    assert values["mp_inbound"] == {"A": 200}
    assert not warnings
