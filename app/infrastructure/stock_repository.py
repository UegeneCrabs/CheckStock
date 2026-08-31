from types import TracebackType

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.dto.marketplace import Marketplace
from app.dto.stock import (
    ApplyShipmentCommand,
    ApplyTransferCommand,
    CancelTransitCommand,
    CatalogItem,
    CatalogItems,
    CatalogQuery,
    ReceiveTransitCommand,
    ReopenTransitCommand,
    StockIncrement,
    StockMovementItem,
    StockMovementItems,
    StockQuantity,
    StockQuantityQuery,
    TransitActionResult,
)
from app.errors import StockValidationError
from app.infrastructure.orm import (
    FulfillmentStockRecord,
    FulfillmentTransferRecord,
    FulfillmentTransitBatchRecord,
    FulfillmentTransitItemRecord,
    FulfillmentTransitReceiptItemRecord,
    FulfillmentTransitReceiptRecord,
    StockItemRecord,
    TrashStockRecord,
    UnitEconomics1CSourceValueRecord,
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

    def apply_transfer(self, command: ApplyTransferCommand) -> int:
        transfer = command.transfer
        batch = FulfillmentTransitBatchRecord(
            store_slug=transfer.store_slug,
            from_fulfillment=transfer.from_fulfillment,
            from_marketplace=transfer.from_marketplace.value,
            to_fulfillment=transfer.to_fulfillment,
            to_marketplace=transfer.to_marketplace.value,
            status="in_transit",
            note=transfer.note,
            sent_by_user_id=transfer.user_id,
            sent_by_name=transfer.user_name,
            sent_at=command.created_at.isoformat(),
        )
        self._session.add(batch)
        self._session.flush()

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
            self._session.add(
                FulfillmentTransitItemRecord(
                    batch_id=batch.id,
                    from_article=item.from_article,
                    to_article=item.to_article,
                    barcode=item.barcode,
                    name=item.name,
                    sent_quantity=item.quantity,
                    received_quantity=0,
                    cancelled_quantity=0,
                    purchase_price=self._purchase_price(transfer.store_slug, item.from_article),
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
        self._session.flush()
        return batch.id

    def receive_transfer(self, command: ReceiveTransitCommand) -> TransitActionResult:
        created_at = command.created_at
        if created_at is None:
            raise RuntimeError("Не указано время приёмки перемещения")
        batch = self._session.scalar(
            select(FulfillmentTransitBatchRecord)
            .where(FulfillmentTransitBatchRecord.id == command.transfer_id)
            .with_for_update()
        )
        if batch is None:
            raise StockValidationError("Перемещение не найдено")
        if batch.status not in {"in_transit", "partial"}:
            raise StockValidationError("Это перемещение уже закрыто")

        item_rows = list(
            self._session.scalars(
                select(FulfillmentTransitItemRecord)
                .where(FulfillmentTransitItemRecord.batch_id == batch.id)
                .order_by(FulfillmentTransitItemRecord.id)
                .with_for_update()
            )
        )
        by_id = {item.id: item for item in item_rows}
        requested: dict[int, int] = {}
        for entry in command.request.items:
            if entry.item_id in requested:
                raise StockValidationError("Одна позиция указана несколько раз")
            requested[entry.item_id] = entry.quantity

        missing = sorted(item_id for item_id in requested if item_id not in by_id)
        if missing:
            raise StockValidationError("Позиции не относятся к перемещению: " + ", ".join(map(str, missing)))

        receipt = FulfillmentTransitReceiptRecord(
            batch_id=batch.id,
            user_id=command.user_id,
            user_name=command.user_name,
            note=command.request.note,
            received_at=created_at.isoformat(),
        )
        self._session.add(receipt)
        self._session.flush()

        moved: list[StockMovementItem] = []
        for item_id, quantity in requested.items():
            item = by_id[item_id]
            remaining = item.sent_quantity - item.received_quantity - item.cancelled_quantity
            if quantity > remaining:
                raise StockValidationError(
                    f"{item.to_article}: принимается {quantity}, в пути осталось {remaining}"
                )
            self.increment(
                StockIncrement(
                    store_slug=batch.store_slug,
                    article=item.to_article,
                    fulfillment=batch.to_fulfillment,
                    marketplace=Marketplace(batch.to_marketplace),
                    quantity=quantity,
                    updated_at=created_at,
                )
            )
            item.received_quantity += quantity
            self._session.add(
                FulfillmentTransitReceiptItemRecord(
                    receipt_id=receipt.id,
                    transit_item_id=item.id,
                    quantity=quantity,
                )
            )
            moved.append(
                StockMovementItem(
                    article=item.to_article,
                    name=item.name or item.to_article,
                    barcode=item.barcode or "",
                    quantity=quantity,
                    purchase_price=item.purchase_price,
                )
            )

        remaining_total = sum(
            item.sent_quantity - item.received_quantity - item.cancelled_quantity for item in item_rows
        )
        batch.status = "received" if remaining_total == 0 else "partial"
        batch.last_received_by_user_id = command.user_id
        batch.last_received_by_name = command.user_name
        batch.last_received_at = created_at.isoformat()
        self._session.flush()
        return TransitActionResult(
            transfer_id=batch.id,
            status=batch.status,
            moved=StockMovementItems(tuple(moved)),
        )

    def cancel_transfer(self, command: CancelTransitCommand) -> TransitActionResult:
        created_at = command.created_at
        if created_at is None:
            raise RuntimeError("Не указано время отмены перемещения")
        batch = self._session.scalar(
            select(FulfillmentTransitBatchRecord)
            .where(FulfillmentTransitBatchRecord.id == command.transfer_id)
            .with_for_update()
        )
        if batch is None:
            raise StockValidationError("Перемещение не найдено")
        if batch.status not in {"in_transit", "partial"}:
            raise StockValidationError("Это перемещение уже закрыто")

        item_rows = list(
            self._session.scalars(
                select(FulfillmentTransitItemRecord)
                .where(FulfillmentTransitItemRecord.batch_id == batch.id)
                .order_by(FulfillmentTransitItemRecord.id)
                .with_for_update()
            )
        )
        moved: list[StockMovementItem] = []
        for item in item_rows:
            remaining = item.sent_quantity - item.received_quantity - item.cancelled_quantity
            if remaining <= 0:
                continue
            self.increment(
                StockIncrement(
                    store_slug=batch.store_slug,
                    article=item.from_article,
                    fulfillment=batch.from_fulfillment,
                    marketplace=Marketplace(batch.from_marketplace),
                    quantity=remaining,
                    updated_at=created_at,
                )
            )
            item.cancelled_quantity += remaining
            moved.append(
                StockMovementItem(
                    article=item.from_article,
                    name=item.name or item.from_article,
                    barcode=item.barcode or "",
                    quantity=remaining,
                    purchase_price=item.purchase_price,
                )
            )

        if not moved:
            raise StockValidationError("В перемещении не осталось товара для возврата")
        batch.status = "cancelled"
        batch.cancelled_by_user_id = command.user_id
        batch.cancelled_by_name = command.user_name
        batch.cancelled_at = created_at.isoformat()
        batch.cancellation_reason = command.request.reason
        self._session.flush()
        return TransitActionResult(
            transfer_id=batch.id,
            status=batch.status,
            moved=StockMovementItems(tuple(moved)),
        )

    def reopen_transfer(self, command: ReopenTransitCommand) -> TransitActionResult:
        created_at = command.created_at
        if created_at is None:
            raise RuntimeError("Не указано время возврата приёмки в путь")
        batch = self._session.scalar(
            select(FulfillmentTransitBatchRecord)
            .where(FulfillmentTransitBatchRecord.id == command.transfer_id)
            .with_for_update()
        )
        if batch is None:
            raise StockValidationError("Перемещение не найдено")
        if batch.status not in {"received", "partial"}:
            raise StockValidationError("В партии нет принятого товара для возврата в путь")

        item_rows = list(
            self._session.scalars(
                select(FulfillmentTransitItemRecord)
                .where(FulfillmentTransitItemRecord.batch_id == batch.id)
                .order_by(FulfillmentTransitItemRecord.id)
                .with_for_update()
            )
        )
        if any(item.cancelled_quantity for item in item_rows):
            raise StockValidationError("Нельзя вернуть в путь приёмку уже отменённой партии")

        received_by_article: dict[str, dict[str, object]] = {}
        for item in item_rows:
            if item.received_quantity <= 0:
                continue
            entry = received_by_article.setdefault(
                item.to_article,
                {
                    "quantity": 0,
                    "name": item.name or item.to_article,
                    "barcode": item.barcode or "",
                    "purchase_price": item.purchase_price,
                },
            )
            entry["quantity"] = int(entry["quantity"]) + item.received_quantity

        if not received_by_article:
            raise StockValidationError("В партии нет принятого товара для возврата в путь")

        destination_records: dict[str, FulfillmentStockRecord] = {}
        for article, entry in received_by_article.items():
            quantity = int(entry["quantity"])
            record = self._stock_record(
                StockQuantityQuery(
                    store_slug=batch.store_slug,
                    article=article,
                    fulfillment=batch.to_fulfillment,
                    marketplace=Marketplace(batch.to_marketplace),
                ),
                for_update=True,
            )
            available = record.quantity if record is not None else 0
            if record is None or record.quantity < quantity:
                raise StockValidationError(
                    f"Нельзя вернуть приёмку в путь: {article} — на складе назначения "
                    f"осталось {available}, требуется {quantity}"
                )
            destination_records[article] = record

        moved: list[StockMovementItem] = []
        for article, entry in received_by_article.items():
            quantity = int(entry["quantity"])
            record = destination_records[article]
            record.quantity -= quantity
            record.updated_at = created_at.isoformat()
            if record.quantity == 0:
                self._session.delete(record)
            moved.append(
                StockMovementItem(
                    article=article,
                    name=str(entry["name"]),
                    barcode=str(entry["barcode"]),
                    quantity=quantity,
                    purchase_price=(
                        float(entry["purchase_price"]) if entry["purchase_price"] is not None else None
                    ),
                )
            )

        receipt_ids = tuple(
            self._session.scalars(
                select(FulfillmentTransitReceiptRecord.id).where(
                    FulfillmentTransitReceiptRecord.batch_id == batch.id
                )
            )
        )
        if receipt_ids:
            self._session.execute(
                delete(FulfillmentTransitReceiptItemRecord).where(
                    FulfillmentTransitReceiptItemRecord.receipt_id.in_(receipt_ids)
                )
            )
            self._session.execute(
                delete(FulfillmentTransitReceiptRecord).where(
                    FulfillmentTransitReceiptRecord.id.in_(receipt_ids)
                )
            )

        for item in item_rows:
            item.received_quantity = 0
        batch.status = "in_transit"
        batch.last_received_by_user_id = None
        batch.last_received_by_name = None
        batch.last_received_at = None
        self._session.flush()
        return TransitActionResult(
            transfer_id=batch.id,
            status=batch.status,
            moved=StockMovementItems(tuple(moved)),
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

    def _stock_record(
        self,
        query: StockQuantityQuery,
        *,
        for_update: bool = False,
    ) -> FulfillmentStockRecord | None:
        statement = select(FulfillmentStockRecord).where(
            FulfillmentStockRecord.store_slug == query.store_slug,
            FulfillmentStockRecord.article == query.article,
            FulfillmentStockRecord.fulfillment == query.fulfillment,
            FulfillmentStockRecord.marketplace == query.marketplace.value,
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def _purchase_price(self, store_slug: str, article: str) -> float | None:
        value = self._session.scalar(
            select(UnitEconomics1CSourceValueRecord.purchase_price)
            .join(
                StockItemRecord,
                StockItemRecord.id == UnitEconomics1CSourceValueRecord.stock_item_id,
            )
            .where(
                StockItemRecord.store_slug == store_slug,
                StockItemRecord.marketplace == "WB",
                StockItemRecord.article == article,
                StockItemRecord.is_service == 0,
            )
        )
        return float(value) if value is not None else None

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
