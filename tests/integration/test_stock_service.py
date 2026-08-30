from pathlib import Path

import pytest

from app import db
from app.container import ApplicationContainer
from app.dto.marketplace import Marketplace
from app.dto.stock import (
    AddFulfillmentItemsCommand,
    AddFulfillmentItemsRequest,
    CancelTransitCommand,
    CancelTransitRequest,
    ReceiveTransitCommand,
    ReceiveTransitRequest,
    ShipmentCommand,
    SignedStockEntries,
    SignedStockEntry,
    TransferStockCommand,
)
from app.errors import StockValidationError


@pytest.fixture
def stock_container(database_path: Path) -> ApplicationContainer:
    db.replace_catalog(
        "rimili",
        "WB",
        [{"article": "A", "barcode": "barcode-A", "name": "Product A"}],
        "2026-08-12T10:00:00+00:00",
    )
    db.replace_catalog(
        "rimili",
        "OZON",
        [{"article": "A", "barcode": "ozon-A", "name": "Product A"}],
        "2026-08-12T10:00:00+00:00",
    )
    return ApplicationContainer(database_path=lambda: database_path)


@pytest.mark.integration
def test_stock_service_persists_full_movement_chain(stock_container: ApplicationContainer) -> None:
    stock_container.stock.add_items(
        AddFulfillmentItemsCommand(
            store_slug="rimili",
            request=AddFulfillmentItemsRequest(
                fulfillment="Source",
                marketplace=Marketplace.WB,
                note="Initial integration stock",
                items=({"code": "A", "quantity": 5},),
            ),
        )
    )
    transfer = stock_container.stock.transfer(
        TransferStockCommand(
            store_slug="rimili",
            entries=SignedStockEntries((SignedStockEntry(code="A", quantity=2),)),
            from_fulfillment="Source",
            from_marketplace=Marketplace.WB,
            to_fulfillment="Target",
            to_marketplace=Marketplace.OZON,
            user_id=1,
            user_name="Integration User",
        )
    )
    batch = db.get_ff_transit_batch(transfer.transfer_id)
    assert batch is not None
    stock_container.stock.receive_transfer(
        ReceiveTransitCommand(
            transfer_id=transfer.transfer_id,
            request=ReceiveTransitRequest(
                items=({"item_id": batch["items"][0]["id"], "quantity": 2},),
            ),
            user_id=1,
            user_name="Integration User",
        )
    )
    stock_container.stock.ship(
        ShipmentCommand(
            store_slug="rimili",
            entries=SignedStockEntries((SignedStockEntry(code="A", quantity=1),)),
            fulfillment="Target",
            marketplace=Marketplace.OZON,
        )
    )

    assert db.get_ff_stock_one("rimili", "A", "Source", "WB") == 3
    assert db.get_ff_stock_one("rimili", "A", "Target", "OZON") == 1


@pytest.mark.integration
def test_stock_service_partially_receives_and_cancels_transit_remainder(
    stock_container: ApplicationContainer,
) -> None:
    stock_container.stock.add_items(
        AddFulfillmentItemsCommand(
            store_slug="rimili",
            request=AddFulfillmentItemsRequest(
                fulfillment="Source",
                marketplace=Marketplace.WB,
                note="Initial partial stock",
                items=({"code": "A", "quantity": 6},),
            ),
        )
    )
    transfer = stock_container.stock.transfer(
        TransferStockCommand(
            store_slug="rimili",
            entries=SignedStockEntries((SignedStockEntry(code="A", quantity=4),)),
            from_fulfillment="Source",
            from_marketplace=Marketplace.WB,
            to_fulfillment="Target",
            to_marketplace=Marketplace.OZON,
            user_id=1,
            user_name="Integration User",
            note="Partial receipt test",
        )
    )
    batch = db.get_ff_transit_batch(transfer.transfer_id)
    assert batch is not None
    item_id = batch["items"][0]["id"]

    received = stock_container.stock.receive_transfer(
        ReceiveTransitCommand(
            transfer_id=transfer.transfer_id,
            request=ReceiveTransitRequest(
                items=({"item_id": item_id, "quantity": 1},),
                note="One item arrived",
            ),
            user_id=2,
            user_name="Receiver",
        )
    )

    assert received.status == "partial"
    assert db.get_ff_stock_one("rimili", "A", "Source", "WB") == 2
    assert db.get_ff_stock_one("rimili", "A", "Target", "OZON") == 1
    assert db.get_ff_transit_totals("rimili", "OZON") == {"A": 3}

    cancelled = stock_container.stock.cancel_transfer(
        CancelTransitCommand(
            transfer_id=transfer.transfer_id,
            request=CancelTransitRequest(reason="Destination discrepancy"),
            user_id=1,
            user_name="Senior manager",
        )
    )

    assert cancelled.status == "cancelled"
    assert db.get_ff_stock_one("rimili", "A", "Source", "WB") == 5
    assert db.get_ff_stock_one("rimili", "A", "Target", "OZON") == 1
    assert db.get_ff_transit_totals("rimili", "OZON") == {}

    history = db.get_ff_transit_batches(
        "rimili",
        "OZON",
        active_only=False,
        closed_only=True,
    )
    assert len(history) == 1
    assert history[0]["status"] == "cancelled"
    assert history[0]["sent_units"] == 4
    assert history[0]["received_units"] == 1
    assert history[0]["cancelled_units"] == 3
    assert history[0]["cancellation_reason"] == "Destination discrepancy"
    assert history[0]["receipts"][0]["user_name"] == "Receiver"
    assert history[0]["receipts"][0]["note"] == "One item arrived"
    assert history[0]["receipts"][0]["received_units"] == 1
    assert history[0]["receipts"][0]["items"][0]["article"] == "A"
    assert history[0]["receipts"][0]["items"][0]["quantity"] == 1


@pytest.mark.integration
def test_stock_service_rolls_back_rejected_transfer(stock_container: ApplicationContainer) -> None:
    stock_container.stock.add_items(
        AddFulfillmentItemsCommand(
            store_slug="rimili",
            request=AddFulfillmentItemsRequest(
                fulfillment="Source",
                marketplace=Marketplace.WB,
                note="Initial rollback stock",
                items=({"code": "A", "quantity": 2},),
            ),
        )
    )
    command = TransferStockCommand(
        store_slug="rimili",
        entries=SignedStockEntries((SignedStockEntry(code="A", quantity=3),)),
        from_fulfillment="Source",
        from_marketplace=Marketplace.WB,
        to_fulfillment="Target",
        to_marketplace=Marketplace.OZON,
        user_id=1,
        user_name="Integration User",
    )

    with pytest.raises(StockValidationError, match="Недостаточно"):
        stock_container.stock.transfer(command)

    assert db.get_ff_stock_one("rimili", "A", "Source", "WB") == 2
    assert db.get_ff_stock_one("rimili", "A", "Target", "OZON") == 0
