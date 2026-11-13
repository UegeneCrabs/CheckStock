from datetime import date, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import JSON, and_, bindparam, case, cast, func, select, text, type_coerce, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import (
    OZON_ORDERS,
    TIME,
    DatabaseDialect,
    Marketplace,
    OrderScheme,
    OrderSyncStage,
)
from app.dto.orders import (
    OrderSaveBatch,
    OzonOrderDateRepair,
    OzonOrderDayState,
    OzonPendingPosting,
    SalesOrderLine,
)
from app.dto.ozon_logistics import LogisticsPeriod
from app.dto.ozon_new_product_costs import OzonFboLocality
from app.dto.ozon_orders import OzonPostingDates
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.economics_coverage_orm import economics_source_days, ozon_order_days
from app.infrastructure.orm import SalesOrderLineRecord, SalesSyncStateRecord, SyncHealthRecord


class AsyncOrdersRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def fbo_locality(self, store: str, period: LogisticsPeriod) -> OzonFboLocality:
        table = table_for(SalesOrderLineRecord)
        raw = (
            cast(table.c.raw_json, JSON)
            if self.database.dialect == DatabaseDialect.POSTGRESQL
            else type_coerce(case((func.json_valid(table.c.raw_json), table.c.raw_json)), JSON)
        )
        origin = func.nullif(func.trim(raw["financial_data"]["cluster_from"].as_string()), "")
        destination = func.nullif(func.trim(raw["financial_data"]["cluster_to"].as_string()), "")
        route = case(
            (and_(origin.is_not(None), destination.is_not(None)), case((origin == destination, 1), else_=0))
        )
        postings = (
            select(func.min(route).label("min_route"), func.max(route).label("max_route"))
            .where(
                table.c.store_slug == store,
                table.c.marketplace == Marketplace.OZON,
                table.c.scheme == OrderScheme.FBO,
                table.c.ordered_at >= period.start.isoformat(),
                table.c.ordered_at < period.day.isoformat(),
            )
            .group_by(table.c.order_key)
            .subquery()
        )
        confirmed = postings.c.min_route == postings.c.max_route
        async with self.database.transaction() as connection:
            counts = (
                await connection.execute(
                    select(
                        func.count().label("total"),
                        func.count(case((and_(confirmed, postings.c.min_route == 1), 1))).label("local"),
                        func.count(case((and_(confirmed, postings.c.min_route == 0), 1))).label(
                            "nonlocal_count"
                        ),
                    ).select_from(postings)
                )
            ).one()
        covered = sum(
            state.scheme == OrderScheme.FBO
            and period.start <= state.day <= period.end
            and state.listed
            and state.finalized
            and state.ok
            for state in await self.days((store,))
        )
        return OzonFboLocality(
            period=period,
            local=counts.local,
            nonlocal_count=counts.nonlocal_count,
            unknown=counts.total - counts.local - counts.nonlocal_count,
            covered_days=covered,
        )

    async def days(self, stores: tuple[str, ...]) -> tuple[OzonOrderDayState, ...]:
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(ozon_order_days.c.payload).where(ozon_order_days.c.store_slug.in_(stores))
            )
            result = {
                (item.store_slug, item.scheme, item.day): item
                for row in rows
                for item in (OzonOrderDayState.model_validate_json(row.payload),)
            }
            legacy = await connection.execute(
                select(economics_source_days).where(
                    economics_source_days.c.marketplace == Marketplace.OZON,
                    economics_source_days.c.source == OZON_ORDERS.coverage_source,
                    economics_source_days.c.store_slug.in_(stores),
                )
            )
            for row in legacy:
                for scheme in OZON_ORDERS.schemes:
                    day = date.fromisoformat(row.day)
                    result.setdefault(
                        (row.store_slug, scheme, day),
                        OzonOrderDayState(
                            store_slug=row.store_slug,
                            scheme=scheme,
                            day=day,
                            listed=True,
                            finalized=True,
                            ok=True,
                            attempted_at=datetime.fromisoformat(row.updated_at),
                        ),
                    )
            return tuple(result.values())

    async def pending(self, store: str) -> tuple[OzonPendingPosting, ...]:
        table = table_for(SalesOrderLineRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(
                    table.c.scheme,
                    func.substr(table.c.ordered_at, 1, 10).label("day"),
                    table.c.order_key.label("posting_number"),
                    table.c.external_order_id.label("order_number"),
                    table.c.raw_json,
                )
                .where(
                    table.c.marketplace == Marketplace.OZON,
                    table.c.store_slug == store,
                    table.c.sold_quantity + table.c.cancelled_quantity < table.c.quantity,
                )
                .distinct()
            )
            return tuple(
                OzonPendingPosting.model_validate(
                    {
                        "scheme": row.scheme,
                        "posting_number": row.posting_number,
                        "order_number": row.order_number,
                        "filter_day": OzonPostingDates.model_validate_json(row.raw_json).filter_day(
                            row.scheme, date.fromisoformat(row.day)
                        ),
                    }
                )
                for row in rows
            )

    async def save(self, batch: OrderSaveBatch) -> None:
        if not isinstance(batch, OrderSaveBatch):
            raise TypeError("OrderSaveBatch is required")
        attempted_at = batch.attempted_at.isoformat(timespec="seconds")
        async with self.database.transaction(write=True) as connection:
            await self._lock_store(connection, batch.store_slug)
            table = table_for(SalesOrderLineRecord)
            keys = ("store_slug", "marketplace", "order_key", "line_key")
            if batch.lines:
                values = [self._values(line, attempted_at) for line in batch.lines]
                await connection.execute(upsert(connection, table, values[0], keys), values)
            for day in batch.days:
                await connection.execute(
                    upsert(
                        connection,
                        ozon_order_days,
                        {
                            "store_slug": day.store_slug,
                            "scheme": day.scheme.value,
                            "day": day.day.isoformat(),
                            "payload": day.model_dump_json(),
                        },
                        ("store_slug", "scheme", "day"),
                    )
                )
            if batch.report_status:
                await self._status(connection, batch, attempted_at)
            if not batch.incremental:
                await self._coverage(connection, batch, attempted_at)

    async def _lock_store(self, connection: AsyncConnection, store: str) -> None:
        if self.database.dialect == DatabaseDialect.POSTGRESQL:
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:store), hashtext(:marketplace))"),
                {"store": store, "marketplace": Marketplace.OZON.value},
            )

    async def stores_with_orders(self) -> tuple[str, ...]:
        table = table_for(SalesOrderLineRecord)
        async with self.database.transaction() as connection:
            return tuple(
                (
                    await connection.scalars(
                        select(table.c.store_slug)
                        .where(table.c.marketplace == Marketplace.OZON)
                        .distinct()
                        .order_by(table.c.store_slug)
                    )
                ).all()
            )

    async def repair_processing_dates(self, store: str, *, apply: bool) -> OzonOrderDateRepair:
        table = table_for(SalesOrderLineRecord)
        scope = (
            table.c.marketplace == Marketplace.OZON,
            table.c.store_slug == store,
        )
        query = select(table.c.order_key, table.c.line_key, table.c.ordered_at, table.c.raw_json).where(
            *scope
        )
        changes: list[dict[str, str]] = []
        moved = missing = changed = 0
        async with self.database.transaction(write=apply) as connection:
            if apply:
                await self._lock_store(connection, store)
            async with connection.stream(query) as rows:
                async for row in rows:
                    try:
                        accepted = (
                            OzonPostingDates.model_validate_json(row.raw_json).in_process_at
                            if row.raw_json
                            else None
                        )
                    except ValidationError:
                        accepted = None
                    if accepted is None:
                        missing += 1
                        continue
                    ordered_at = accepted.astimezone(TIME.moscow_timezone).isoformat(timespec="seconds")
                    if ordered_at == row.ordered_at:
                        continue
                    changed += 1
                    moved += ordered_at[:10] != row.ordered_at[:10]
                    if apply:
                        changes.append(
                            {
                                "repair_order": row.order_key,
                                "repair_line": row.line_key,
                                "repair_at": ordered_at,
                            }
                        )
            if changes:
                await connection.execute(
                    update(table)
                    .where(
                        *scope,
                        table.c.order_key == bindparam("repair_order"),
                        table.c.line_key == bindparam("repair_line"),
                    )
                    .values(ordered_at=bindparam("repair_at")),
                    changes,
                )
        return OzonOrderDateRepair(
            changed=changed, moved_to_another_day=moved, missing_processing_date=missing
        )

    @staticmethod
    async def _coverage(connection: AsyncConnection, batch: OrderSaveBatch, attempted_at: str) -> None:
        if any(issue.stage != OrderSyncStage.LOAD or issue.period is None for issue in batch.errors):
            return
        day = batch.period.start
        end = min(batch.period.end, batch.attempted_at.astimezone(TIME.moscow_timezone).date())
        while day < end:
            if not any(
                issue.period is not None and issue.period.start <= day < issue.period.end
                for issue in batch.errors
            ):
                await connection.execute(
                    upsert(
                        connection,
                        economics_source_days,
                        {
                            "marketplace": Marketplace.OZON.value,
                            "store_slug": batch.store_slug,
                            "source": OZON_ORDERS.coverage_source,
                            "day": day.isoformat(),
                            "updated_at": attempted_at,
                        },
                        ("marketplace", "store_slug", "source", "day"),
                    )
                )
            day += timedelta(days=1)

    @staticmethod
    def _values(line: SalesOrderLine, attempted_at: str) -> dict[str, object]:
        return {
            "store_slug": line.store_slug,
            "marketplace": line.marketplace.value,
            "order_key": line.order_key,
            "line_key": line.line_key,
            "external_order_id": line.external_order_id,
            "scheme": line.scheme.value,
            "status": line.status,
            "substatus": line.substatus,
            "article": line.article,
            "barcode": line.barcode,
            "name": line.name,
            "ordered_at": line.ordered_at,
            "source_updated_at": line.source_updated_at,
            "cancelled_at": line.cancelled_at,
            "sold_at": line.sold_at,
            "returned_at": line.returned_at,
            "quantity": line.quantity,
            "cancelled_quantity": line.cancelled_quantity,
            "sold_quantity": line.sold_quantity,
            "return_quantity": line.return_quantity,
            "order_amount": line.order_amount,
            "cancelled_amount": line.cancelled_amount,
            "sale_amount": line.sale_amount,
            "return_amount": line.return_amount,
            "currency": line.currency,
            "raw_json": line.raw_json,
            "synced_at": attempted_at,
        }

    async def _status(self, connection: AsyncConnection, batch: OrderSaveBatch, attempted_at: str) -> None:
        store, lookback_days, rows, error = (
            batch.store_slug,
            batch.lookback_days,
            batch.rows_received if batch.rows_received is not None else len(batch.lines),
            batch.error,
        )
        table = table_for(SalesSyncStateRecord)
        values = {
            "store_slug": store,
            "marketplace": Marketplace.OZON.value,
            "last_attempt_at": attempted_at,
            "last_success_at": attempted_at if error is None else None,
            "ok": int(error is None),
            "error": error,
            "rows_received": rows,
            "lookback_days": lookback_days,
        }
        statement = upsert(
            connection,
            table,
            values,
            ("store_slug", "marketplace"),
            updates={
                **{
                    key: value
                    for key, value in values.items()
                    if key not in {"store_slug", "marketplace", "last_success_at", "lookback_days"}
                },
                "last_success_at": func.coalesce(values["last_success_at"], table.c.last_success_at),
                "lookback_days": case(
                    (table.c.lookback_days >= lookback_days, table.c.lookback_days), else_=lookback_days
                ),
            },
        )
        await connection.execute(statement)
        await connection.execute(
            upsert(
                connection,
                table_for(SyncHealthRecord),
                {
                    "store_slug": store,
                    "marketplace": Marketplace.OZON.value,
                    "scope": OZON_ORDERS.health_scope,
                    "ok": int(error is None),
                    "error": error,
                    "checked_at": attempted_at,
                },
                ("store_slug", "marketplace", "scope"),
            )
        )
