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
    ReopenTransitCommand,
    ReopenTransitRequest,
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
def test_stock_service_maps_different_target_article_by_barcode(
    stock_container: ApplicationContainer,
) -> None:
    db.replace_catalog(
        "rimili",
        "OZON",
        [{"article": "OZON-A", "barcode": "barcode-A", "name": "Product A"}],
        "2026-08-12T10:00:00+00:00",
    )
    stock_container.stock.add_items(
        AddFulfillmentItemsCommand(
            store_slug="rimili",
            request=AddFulfillmentItemsRequest(
                fulfillment="Source",
                marketplace=Marketplace.WB,
                note="Initial barcode mapping stock",
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
    assert batch["items"][0]["from_article"] == "A"
    assert batch["items"][0]["to_article"] == "OZON-A"

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

    assert db.get_ff_stock_one("rimili", "A", "Source", "WB") == 3
    assert db.get_ff_stock_one("rimili", "OZON-A", "Target", "OZON") == 2


@pytest.mark.integration
def test_stock_service_reopens_received_transfer(stock_container: ApplicationContainer) -> None:
    stock_container.stock.add_items(
        AddFulfillmentItemsCommand(
            store_slug="rimili",
            request=AddFulfillmentItemsRequest(
                fulfillment="Source",
                marketplace=Marketplace.WB,
                note="Initial stock",
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
            user_name="Sender",
        )
    )
    batch = db.get_ff_transit_batch(transfer.transfer_id)
    assert batch is not None
    stock_container.stock.receive_transfer(
        ReceiveTransitCommand(
            transfer_id=transfer.transfer_id,
            request=ReceiveTransitRequest(
                items=({"item_id": batch["items"][0]["id"], "quantity": 2},),
                note="Accepted by mistake",
            ),
            user_id=2,
            user_name="Receiver",
        )
    )

    reopened = stock_container.stock.reopen_transfer(
        ReopenTransitCommand(
            transfer_id=transfer.transfer_id,
            request=ReopenTransitRequest(reason="Goods are still in transit"),
            user_id=1,
            user_name="Senior manager",
        )
    )

    assert reopened.status == "in_transit"
    assert [(item.article, item.quantity) for item in reopened.moved.root] == [("A", 2)]
    assert db.get_ff_stock_one("rimili", "A", "Source", "WB") == 3
    assert db.get_ff_stock_one("rimili", "A", "Target", "OZON") == 0
    assert db.get_ff_transit_totals("rimili", "OZON") == {"A": 2}
    restored = db.get_ff_transit_batch(transfer.transfer_id)
    assert restored is not None
    assert restored["status"] == "in_transit"
    assert restored["received_units"] == 0
    assert restored["remaining_units"] == 2
    assert restored["receipts"] == []
    assert restored["last_received_at"] is None

    with pytest.raises(StockValidationError, match="нет принятого товара"):
        stock_container.stock.reopen_transfer(
            ReopenTransitCommand(
                transfer_id=transfer.transfer_id,
                request=ReopenTransitRequest(reason="Duplicate rollback"),
                user_id=1,
                user_name="Senior manager",
            )
        )


@pytest.mark.integration
def test_stock_service_rejects_reopen_when_destination_stock_was_used(
    stock_container: ApplicationContainer,
) -> None:
    stock_container.stock.add_items(
        AddFulfillmentItemsCommand(
            store_slug="rimili",
            request=AddFulfillmentItemsRequest(
                fulfillment="Source",
                marketplace=Marketplace.WB,
                note="Initial stock",
                items=({"code": "A", "quantity": 3},),
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
            user_name="Sender",
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
            user_id=2,
            user_name="Receiver",
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

    with pytest.raises(StockValidationError, match="осталось 1, требуется 2"):
        stock_container.stock.reopen_transfer(
            ReopenTransitCommand(
                transfer_id=transfer.transfer_id,
                request=ReopenTransitRequest(reason="Too late"),
                user_id=1,
                user_name="Senior manager",
            )
        )

    assert db.get_ff_stock_one("rimili", "A", "Target", "OZON") == 1
    unchanged = db.get_ff_transit_batch(transfer.transfer_id)
    assert unchanged is not None
    assert unchanged["status"] == "received"
    assert unchanged["received_units"] == 2
    assert len(unchanged["receipts"]) == 1


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
