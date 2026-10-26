import hashlib
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import FBO_STOCK, INBOUND_SCHEDULE, FboStockErrorCode, FboStockState
from app.dto.fbo_stock import (
    FboActor,
    FboAllocation,
    FboBalance,
    FboEvidence,
    FboLegacyBatch,
    FboMovement,
    FboStockCommand,
    FboStockError,
    FboStockReport,
    FboStockResult,
    FboTarget,
)
from app.dto.inbound_supplies import InboundSupply
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.fbo_stock_orm import FboSupplyBatchRecord, FboSupplyMovementRecord
from app.infrastructure.orm import (
    ActivityLogRecord,
    CatalogBarcodeRecord,
    FulfillmentRecord,
    FulfillmentStockRecord,
    FulfillmentTransitBatchRecord,
    FulfillmentTransitItemRecord,
    InboundSupplySnapshotRecord,
    StockItemRecord,
    StockMutationRequestRecord,
    StockOperationItemRecord,
    StockOperationRecord,
    UnitEconomics1CSourceValueRecord,
)
from app.stock.fbo_availability import movement_availability

MOVEMENTS = table_for(FboSupplyMovementRecord)
LINKS = table_for(FboSupplyBatchRecord)
SNAPSHOTS = table_for(InboundSupplySnapshotRecord)
STOCK = table_for(FulfillmentStockRecord)
FULFILLMENTS = table_for(FulfillmentRecord)
BATCHES = table_for(FulfillmentTransitBatchRecord)
ITEMS = table_for(FulfillmentTransitItemRecord)
OPERATIONS = table_for(StockOperationRecord)
OPERATION_ITEMS = table_for(StockOperationItemRecord)
ACTIVITY = table_for(ActivityLogRecord)
REQUESTS = table_for(StockMutationRequestRecord)
CATALOG = table_for(StockItemRecord)
PRICES = table_for(UnitEconomics1CSourceValueRecord)
BARCODES = table_for(CatalogBarcodeRecord)
SUPPLIES = TypeAdapter(tuple[InboundSupply, ...])


class FboStockRepository:
    def __init__(self, database: AsyncDatabase) -> None:
        self.database = database

    @asynccontextmanager
    async def transaction(self, target: FboTarget) -> AsyncIterator["FboStockUnitOfWork"]:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                select(SNAPSHOTS.c.store_slug)
                .where(
                    SNAPSHOTS.c.store_slug == target.store_slug,
                    SNAPSHOTS.c.marketplace == target.marketplace,
                )
                .with_for_update()
            )
            yield FboStockUnitOfWork(connection, target)

    async def report(self, target: FboTarget) -> FboStockReport:
        async with self.database.transaction() as connection:
            unit = FboStockUnitOfWork(connection, target)
            evidence = await unit.evidence(lock_stock=False)
            linked = await connection.scalars(
                select(LINKS.c.batch_id).where(
                    LINKS.c.store_slug == target.store_slug,
                    LINKS.c.marketplace == target.marketplace,
                )
            )
            return FboStockReport(
                movements=tuple(
                    movement_availability(target, movement, evidence) for movement in evidence.movements
                ),
                legacy=evidence.legacy,
                linked_batch_ids=tuple(linked),
                fulfillments=evidence.fulfillments,
            )


class FboStockUnitOfWork:
    def __init__(self, connection: AsyncConnection, target: FboTarget) -> None:
        self.connection = connection
        self.target = target
        self._prices_loaded = False
        self._prices_by_article: dict[str, float | None] = {}
        self._prices_by_barcode: dict[str, float | None] = {}

    async def evidence(self, *, lock_stock: bool = True) -> FboEvidence:
        target = self.target
        if lock_stock:
            await self.connection.execute(
                select(BATCHES.c.id)
                .where(
                    BATCHES.c.store_slug == target.store_slug,
                    BATCHES.c.from_marketplace == target.marketplace,
                    BATCHES.c.kind == FBO_STOCK.batch_kind,
                )
                .order_by(BATCHES.c.id)
                .with_for_update()
            )
        snapshot = (
            (
                await self.connection.execute(
                    select(SNAPSHOTS.c.payload_json, SNAPSHOTS.c.last_success, SNAPSHOTS.c.status).where(
                        SNAPSHOTS.c.store_slug == target.store_slug,
                        SNAPSHOTS.c.marketplace == target.marketplace,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        confirmed = False
        if snapshot and snapshot["last_success"]:
            observed = datetime.fromisoformat(snapshot["last_success"])
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=UTC)
            confirmed = (
                snapshot["status"] in FBO_STOCK.snapshot_confirmed
                and (datetime.now(UTC) - observed).total_seconds() <= INBOUND_SCHEDULE.stale_after_seconds
            )
        movements = await self.connection.scalars(
            select(MOVEMENTS.c.payload_json)
            .where(
                MOVEMENTS.c.store_slug == target.store_slug,
                MOVEMENTS.c.marketplace == target.marketplace,
            )
            .order_by(MOVEMENTS.c.supply_key)
        )
        stock_query = (
            select(STOCK.c.article, STOCK.c.fulfillment, STOCK.c.quantity)
            .where(
                STOCK.c.store_slug == target.store_slug,
                STOCK.c.marketplace == target.marketplace,
            )
            .order_by(STOCK.c.id)
        )
        balances = await self.connection.execute(stock_query.with_for_update() if lock_stock else stock_query)
        legacy_rows = await self.connection.execute(
            select(
                BATCHES.c.id,
                BATCHES.c.from_fulfillment,
                BATCHES.c.sent_at,
                ITEMS.c.from_article,
                ITEMS.c.sent_quantity,
                ITEMS.c.cancelled_quantity,
                ITEMS.c.barcode,
                ITEMS.c.name,
                ITEMS.c.purchase_price,
            )
            .join(ITEMS, ITEMS.c.batch_id == BATCHES.c.id)
            .where(
                BATCHES.c.store_slug == target.store_slug,
                BATCHES.c.from_marketplace == target.marketplace,
                BATCHES.c.to_marketplace == target.marketplace,
                BATCHES.c.kind == FBO_STOCK.batch_kind,
                BATCHES.c.status.in_((FBO_STOCK.batch_active, FBO_STOCK.batch_partial)),
                ~select(LINKS.c.batch_id).where(LINKS.c.batch_id == BATCHES.c.id).exists(),
            )
            .order_by(BATCHES.c.id, ITEMS.c.id)
        )
        legacy_items: dict[int, list[FboAllocation]] = defaultdict(list)
        legacy_meta: dict[int, FboLegacyBatch] = {}
        for row in legacy_rows.mappings():
            batch_id = int(row["id"])
            legacy_meta[batch_id] = FboLegacyBatch(
                id=batch_id,
                fulfillment=row["from_fulfillment"],
                sent_at=row["sent_at"],
                items=(),
            )
            quantity = int(row["sent_quantity"]) - int(row["cancelled_quantity"])
            if quantity:
                legacy_items[batch_id].append(
                    FboAllocation(
                        article=row["from_article"],
                        fulfillment=row["from_fulfillment"],
                        quantity=quantity,
                        barcode=row["barcode"] or "",
                        name=row["name"] or "",
                        purchase_price=row["purchase_price"],
                    )
                )
        fulfillments = await self.connection.scalars(
            select(FULFILLMENTS.c.name).order_by(FULFILLMENTS.c.name)
        )
        return FboEvidence(
            confirmed=confirmed,
            supplies=SUPPLIES.validate_json(snapshot["payload_json"] if snapshot else "[]"),
            movements=tuple(FboMovement.model_validate_json(row) for row in movements),
            balances=tuple(FboBalance.model_validate(row) for row in balances.mappings()),
            legacy=tuple(
                row.model_copy(update={"items": tuple(legacy_items[batch_id])})
                for batch_id, row in legacy_meta.items()
            ),
            fulfillments=tuple(fulfillments),
        )

    async def claim(self, command: FboStockCommand) -> FboStockResult | None:
        payload = command.change.model_dump_json(
            exclude={"fulfillment"} if not command.change.fulfillment else set()
        )
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()
        statement = upsert(
            self.connection,
            REQUESTS,
            {
                "store_slug": command.target.store_slug,
                "user_id": command.user_id,
                "request_key": command.request_key,
                "payload_hash": payload_hash,
                "created_at": datetime.now(UTC).isoformat(),
            },
            ("store_slug", "user_id", "request_key"),
            updates={"request_key": command.request_key},
        )
        await self.connection.execute(statement)
        row = (
            (
                await self.connection.execute(
                    select(REQUESTS).where(
                        REQUESTS.c.store_slug == command.target.store_slug,
                        REQUESTS.c.user_id == command.user_id,
                        REQUESTS.c.request_key == command.request_key,
                    )
                )
            )
            .mappings()
            .one()
        )
        if row["payload_hash"] != payload_hash:
            raise FboStockError(FboStockErrorCode.CONFLICT)
        return FboStockResult.model_validate_json(row["response_json"]) if row["response_json"] else None

    async def finish(self, command: FboStockCommand, result: FboStockResult) -> None:
        await self.connection.execute(
            update(REQUESTS)
            .where(
                REQUESTS.c.store_slug == command.target.store_slug,
                REQUESTS.c.user_id == command.user_id,
                REQUESTS.c.request_key == command.request_key,
            )
            .values(response_json=result.model_dump_json(exclude_computed_fields=True), status_code=200)
        )

    async def _purchase_price(self, allocation: FboAllocation) -> float | None:
        if not self._prices_loaded:
            await self._load_prices()
        exact = self._prices_by_article.get(allocation.article)
        return exact if exact is not None else self._prices_by_barcode.get(allocation.barcode)

    async def _load_prices(self) -> None:
        query = (
            select(CATALOG.c.id, CATALOG.c.article, CATALOG.c.barcode, PRICES.c.purchase_price)
            .outerjoin(
                PRICES,
                PRICES.c.stock_item_id == CATALOG.c.id,
            )
            .where(CATALOG.c.store_slug == self.target.store_slug, CATALOG.c.marketplace == "WB")
        )
        products = (await self.connection.execute(query)).mappings().all()
        aliases: dict[int, set[str]] = defaultdict(set)
        alias_rows = await self.connection.execute(
            select(BARCODES.c.stock_item_id, BARCODES.c.barcode)
            .join(CATALOG, CATALOG.c.id == BARCODES.c.stock_item_id)
            .where(CATALOG.c.store_slug == self.target.store_slug, CATALOG.c.marketplace == "WB")
        )
        for row in alias_rows.mappings():
            aliases[row["stock_item_id"]].add(row["barcode"])
        barcode_prices: dict[str, list[float | None]] = defaultdict(list)
        for row in products:
            price = float(row["purchase_price"]) if row["purchase_price"] is not None else None
            self._prices_by_article[row["article"]] = price
            for barcode in aliases[row["id"]] | {row["barcode"]}:
                if barcode:
                    barcode_prices[barcode].append(price)
        self._prices_by_barcode = {
            barcode: prices[0] for barcode, prices in barcode_prices.items() if len(prices) == 1
        }
        self._prices_loaded = True

    async def _stock_delta(self, allocation: FboAllocation, quantity: int, now: str) -> None:
        if quantity < 0:
            changed = await self.connection.scalar(
                update(STOCK)
                .where(
                    STOCK.c.store_slug == self.target.store_slug,
                    STOCK.c.marketplace == self.target.marketplace,
                    STOCK.c.article == allocation.article,
                    STOCK.c.fulfillment == allocation.fulfillment,
                    STOCK.c.quantity >= -quantity,
                )
                .values(quantity=STOCK.c.quantity + quantity, updated_at=now)
                .returning(STOCK.c.id)
            )
            if changed is None:
                raise FboStockError(FboStockErrorCode.CONFLICT)
        elif quantity > 0:
            await self.connection.execute(
                upsert(
                    self.connection,
                    STOCK,
                    {
                        "store_slug": self.target.store_slug,
                        "marketplace": self.target.marketplace,
                        "article": allocation.article,
                        "fulfillment": allocation.fulfillment,
                        "quantity": quantity,
                        "updated_at": now,
                    },
                    ("store_slug", "article", "fulfillment", "marketplace"),
                    updates={
                        "quantity": STOCK.c.quantity + quantity,
                        "updated_at": now,
                    },
                )
            )

    async def _operation(
        self,
        movement: FboMovement,
        actor: FboActor,
        batch_id: int,
        allocation: FboAllocation,
        quantity: int,
        kind: str,
    ) -> None:
        operation_id = await self.connection.scalar(
            insert(OPERATIONS)
            .values(
                store_slug=self.target.store_slug,
                kind=kind,
                source_type=FBO_STOCK.source,
                source_name=movement.supply.supply_id,
                from_fulfillment=allocation.fulfillment,
                from_marketplace=self.target.marketplace,
                to_fulfillment=movement.supply.warehouse,
                to_marketplace=self.target.marketplace,
                user_id=actor.id,
                user_name=actor.name,
                created_at=movement.updated_at,
                transit_batch_id=batch_id,
            )
            .returning(OPERATIONS.c.id)
        )
        await self.connection.execute(
            insert(OPERATION_ITEMS).values(
                operation_id=operation_id,
                article=allocation.article,
                barcode=allocation.barcode,
                name=allocation.name,
                quantity=quantity,
                purchase_price=allocation.purchase_price,
                purchase_price_recorded=1,
            )
        )
        await self.connection.execute(
            insert(ACTIVITY).values(
                user_id=actor.id,
                user_name=actor.name,
                action=kind,
                details=movement.supply.supply_id,
                created_at=movement.updated_at,
                operation_id=operation_id,
            )
        )

    async def save(
        self,
        previous: FboMovement | None,
        movement: FboMovement,
        actor: FboActor,
        *,
        adopt: tuple[int, ...] = (),
    ) -> FboMovement:
        target = self.target
        old = {(row.fulfillment, row.article): row for row in previous.allocations} if previous else {}
        allocations = []
        for allocation_row in movement.allocations:
            prior = old.get((allocation_row.fulfillment, allocation_row.article))
            price = prior.purchase_price if prior else await self._purchase_price(allocation_row)
            allocations.append(allocation_row.model_copy(update={"purchase_price": price}))
        movement = movement.model_copy(update={"allocations": tuple(allocations)})
        new = {(row.fulfillment, row.article): row for row in movement.allocations}
        if adopt:
            rows = (
                (
                    await self.connection.execute(
                        select(BATCHES).where(BATCHES.c.id.in_(adopt)).with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                await self.connection.execute(
                    insert(LINKS).values(
                        batch_id=row["id"],
                        store_slug=target.store_slug,
                        marketplace=target.marketplace,
                        supply_key=movement.supply.key,
                        fulfillment=row["from_fulfillment"],
                    )
                )
        links = (
            (
                await self.connection.execute(
                    select(LINKS.c.batch_id, LINKS.c.fulfillment)
                    .where(
                        LINKS.c.store_slug == target.store_slug,
                        LINKS.c.marketplace == target.marketplace,
                        LINKS.c.supply_key == movement.supply.key,
                    )
                    .order_by(LINKS.c.batch_id)
                )
            )
            .mappings()
            .all()
        )
        by_ff: dict[str, list[int]] = defaultdict(list)
        for row in links:
            by_ff[row["fulfillment"]].append(row["batch_id"])
        for fulfillment in sorted({key[0] for key in old.keys() | new.keys()}):
            if not by_ff[fulfillment]:
                batch_id = await self.connection.scalar(
                    insert(BATCHES)
                    .values(
                        store_slug=target.store_slug,
                        kind=FBO_STOCK.batch_kind,
                        from_fulfillment=fulfillment,
                        from_marketplace=target.marketplace,
                        to_fulfillment=movement.supply.warehouse,
                        to_marketplace=target.marketplace,
                        status=FBO_STOCK.batch_active,
                        note="",
                        sent_by_name=actor.name,
                        sent_by_user_id=actor.id,
                        sent_at=movement.posted_at,
                    )
                    .returning(BATCHES.c.id)
                )
                if batch_id is None:
                    raise FboStockError(FboStockErrorCode.CONFLICT)
                await self.connection.execute(
                    insert(LINKS).values(
                        batch_id=batch_id,
                        store_slug=target.store_slug,
                        marketplace=target.marketplace,
                        supply_key=movement.supply.key,
                        fulfillment=fulfillment,
                    )
                )
                by_ff[fulfillment].append(batch_id)
            batch_ids = by_ff[fulfillment]
            batch_id = batch_ids[0]
            active_rows = [row for row in new.values() if row.fulfillment == fulfillment]
            accepted = movement.state == FboStockState.ACCEPTED
            status = FBO_STOCK.batch_accepted if accepted else FBO_STOCK.batch_active
            if not active_rows:
                status = FBO_STOCK.batch_cancelled
            for identity in sorted(old.keys() | new.keys()):
                if identity[0] != fulfillment:
                    continue
                before, after = old.get(identity), new.get(identity)
                delta = (after.quantity if after else 0) - (before.quantity if before else 0)
                allocation = after or before
                if allocation is None:
                    continue
                if delta:
                    await self._stock_delta(allocation, -delta, movement.updated_at)
                    await self._operation(
                        movement,
                        actor,
                        batch_id,
                        allocation,
                        abs(delta),
                        FBO_STOCK.dispatch_operation if delta > 0 else FBO_STOCK.return_operation,
                    )
                old_received = (
                    before.quantity if previous and previous.state == FboStockState.ACCEPTED and before else 0
                )
                received = after.quantity if accepted and after else 0
                if received != old_received:
                    await self._operation(
                        movement,
                        actor,
                        batch_id,
                        allocation,
                        abs(received - old_received),
                        FBO_STOCK.receive_operation
                        if received > old_received
                        else FBO_STOCK.reopen_operation,
                    )
            existing = (
                (
                    await self.connection.execute(
                        select(ITEMS)
                        .where(ITEMS.c.batch_id.in_(batch_ids))
                        .order_by(ITEMS.c.id)
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            by_article: dict[str, list[int]] = defaultdict(list)
            for row in existing:
                by_article[row["to_article"]].append(row["id"])
            for article in sorted(set(by_article) | {row.article for row in active_rows}):
                allocation = new.get((fulfillment, article))
                quantity = allocation.quantity if allocation else 0
                ids = by_article[article]
                values = {
                    "sent_quantity": quantity,
                    "received_quantity": quantity if accepted else 0,
                    "cancelled_quantity": 0,
                }
                if ids:
                    await self.connection.execute(update(ITEMS).where(ITEMS.c.id == ids[0]).values(**values))
                    if len(ids) > 1:
                        await self.connection.execute(
                            update(ITEMS)
                            .where(ITEMS.c.id.in_(ids[1:]))
                            .values(
                                sent_quantity=0,
                                received_quantity=0,
                                cancelled_quantity=0,
                            )
                        )
                elif allocation:
                    await self.connection.execute(
                        insert(ITEMS).values(
                            **values,
                            batch_id=batch_id,
                            from_article=article,
                            to_article=article,
                            barcode=allocation.barcode,
                            name=allocation.name,
                            purchase_price=allocation.purchase_price,
                        )
                    )
            await self.connection.execute(
                update(BATCHES)
                .where(BATCHES.c.id.in_(batch_ids))
                .values(
                    status=status,
                    to_fulfillment=movement.supply.warehouse,
                    last_received_at=movement.updated_at if accepted else None,
                    last_received_by_name=FBO_STOCK.source if accepted else None,
                )
            )
        await self.connection.execute(
            upsert(
                self.connection,
                MOVEMENTS,
                {
                    "store_slug": target.store_slug,
                    "marketplace": target.marketplace,
                    "supply_key": movement.supply.key,
                    "payload_json": movement.model_dump_json(exclude_computed_fields=True),
                },
                ("store_slug", "marketplace", "supply_key"),
            )
        )
        return movement
