from collections import defaultdict
from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.engine import ScalarResult

from app.core.constants import OZON_CABINET, OZON_PROMOTIONS, Marketplace
from app.core.stores import STORES
from app.dto.ozon_buyout import OzonBuyoutLine
from app.dto.ozon_cabinet import (
    OzonBuyoutSnapshot,
    OzonCabinetRecord,
    OzonCabinetSave,
    OzonCabinetValues,
    OzonCabinetView,
    OzonProductGoals,
    OzonTeamCommission,
)
from app.dto.ozon_economics import OzonProductsQuery
from app.dto.ozon_promotions import OzonBoostRecord, OzonBoostSave, OzonBoostSettings
from app.infrastructure.async_database import AsyncDatabase, table_for, upsert
from app.infrastructure.orm import (
    SalesOrderLineRecord,
    StockItemRecord,
    UnitEconomics1CProductSettingRecord,
    UnitEconomics1CSourceValueRecord,
)
from app.infrastructure.ozon_cabinet_orm import OzonBuyoutDailyRecord, OzonBuyoutRunRecord
from app.infrastructure.ozon_cabinet_orm import OzonCabinetRecord as CabinetTable


class OzonCabinetConflict(Exception):
    pass


def stored_cabinet_values(payload: str) -> OzonCabinetValues:
    values = OzonCabinetValues.model_validate_json(payload)
    return values.model_copy(
        update={"buyout": values.buyout.model_copy(update={"period_days": OZON_CABINET.default_period_days})}
    )


class OzonCabinetRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def boost_settings(self) -> OzonBoostRecord:
        table = table_for(CabinetTable)
        async with self.database.transaction() as connection:
            row = (
                await connection.execute(
                    select(table).where(table.c.store_slug == OZON_PROMOTIONS.settings_scope)
                )
            ).first()
        return (
            OzonBoostRecord(
                values=OzonBoostSettings.model_validate_json(row.payload),
                revision=row.revision,
                updated_at=row.updated_at,
                updated_by=row.updated_by,
            )
            if row is not None
            else OzonBoostRecord()
        )

    async def save_boost(self, command: OzonBoostSave) -> OzonBoostRecord:
        table = table_for(CabinetTable)
        async with self.database.transaction(write=True) as connection:
            if command.change.revision == 0:
                await connection.execute(
                    upsert(
                        connection,
                        table,
                        {
                            "store_slug": OZON_PROMOTIONS.settings_scope,
                            "revision": 0,
                            "payload": OzonBoostSettings().model_dump_json(),
                            "updated_at": command.saved_at.isoformat(),
                            "updated_by": command.user_name,
                        },
                        ("store_slug",),
                        updates={"store_slug": OZON_PROMOTIONS.settings_scope},
                    )
                )
            revision = await connection.scalar(
                update(table)
                .where(
                    table.c.store_slug == OZON_PROMOTIONS.settings_scope,
                    table.c.revision == command.change.revision,
                )
                .values(
                    payload=command.change.values.model_dump_json(),
                    revision=command.change.revision + 1,
                    updated_at=command.saved_at.isoformat(),
                    updated_by=command.user_name,
                )
                .returning(table.c.revision)
            )
            if revision is None:
                raise OzonCabinetConflict()
        return OzonBoostRecord(
            values=command.change.values,
            revision=revision,
            updated_at=command.saved_at,
            updated_by=command.user_name,
        )

    async def buyout_lines(self, query: OzonProductsQuery) -> tuple[OzonBuyoutLine, ...]:
        orders = table_for(SalesOrderLineRecord)
        statement = select(
            orders.c.order_key,
            orders.c.line_key,
            orders.c.article,
            orders.c.sold_quantity,
            orders.c.cancelled_quantity,
        ).where(
            orders.c.marketplace == Marketplace.OZON,
            orders.c.store_slug.in_(query.stores),
            func.substr(orders.c.ordered_at, 1, 10).between(
                query.period_from.isoformat(), query.period_to.isoformat()
            ),
        )
        if query.article:
            statement = statement.where(orders.c.article == query.article)
        async with self.database.transaction() as connection:
            return tuple(
                OzonBuyoutLine.model_validate(row) for row in (await connection.execute(statement)).mappings()
            )

    async def settings(self, stores: tuple[str, ...]) -> tuple[OzonCabinetRecord, ...]:
        table = table_for(CabinetTable)
        async with self.database.transaction() as connection:
            records = {
                row.store_slug: OzonCabinetRecord(
                    store_slug=row.store_slug,
                    revision=row.revision,
                    values=stored_cabinet_values(row.payload),
                    updated_at=row.updated_at,
                    updated_by=row.updated_by,
                )
                for row in await connection.execute(select(table).where(table.c.store_slug.in_(stores)))
            }
        return tuple(records.get(store, OzonCabinetRecord(store_slug=store)) for store in stores)

    async def save(self, command: OzonCabinetSave) -> OzonCabinetRecord:
        table = table_for(CabinetTable)
        async with self.database.transaction(write=True) as connection:
            if command.change.revision == 0:
                await connection.execute(
                    upsert(
                        connection,
                        table,
                        {
                            "store_slug": command.store_slug,
                            "revision": 0,
                            "payload": OzonCabinetValues().model_dump_json(),
                            "updated_at": command.saved_at.isoformat(),
                            "updated_by": command.user_name,
                        },
                        ("store_slug",),
                        updates={"store_slug": command.store_slug},
                    )
                )
            row = (
                await connection.execute(select(table).where(table.c.store_slug == command.store_slug))
            ).first()
            if row is None or row.revision != command.change.revision:
                raise OzonCabinetConflict()
            values = stored_cabinet_values(row.payload).model_copy(
                update={
                    key: value
                    for key in ("goals", "buyout", "expenses", "forecast")
                    if (value := getattr(command.change, key)) is not None
                }
            )
            revision = await connection.scalar(
                update(table)
                .where(
                    table.c.store_slug == command.store_slug,
                    table.c.revision == command.change.revision,
                )
                .values(
                    payload=values.model_dump_json(),
                    revision=command.change.revision + 1,
                    updated_at=command.saved_at.isoformat(),
                    updated_by=command.user_name,
                )
                .returning(table.c.revision)
            )
            if revision is None:
                raise OzonCabinetConflict()
        return OzonCabinetRecord(
            store_slug=command.store_slug,
            values=values,
            revision=revision,
            updated_at=command.saved_at,
            updated_by=command.user_name,
        )

    async def views(self, stores: tuple[str, ...]) -> tuple[OzonCabinetView, ...]:
        settings = await self.settings(stores)
        items = table_for(StockItemRecord)
        source = table_for(UnitEconomics1CSourceValueRecord)
        rates: dict[str, set[float]] = defaultdict(set)
        total: dict[str, int] = defaultdict(int)
        missing: dict[str, int] = defaultdict(int)
        times: dict[str, list[str]] = defaultdict(list)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(
                    items.c.store_slug,
                    source.c.team_commission_percent,
                    source.c.synced_at,
                )
                .select_from(items.outerjoin(source, source.c.stock_item_id == items.c.id))
                .where(
                    items.c.store_slug.in_(stores),
                    items.c.marketplace == Marketplace.OZON,
                    items.c.is_service == 0,
                )
            )
            for row in rows:
                total[row.store_slug] += 1
                if row.team_commission_percent is None:
                    missing[row.store_slug] += 1
                else:
                    rates[row.store_slug].add(row.team_commission_percent)
                if row.synced_at:
                    times[row.store_slug].append(row.synced_at)
        return tuple(
            OzonCabinetView(
                **item.model_dump(),
                store_name=STORES[item.store_slug].name,
                team_commission=OzonTeamCommission(
                    percent=next(iter(rates[item.store_slug])) if len(rates[item.store_slug]) == 1 else None,
                    values=tuple(sorted(rates[item.store_slug])),
                    products=total[item.store_slug],
                    missing_products=missing[item.store_slug],
                    updated_at=datetime.fromisoformat(min(times[item.store_slug]))
                    if times[item.store_slug]
                    else None,
                ),
            )
            for item in settings
        )

    async def product_goals(self, stores: tuple[str, ...]) -> dict[tuple[str, str], OzonProductGoals]:
        table = table_for(UnitEconomics1CProductSettingRecord)
        async with self.database.transaction() as connection:
            rows = await connection.execute(
                select(table).where(
                    table.c.store_slug.in_(stores),
                    table.c.marketplace == Marketplace.OZON,
                )
            )
            return {
                (row.store_slug, row.article): OzonProductGoals(
                    target_drr_percent=row.target_drr_percent,
                    target_roi_percent=row.target_roi_percent,
                )
                for row in rows
            }

    async def save_buyout(self, snapshot: OzonBuyoutSnapshot) -> None:
        table = table_for(OzonBuyoutDailyRecord)
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    table_for(OzonBuyoutRunRecord),
                    {
                        "store_slug": snapshot.store_slug,
                        "attempted_at": snapshot.observed_at.isoformat(),
                        "error": None,
                    },
                    ("store_slug",),
                )
            )
            await connection.execute(
                upsert(
                    connection,
                    table,
                    {
                        "store_slug": snapshot.store_slug,
                        "day": snapshot.day.isoformat(),
                        "payload": snapshot.model_dump_json(),
                    },
                    ("store_slug", "day"),
                )
            )

    async def fail_buyout(self, store: str, error: str) -> None:
        async with self.database.transaction(write=True) as connection:
            await connection.execute(
                upsert(
                    connection,
                    table_for(OzonBuyoutRunRecord),
                    {
                        "store_slug": store,
                        "attempted_at": datetime.now(UTC).isoformat(),
                        "error": error,
                    },
                    ("store_slug",),
                )
            )

    async def buyouts(self, stores: tuple[str, ...], day: date) -> tuple[OzonBuyoutSnapshot, ...]:
        table = table_for(OzonBuyoutDailyRecord)
        latest = (
            select(table.c.store_slug, func.max(table.c.day).label("day"))
            .where(
                table.c.store_slug.in_(stores),
                table.c.day <= day.isoformat(),
            )
            .group_by(table.c.store_slug)
            .subquery()
        )
        async with self.database.transaction() as connection:
            rows: ScalarResult[str] = await connection.scalars(
                select(table.c.payload).join(
                    latest,
                    (table.c.store_slug == latest.c.store_slug) & (table.c.day == latest.c.day),
                )
            )
            snapshots = tuple(OzonBuyoutSnapshot.model_validate_json(row) for row in rows)
            runs = table_for(OzonBuyoutRunRecord)
            errors = {
                row.store_slug: row.error
                for row in await connection.execute(select(runs).where(runs.c.store_slug.in_(stores)))
            }
            return tuple(item.model_copy(update={"error": errors.get(item.store_slug)}) for item in snapshots)
