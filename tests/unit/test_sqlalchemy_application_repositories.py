from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import db
from app.dto.decision import DecisionStatus, DecisionStatusRequest, SetDecisionStatusCommand
from app.dto.marketplace import Marketplace
from app.dto.rnp import (
    AddRnpActionCommand,
    RnpActionRequest,
    RnpArticleQuery,
    RnpStrategyRequest,
    SaveRnpStrategyCommand,
)
from app.dto.stock import (
    ApplyShipmentCommand,
    ApplyTransferCommand,
    CatalogQuery,
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
from app.infrastructure.decision_repository import SqlAlchemyDecisionUnitOfWork
from app.infrastructure.rnp_repository import SqlAlchemyRnpUnitOfWork
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
        unit_of_work.repository.apply_transfer(
            ApplyTransferCommand(
                transfer=transfer,
                items=TargetStockEntries((target_item,)),
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


@pytest.mark.unit
def test_rnp_and_decision_repositories_create_and_update(database_path: Path) -> None:
    add_catalog()
    session_factory = database_for_path(database_path).session_factory
    strategy_request = RnpStrategyRequest(
        store="rimili",
        marketplace=Marketplace.WB,
        article="A",
        strategy="growth",
        date_from="2026-08-01",
        date_to="2026-08-31",
    )
    with SqlAlchemyRnpUnitOfWork(session_factory) as unit_of_work:
        assert unit_of_work.repository.article_exists(
            RnpArticleQuery(store_slug="rimili", marketplace=Marketplace.WB, article="A")
        ).root
        assert not unit_of_work.repository.article_exists(
            RnpArticleQuery(store_slug="rimili", marketplace=Marketplace.WB, article="missing")
        ).root
        created = unit_of_work.repository.save_strategy(
            SaveRnpStrategyCommand(
                request=strategy_request,
                updated_by="User",
                updated_at=NOW,
            )
        )
        updated = unit_of_work.repository.save_strategy(
            SaveRnpStrategyCommand(
                request=strategy_request.model_copy(update={"strategy": "hold"}),
                updated_by="Admin",
                updated_at=NOW,
            )
        )
        action = unit_of_work.repository.add_action(
            AddRnpActionCommand(
                request=RnpActionRequest(
                    store="rimili",
                    marketplace=Marketplace.WB,
                    article="A",
                    action_date=NOW.date(),
                    note="Checked",
                ),
                user_id=1,
                user_name="User",
                created_at=NOW,
            )
        )
        unit_of_work.commit()
    assert created.strategy == "growth"
    assert updated.strategy == "hold"
    assert action.id > 0

    status_request = DecisionStatusRequest(fingerprint="rimili:A:stockout", status=DecisionStatus.IN_PROGRESS)
    with SqlAlchemyDecisionUnitOfWork(session_factory) as unit_of_work:
        first = unit_of_work.repository.set_status(
            SetDecisionStatusCommand(
                request=status_request,
                user_id=1,
                user_name="User",
                updated_at=NOW,
            )
        )
        second = unit_of_work.repository.set_status(
            SetDecisionStatusCommand(
                request=status_request.model_copy(update={"status": DecisionStatus.COMPLETED}),
                user_id=2,
                user_name="Admin",
                updated_at=NOW,
            )
        )
        unit_of_work.commit()
    assert first.status is DecisionStatus.IN_PROGRESS
    assert second.status is DecisionStatus.COMPLETED

    for unit_of_work_type in (SqlAlchemyRnpUnitOfWork, SqlAlchemyDecisionUnitOfWork):
        inactive = unit_of_work_type(session_factory)
        with pytest.raises(RuntimeError):
            inactive.commit()
        assert inactive.__exit__(None, None, None) is None
