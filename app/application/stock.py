from collections.abc import Callable
from datetime import datetime

from app.application.ports import StockRepository, StockUnitOfWorkFactory
from app.dto.stock import (
    AddedFulfillmentItem,
    AddedFulfillmentItems,
    AddFulfillmentItemsCommand,
    ApplyShipmentCommand,
    ApplyTransferCommand,
    CancelTransitCommand,
    CatalogItem,
    CatalogQuery,
    ReceiveTransitCommand,
    ReopenTransitCommand,
    ResolvedStockEntries,
    ResolvedStockEntry,
    ResolveStockEntriesCommand,
    ShipmentCommand,
    StockAvailabilityQuery,
    StockEntrySplit,
    StockIncrement,
    StockMovementItem,
    StockMovementItems,
    StockQuantityQuery,
    TargetResolution,
    TargetResolutionQuery,
    TargetStockEntries,
    TargetStockEntry,
    TransferResult,
    TransferStockCommand,
    TransitActionResult,
)
from app.errors import StockValidationError


class StockMovementService:
    def __init__(
        self,
        unit_of_work_factory: StockUnitOfWorkFactory,
        clock: Callable[[], datetime],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def add_items(self, command: AddFulfillmentItemsCommand) -> AddedFulfillmentItems:
        with self._unit_of_work_factory() as unit_of_work:
            catalog = unit_of_work.repository.catalog(
                CatalogQuery(
                    store_slug=command.store_slug,
                    marketplace=command.request.marketplace,
                )
            )
            by_code = self._catalog_by_code(catalog.root)
            additions: dict[str, AddedFulfillmentItem] = {}
            missing: list[str] = []
            for entry in command.request.items:
                item = by_code.get(entry.code)
                if item is None:
                    missing.append(entry.code)
                    continue
                previous = additions.get(item.article)
                quantity = entry.quantity + (previous.added if previous else 0)
                additions[item.article] = AddedFulfillmentItem(
                    article=item.article,
                    barcode=item.barcode,
                    name=item.name,
                    added=quantity,
                )
            if missing:
                raise StockValidationError("Товары не найдены в каталоге: " + ", ".join(sorted(set(missing))))
            now = self._clock()
            for item in additions.values():
                unit_of_work.repository.increment(
                    StockIncrement(
                        store_slug=command.store_slug,
                        article=item.article,
                        fulfillment=command.request.fulfillment,
                        marketplace=command.request.marketplace,
                        quantity=item.added,
                        updated_at=now,
                    )
                )
            unit_of_work.commit()
            return AddedFulfillmentItems(tuple(additions.values()))

    def transfer(self, command: TransferStockCommand) -> TransferResult:
        self._validate_transfer_route(command)
        with self._unit_of_work_factory() as unit_of_work:
            entries = self._resolve_entries(
                unit_of_work.repository,
                ResolveStockEntriesCommand(
                    store_slug=command.store_slug,
                    entries=command.entries,
                    marketplace=command.from_marketplace,
                ),
            )
            resolution = self._resolve_target_entries(
                unit_of_work.repository,
                TargetResolutionQuery(
                    store_slug=command.store_slug,
                    entries=entries,
                    marketplace=command.to_marketplace,
                ),
            )
            if not resolution.movable.root:
                raise StockValidationError("Ни один товар не может быть перемещён")
            self._check_availability(
                unit_of_work.repository,
                StockAvailabilityQuery(
                    store_slug=command.store_slug,
                    entries=ResolvedStockEntries(
                        tuple(
                            ResolvedStockEntry(
                                article=item.from_article,
                                quantity=item.quantity,
                                name=item.name,
                                barcode=item.barcode,
                            )
                            for item in resolution.movable.root
                        )
                    ),
                    fulfillment=command.from_fulfillment,
                    marketplace=command.from_marketplace,
                ),
            )
            transfer_id = unit_of_work.repository.apply_transfer(
                ApplyTransferCommand(
                    transfer=command,
                    items=resolution.movable,
                    created_at=self._clock(),
                )
            )
            unit_of_work.commit()
            return TransferResult(
                moved=StockMovementItems(
                    tuple(
                        StockMovementItem(
                            article=item.to_article,
                            name=item.name,
                            barcode=item.barcode,
                            quantity=item.quantity,
                        )
                        for item in resolution.movable.root
                    )
                ),
                skipped=resolution.skipped,
                transfer_id=transfer_id,
            )

    def receive_transfer(self, command: ReceiveTransitCommand) -> TransitActionResult:
        with self._unit_of_work_factory() as unit_of_work:
            result = unit_of_work.repository.receive_transfer(
                command.model_copy(update={"created_at": self._clock()})
            )
            unit_of_work.commit()
            return result

    def reopen_transfer(self, command: ReopenTransitCommand) -> TransitActionResult:
        with self._unit_of_work_factory() as unit_of_work:
            result = unit_of_work.repository.reopen_transfer(
                command.model_copy(update={"created_at": self._clock()})
            )
            unit_of_work.commit()
            return result

    def cancel_transfer(self, command: CancelTransitCommand) -> TransitActionResult:
        with self._unit_of_work_factory() as unit_of_work:
            result = unit_of_work.repository.cancel_transfer(
                command.model_copy(update={"created_at": self._clock()})
            )
            unit_of_work.commit()
            return result

    def ship(self, command: ShipmentCommand) -> StockMovementItems:
        with self._unit_of_work_factory() as unit_of_work:
            entries = self._resolve_entries(
                unit_of_work.repository,
                ResolveStockEntriesCommand(
                    store_slug=command.store_slug,
                    entries=command.entries,
                    marketplace=command.marketplace,
                    allow_negative=command.to_trash,
                ),
            )
            split = self._split_by_sign(entries)
            if split.surplus.root and not command.to_trash:
                raise StockValidationError("Отрицательное количество допустимо только при списании в мусорку")
            if split.write_off.root:
                self._check_availability(
                    unit_of_work.repository,
                    StockAvailabilityQuery(
                        store_slug=command.store_slug,
                        entries=split.write_off,
                        fulfillment=command.fulfillment,
                        marketplace=command.marketplace,
                    ),
                )
            unit_of_work.repository.apply_shipment(
                ApplyShipmentCommand(
                    shipment=command,
                    write_off=split.write_off,
                    surplus=split.surplus,
                    created_at=self._clock(),
                )
            )
            unit_of_work.commit()
            return StockMovementItems(
                tuple(
                    StockMovementItem(
                        article=entry.article,
                        name=entry.name,
                        barcode=entry.barcode,
                        quantity=entry.quantity,
                    )
                    for entry in entries.root
                )
            )

    def register_fbs_transfer(self, command: ShipmentCommand) -> StockMovementItems:
        """Validate an FBS allocation without removing the physical FF stock."""

        with self._unit_of_work_factory() as unit_of_work:
            entries = self._resolve_entries(
                unit_of_work.repository,
                ResolveStockEntriesCommand(
                    store_slug=command.store_slug,
                    entries=command.entries,
                    marketplace=command.marketplace,
                ),
            )
            self._check_availability(
                unit_of_work.repository,
                StockAvailabilityQuery(
                    store_slug=command.store_slug,
                    entries=entries,
                    fulfillment=command.fulfillment,
                    marketplace=command.marketplace,
                ),
            )
            return StockMovementItems(
                tuple(
                    StockMovementItem(
                        article=entry.article,
                        name=entry.name,
                        barcode=entry.barcode,
                        quantity=entry.quantity,
                    )
                    for entry in entries.root
                )
            )

    @staticmethod
    def _catalog_by_code(items: tuple[CatalogItem, ...]) -> dict[str, CatalogItem]:
        result: dict[str, CatalogItem] = {}
        for item in items:
            result[item.article] = item
            result[item.barcode] = item
        return result

    @staticmethod
    def _validate_transfer_route(command: TransferStockCommand) -> None:
        if (
            command.from_fulfillment == command.to_fulfillment
            and command.from_marketplace is command.to_marketplace
        ):
            raise StockValidationError("Источник и получатель совпадают")

    def _resolve_entries(
        self,
        repository: StockRepository,
        command: ResolveStockEntriesCommand,
    ) -> ResolvedStockEntries:
        catalog = repository.catalog(
            CatalogQuery(store_slug=command.store_slug, marketplace=command.marketplace)
        )
        by_code = self._catalog_by_code(catalog.root)
        resolved: dict[str, ResolvedStockEntry] = {}
        missing: list[str] = []
        duplicates: list[str] = []
        for entry in command.entries.root:
            if entry.quantity < 0 and not command.allow_negative:
                raise StockValidationError("Количество должно быть больше нуля")
            item = by_code.get(entry.code)
            if item is None:
                missing.append(entry.code)
                continue
            if item.article in resolved:
                duplicates.append(item.article)
                continue
            resolved[item.article] = ResolvedStockEntry(
                article=item.article,
                quantity=entry.quantity,
                name=item.name,
                barcode=item.barcode,
            )
        if missing:
            raise StockValidationError("Товары не найдены в каталоге: " + ", ".join(sorted(set(missing))))
        if duplicates:
            raise StockValidationError("Товары указаны несколько раз: " + ", ".join(sorted(set(duplicates))))
        return ResolvedStockEntries(tuple(resolved.values()))

    @staticmethod
    def _resolve_target_entries(
        repository: StockRepository,
        query: TargetResolutionQuery,
    ) -> TargetResolution:
        target = repository.catalog(CatalogQuery(store_slug=query.store_slug, marketplace=query.marketplace))
        target_by_article = {item.article: item for item in target.root}
        target_by_barcode = {item.barcode: item for item in target.root if item.barcode}
        movable: list[TargetStockEntry] = []
        skipped: list[StockMovementItem] = []
        for entry in query.entries.root:
            target_item = target_by_article.get(entry.article)
            if target_item is None and entry.barcode:
                target_item = target_by_barcode.get(entry.barcode)
            if target_item is None:
                skipped.append(
                    StockMovementItem(
                        article=entry.article,
                        name=entry.name,
                        barcode=entry.barcode,
                        quantity=entry.quantity,
                        reason=f"Товар не найден по артикулу или баркоду в каталоге {query.marketplace.value}",
                    )
                )
                continue
            movable.append(
                TargetStockEntry(
                    from_article=entry.article,
                    to_article=target_item.article,
                    quantity=entry.quantity,
                    name=entry.name,
                    barcode=entry.barcode,
                )
            )
        return TargetResolution(
            movable=TargetStockEntries(tuple(movable)),
            skipped=StockMovementItems(tuple(skipped)),
        )

    @staticmethod
    def _check_availability(
        repository: StockRepository,
        query: StockAvailabilityQuery,
    ) -> None:
        shortages: list[str] = []
        for entry in query.entries.root:
            available = repository.quantity(
                StockQuantityQuery(
                    store_slug=query.store_slug,
                    article=entry.article,
                    fulfillment=query.fulfillment,
                    marketplace=query.marketplace,
                )
            ).root
            if entry.quantity > available:
                shortages.append(f"{entry.article}: запрошено {entry.quantity}, доступно {available}")
        if shortages:
            raise StockValidationError("Недостаточно остатка: " + "; ".join(shortages))

    @staticmethod
    def _split_by_sign(entries: ResolvedStockEntries) -> StockEntrySplit:
        write_off: list[ResolvedStockEntry] = []
        surplus: list[ResolvedStockEntry] = []
        for entry in entries.root:
            if entry.quantity < 0:
                surplus.append(entry.model_copy(update={"quantity": -entry.quantity}))
            else:
                write_off.append(entry)
        return StockEntrySplit(
            write_off=ResolvedStockEntries(tuple(write_off)),
            surplus=ResolvedStockEntries(tuple(surplus)),
        )
