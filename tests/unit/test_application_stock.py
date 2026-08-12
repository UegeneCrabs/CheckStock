from datetime import UTC, datetime
from unittest import mock

import pytest

from app.application.stock import StockMovementService
from app.dto.marketplace import Marketplace
from app.dto.stock import (
    AddFulfillmentItemsCommand,
    AddFulfillmentItemsRequest,
    CatalogItem,
    CatalogItems,
    ShipmentCommand,
    SignedStockEntries,
    SignedStockEntry,
    StockQuantity,
    TransferStockCommand,
)
from app.errors import StockValidationError

NOW = datetime(2026, 8, 12, 10, tzinfo=UTC)


def catalog(*articles: str, marketplace: Marketplace = Marketplace.WB) -> CatalogItems:
    return CatalogItems(
        tuple(
            CatalogItem(
                article=article,
                barcode=f"barcode-{article}",
                name=f"Product {article}",
                marketplace=marketplace,
            )
            for article in articles
        )
    )


@pytest.fixture
def stock_service() -> tuple[StockMovementService, mock.Mock, mock.MagicMock]:
    repository = mock.Mock()
    unit_of_work = mock.MagicMock()
    unit_of_work.__enter__.return_value = unit_of_work
    unit_of_work.repository = repository
    service = StockMovementService(lambda: unit_of_work, clock=lambda: NOW)
    return service, repository, unit_of_work


def transfer_command(
    *entries: tuple[str, int],
    to_marketplace: Marketplace = Marketplace.OZON,
) -> TransferStockCommand:
    return TransferStockCommand(
        store_slug="store",
        entries=SignedStockEntries(
            tuple(SignedStockEntry(code=code, quantity=quantity) for code, quantity in entries)
        ),
        from_fulfillment="Source",
        from_marketplace=Marketplace.WB,
        to_fulfillment="Target",
        to_marketplace=to_marketplace,
        user_id=1,
        user_name="User",
    )


@pytest.mark.unit
def test_add_items_aggregates_catalog_codes_and_commits(stock_service) -> None:
    service, repository, unit_of_work = stock_service
    repository.catalog.return_value = catalog("A")
    command = AddFulfillmentItemsCommand(
        store_slug="store",
        request=AddFulfillmentItemsRequest(
            fulfillment="FF",
            marketplace=Marketplace.WB,
            items=(
                {"code": "A", "quantity": 2},
                {"code": "barcode-A", "quantity": 3},
            ),
        ),
    )

    result = service.add_items(command)

    assert result.root[0].added == 5
    assert repository.increment.call_args.args[0].quantity == 5
    unit_of_work.commit.assert_called_once()


@pytest.mark.unit
def test_add_items_rejects_unknown_catalog_code(stock_service) -> None:
    service, repository, unit_of_work = stock_service
    repository.catalog.return_value = catalog("A")
    command = AddFulfillmentItemsCommand(
        store_slug="store",
        request=AddFulfillmentItemsRequest(
            fulfillment="FF",
            items=({"code": "missing", "quantity": 1},),
        ),
    )

    with pytest.raises(StockValidationError, match="missing"):
        service.add_items(command)

    unit_of_work.commit.assert_not_called()


@pytest.mark.unit
def test_transfer_moves_available_items_and_reports_target_misses(stock_service) -> None:
    service, repository, unit_of_work = stock_service
    repository.catalog.side_effect = [catalog("A", "B"), catalog("A", marketplace=Marketplace.OZON)]
    repository.quantity.return_value = StockQuantity(10)

    result = service.transfer(transfer_command(("A", 2), ("B", 1)))

    assert [item.article for item in result.moved.root] == ["A"]
    assert result.skipped.root[0].article == "B"
    repository.apply_transfer.assert_called_once()
    unit_of_work.commit.assert_called_once()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "target", "quantity", "message"),
    [
        (catalog("A"), catalog("A", marketplace=Marketplace.OZON), 1, "несколько раз"),
        (catalog("A"), catalog("A", marketplace=Marketplace.OZON), 0, "Недостаточно"),
        (catalog("A"), catalog(marketplace=Marketplace.OZON), 5, "Ни один"),
    ],
)
def test_transfer_rejects_duplicates_shortage_and_empty_target(
    stock_service,
    source: CatalogItems,
    target: CatalogItems,
    quantity: int,
    message: str,
) -> None:
    service, repository, unit_of_work = stock_service
    repository.catalog.side_effect = [source, target]
    repository.quantity.return_value = StockQuantity(quantity)
    command = (
        transfer_command(("A", 1), ("barcode-A", 2))
        if message == "несколько раз"
        else transfer_command(("A", 2))
    )

    with pytest.raises(StockValidationError, match=message):
        service.transfer(command)

    unit_of_work.commit.assert_not_called()


@pytest.mark.unit
def test_transfer_rejects_same_route_and_unknown_source(stock_service) -> None:
    service, repository, _unit_of_work = stock_service
    same = transfer_command(("A", 1), to_marketplace=Marketplace.WB).model_copy(
        update={"to_fulfillment": "Source"}
    )
    with pytest.raises(StockValidationError, match="совпадают"):
        service.transfer(same)

    repository.catalog.return_value = catalog("A")
    with pytest.raises(StockValidationError, match="missing"):
        service.transfer(transfer_command(("missing", 1)))


@pytest.mark.unit
def test_shipment_splits_write_off_and_surplus_atomically(stock_service) -> None:
    service, repository, unit_of_work = stock_service
    repository.catalog.return_value = catalog("A", "B")
    repository.quantity.return_value = StockQuantity(10)
    command = ShipmentCommand(
        store_slug="store",
        entries=SignedStockEntries(
            (
                SignedStockEntry(code="A", quantity=2),
                SignedStockEntry(code="B", quantity=-3),
            )
        ),
        fulfillment="FF",
        marketplace=Marketplace.WB,
        to_trash=True,
    )

    result = service.ship(command)

    persisted = repository.apply_shipment.call_args.args[0]
    assert persisted.write_off.root[0].quantity == 2
    assert persisted.surplus.root[0].quantity == 3
    assert len(result.root) == 2
    unit_of_work.commit.assert_called_once()


@pytest.mark.unit
def test_shipment_rejects_negative_regular_and_shortage(stock_service) -> None:
    service, repository, unit_of_work = stock_service
    repository.catalog.return_value = catalog("A")
    negative = ShipmentCommand(
        store_slug="store",
        entries=SignedStockEntries((SignedStockEntry(code="A", quantity=-1),)),
        fulfillment="FF",
        marketplace=Marketplace.WB,
        to_trash=False,
    )
    with pytest.raises(StockValidationError, match="больше нуля"):
        service.ship(negative)

    repository.quantity.return_value = StockQuantity(0)
    regular = negative.model_copy(
        update={
            "entries": SignedStockEntries((SignedStockEntry(code="A", quantity=2),)),
        }
    )
    with pytest.raises(StockValidationError, match="Недостаточно"):
        service.ship(regular)
    unit_of_work.commit.assert_not_called()
