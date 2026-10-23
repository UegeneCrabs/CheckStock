from datetime import date, datetime, timedelta

from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.constants import (
    OZON_ORDERS,
    TIME,
    DatabaseDialect,
    Marketplace,
    OrderSyncStage,
)
from app.dto.orders import OrderSaveBatch, OzonOrderDayState, OzonPendingPosting, SalesOrderLine
from app.dto.ozon_orders import OzonPostingDates
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.economics_coverage_orm import economics_source_days, ozon_order_days
from app.infrastructure.orm import SalesOrderLineRecord, SalesSyncStateRecord, SyncHealthRecord


class AsyncOrdersRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

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
                        "day": row.day,
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
            if self.database.dialect == DatabaseDialect.POSTGRESQL:
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext(:store), hashtext(:marketplace))"),
                    {"store": batch.store_slug, "marketplace": Marketplace.OZON.value},
                )
            table = table_for(SalesOrderLineRecord)
            keys = ("store_slug", "marketplace", "order_key", "line_key")
            if batch.lines:
                values = [self._values(line, attempted_at) for line in batch.lines]
                excluded = upsert(connection, table, values[0], keys).excluded
                statement = upsert(
                    connection,
                    table,
                    values[0],
                    keys,
                    updates={key: excluded[key] for key in values[0] if key not in {*keys, "ordered_at"}},
                )
                await connection.execute(statement, values)
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
