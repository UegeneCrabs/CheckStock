from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import db
from app.dto.marketplace import Marketplace
from app.dto.stock import (
    ApplyShipmentCommand,
    ApplyTransferCommand,
    CatalogQuery,
    ReceiveTransitCommand,
    ReceiveTransitRequest,
    ResolvedStockEntries,
    ResolvedStockEntry,
    ShipmentCommand,
    SignedStockEntries,
    SignedStockEntry,
    StockIncrement,
    StockQuantityQuery,
    TargetStockEntries,
    TargetStockEntry,
    TransferStockCommand,
)
from app.infrastructure.database import database_for_path
from app.infrastructure.stock_repository import SqlAlchemyStockUnitOfWork

NOW = datetime(2026, 8, 12, 10, tzinfo=UTC)


def add_catalog() -> None:
    for marketplace in ("WB", "OZON"):
        db.replace_catalog(
            "rimili",
            marketplace,
            [{"article": "A", "barcode": f"{marketplace}-A", "name": "Product A"}],
            NOW.isoformat(),
        )


@pytest.mark.unit
def test_stock_repository_covers_create_update_delete_and_movements(database_path: Path) -> None:
    add_catalog()
    session_factory = database_for_path(database_path).session_factory
    stock = SqlAlchemyStockUnitOfWork(session_factory)
    with stock as unit_of_work:
        repository = unit_of_work.repository
        catalog = repository.catalog(CatalogQuery(store_slug="rimili", marketplace=Marketplace.WB))
        assert catalog.root[0].article == "A"
        query = StockQuantityQuery(
            store_slug="rimili",
            article="A",
            fulfillment="Source",
            marketplace=Marketplace.WB,
        )
        assert repository.quantity(query).root == 0
        repository.increment(
            StockIncrement(
                store_slug="rimili",
                article="A",
                fulfillment="Source",
                marketplace=Marketplace.WB,
                quantity=5,
                updated_at=NOW,
            )
        )
        assert repository.quantity(query).root == 5
        unit_of_work.commit()

    transfer = TransferStockCommand(
        store_slug="rimili",
        entries=SignedStockEntries((SignedStockEntry(code="A", quantity=2),)),
        from_fulfillment="Source",
        from_marketplace=Marketplace.WB,
        to_fulfillment="Target",
        to_marketplace=Marketplace.OZON,
        user_id=1,
        user_name="User",
    )
    target_item = TargetStockEntry(
        from_article="A",
        to_article="A",
        quantity=2,
        name="Product A",
        barcode="OZON-A",
    )
    with SqlAlchemyStockUnitOfWork(session_factory) as unit_of_work:
        transfer_id = unit_of_work.repository.apply_transfer(
            ApplyTransferCommand(
                transfer=transfer,
                items=TargetStockEntries((target_item,)),
                created_at=NOW,
            )
        )
        unit_of_work.commit()

    batch = db.get_ff_transit_batch(transfer_id)
    assert batch is not None
    with SqlAlchemyStockUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.repository.receive_transfer(
            ReceiveTransitCommand(
                transfer_id=transfer_id,
                request=ReceiveTransitRequest(
                    items=({"item_id": batch["items"][0]["id"], "quantity": 2},),
                ),
                user_id=1,
                user_name="User",
                created_at=NOW,
            )
        )
        unit_of_work.commit()

    shipment = ShipmentCommand(
        store_slug="rimili",
        entries=SignedStockEntries((SignedStockEntry(code="A", quantity=1),)),
        fulfillment="Target",
        marketplace=Marketplace.OZON,
        to_trash=True,
    )
    write_off = ResolvedStockEntries(
        (ResolvedStockEntry(article="A", quantity=1, name="Product A", barcode="OZON-A"),)
    )
    with SqlAlchemyStockUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.repository.apply_shipment(
            ApplyShipmentCommand(
                shipment=shipment,
                write_off=write_off,
                surplus=ResolvedStockEntries(()),
                created_at=NOW,
            )
        )
        unit_of_work.repository.apply_shipment(
            ApplyShipmentCommand(
                shipment=shipment,
                write_off=ResolvedStockEntries(()),
                surplus=write_off,
                created_at=NOW,
            )
        )
        unit_of_work.commit()

    with SqlAlchemyStockUnitOfWork(session_factory) as unit_of_work:
        unit_of_work.repository.increment(
            StockIncrement(
                store_slug="rimili",
                article="A",
                fulfillment="Target",
                marketplace=Marketplace.OZON,
                quantity=-2,
                updated_at=NOW,
            )
        )
        unit_of_work.commit()
    assert db.get_ff_stock_one("rimili", "A", "Target", "OZON") == 0

    inactive = SqlAlchemyStockUnitOfWork(session_factory)
    with pytest.raises(RuntimeError):
        inactive.commit()
    inactive.rollback()
    assert inactive.__exit__(None, None, None) is None
