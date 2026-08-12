from types import TracebackType

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.dto.stock import (
    ApplyShipmentCommand,
    ApplyTransferCommand,
    CatalogItem,
    CatalogItems,
    CatalogQuery,
    StockIncrement,
    StockQuantity,
    StockQuantityQuery,
)
from app.infrastructure.orm import (
    FulfillmentStockRecord,
    FulfillmentTransferRecord,
    StockItemRecord,
    TrashStockRecord,
)


class SqlAlchemyStockRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def catalog(self, query: CatalogQuery) -> CatalogItems:
        records = self._session.scalars(
            select(StockItemRecord)
            .where(
                StockItemRecord.store_slug == query.store_slug,
                StockItemRecord.marketplace == query.marketplace.value,
                StockItemRecord.is_service == 0,
            )
            .order_by(StockItemRecord.id)
        )
        return CatalogItems(
            tuple(
                CatalogItem(
                    article=record.article,
                    barcode=record.barcode,
                    name=record.name,
                    marketplace=query.marketplace,
                    mp_sku=record.mp_sku,
                    mp_product_id=record.mp_product_id,
                    image_url=record.image_url,
                )
                for record in records
            )
        )

    def quantity(self, query: StockQuantityQuery) -> StockQuantity:
        record = self._stock_record(query)
        return StockQuantity(record.quantity if record else 0)

    def increment(self, command: StockIncrement) -> None:
        query = StockQuantityQuery(
            store_slug=command.store_slug,
            article=command.article,
            fulfillment=command.fulfillment,
            marketplace=command.marketplace,
        )
        record = self._stock_record(query)
        if record is None:
            record = FulfillmentStockRecord(
                store_slug=command.store_slug,
                article=command.article,
                fulfillment=command.fulfillment,
                marketplace=command.marketplace.value,
                quantity=command.quantity,
                updated_at=command.updated_at.isoformat(),
            )
            self._session.add(record)
            self._session.flush()
        else:
            record.quantity += command.quantity
            record.updated_at = command.updated_at.isoformat()
            if record.quantity == 0:
                self._session.delete(record)

    def apply_transfer(self, command: ApplyTransferCommand) -> None:
        transfer = command.transfer
        for item in command.items.root:
            self.increment(
                StockIncrement(
                    store_slug=transfer.store_slug,
                    article=item.from_article,
                    fulfillment=transfer.from_fulfillment,
                    marketplace=transfer.from_marketplace,
                    quantity=-item.quantity,
                    updated_at=command.created_at,
                )
            )
            self.increment(
                StockIncrement(
                    store_slug=transfer.store_slug,
                    article=item.to_article,
                    fulfillment=transfer.to_fulfillment,
                    marketplace=transfer.to_marketplace,
                    quantity=item.quantity,
                    updated_at=command.created_at,
                )
            )
            self._session.add(
                FulfillmentTransferRecord(
                    store_slug=transfer.store_slug,
                    article=item.to_article,
                    quantity=item.quantity,
                    from_fulfillment=transfer.from_fulfillment,
                    from_marketplace=transfer.from_marketplace.value,
                    to_fulfillment=transfer.to_fulfillment,
                    to_marketplace=transfer.to_marketplace.value,
                    user_id=transfer.user_id,
                    user_name=transfer.user_name,
                    created_at=command.created_at.isoformat(),
                )
            )

    def apply_shipment(self, command: ApplyShipmentCommand) -> None:
        shipment = command.shipment
        for item in command.surplus.root:
            self._adjust_trash(command, item.article, -item.quantity)
            self.increment(
                StockIncrement(
                    store_slug=shipment.store_slug,
                    article=item.article,
                    fulfillment=shipment.fulfillment,
                    marketplace=shipment.marketplace,
                    quantity=item.quantity,
                    updated_at=command.created_at,
                )
            )
        for item in command.write_off.root:
            self.increment(
                StockIncrement(
                    store_slug=shipment.store_slug,
                    article=item.article,
                    fulfillment=shipment.fulfillment,
                    marketplace=shipment.marketplace,
                    quantity=-item.quantity,
                    updated_at=command.created_at,
                )
            )
            if shipment.to_trash:
                self._adjust_trash(command, item.article, item.quantity)

    def _stock_record(self, query: StockQuantityQuery) -> FulfillmentStockRecord | None:
        return self._session.scalar(
            select(FulfillmentStockRecord).where(
                FulfillmentStockRecord.store_slug == query.store_slug,
                FulfillmentStockRecord.article == query.article,
                FulfillmentStockRecord.fulfillment == query.fulfillment,
                FulfillmentStockRecord.marketplace == query.marketplace.value,
            )
        )

    def _adjust_trash(
        self,
        command: ApplyShipmentCommand,
        article: str,
        quantity: int,
    ) -> None:
        shipment = command.shipment
        record = self._session.scalar(
            select(TrashStockRecord).where(
                TrashStockRecord.store_slug == shipment.store_slug,
                TrashStockRecord.article == article,
                TrashStockRecord.fulfillment == shipment.fulfillment,
                TrashStockRecord.marketplace == shipment.marketplace.value,
            )
        )
        if record is None:
            record = TrashStockRecord(
                store_slug=shipment.store_slug,
                article=article,
                fulfillment=shipment.fulfillment,
                marketplace=shipment.marketplace.value,
                quantity=quantity,
                updated_at=command.created_at.isoformat(),
            )
            self._session.add(record)
            self._session.flush()
        else:
            record.quantity += quantity
            record.updated_at = command.created_at.isoformat()
            if record.quantity == 0:
                self._session.delete(record)


class SqlAlchemyStockUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.repository: SqlAlchemyStockRepository

    def __enter__(self) -> "SqlAlchemyStockUnitOfWork":
        self._session = self._session_factory()
        self.repository = SqlAlchemyStockRepository(self._session)
        return self

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("UnitOfWork is not active")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._session is None:
            return None
        try:
            if exc_type is not None:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
        return None
