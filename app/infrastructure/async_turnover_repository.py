from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import case, func, select

from app.core.constants import OZON_ORDERS, TIME, Marketplace, OrderScheme
from app.dto.ozon_economics import OzonConfirmedDay, OzonOrderDay, OzonProductsQuery, OzonTurnoverData
from app.infrastructure.async_database import AsyncDatabase, table_for
from app.infrastructure.async_orders_repository import AsyncOrdersRepository
from app.infrastructure.economics_coverage_orm import economics_source_days
from app.infrastructure.orm import SalesOrderLineRecord, SalesSyncStateRecord


class AsyncTurnoverRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def load(self, query: OzonProductsQuery) -> OzonTurnoverData:
        orders = table_for(SalesOrderLineRecord)
        states = table_for(SalesSyncStateRecord)
        day = func.substr(orders.c.ordered_at, 1, 10)
        statement = (
            select(
                orders.c.store_slug,
                orders.c.article,
                day.label("day"),
                func.sum(orders.c.order_amount - orders.c.cancelled_amount).label("amount"),
                func.sum(orders.c.quantity - orders.c.cancelled_quantity).label("ordered"),
                func.sum(orders.c.sold_quantity).label("received"),
                func.sum(orders.c.cancelled_quantity).label("cancelled"),
                func.sum(case((orders.c.scheme == OrderScheme.FBS, orders.c.quantity), else_=0)).label(
                    "fbs_ordered"
                ),
            )
            .where(
                orders.c.marketplace == Marketplace.OZON,
                orders.c.store_slug.in_(query.stores),
                day.between(query.period_from.isoformat(), query.period_to.isoformat()),
            )
            .group_by(orders.c.store_slug, orders.c.article, day)
        )
        if query.article:
            statement = statement.where(orders.c.article == query.article)
        async with self.database.transaction() as connection:
            amounts = tuple(
                OzonOrderDay.model_validate(row) for row in (await connection.execute(statement)).mappings()
            )
            confirmed = tuple(
                OzonConfirmedDay.model_validate(row)
                for row in (
                    await connection.execute(
                        select(economics_source_days.c.store_slug, economics_source_days.c.day).where(
                            economics_source_days.c.marketplace == Marketplace.OZON,
                            economics_source_days.c.source == OZON_ORDERS.coverage_source,
                            economics_source_days.c.store_slug.in_(query.stores),
                            economics_source_days.c.day.between(
                                query.period_from.isoformat(), query.period_to.isoformat()
                            ),
                        )
                    )
                ).mappings()
            )
            stale = frozenset(
                (
                    await connection.execute(
                        select(states.c.store_slug).where(
                            states.c.marketplace == Marketplace.OZON,
                            states.c.store_slug.in_(query.stores),
                            states.c.ok == 0,
                        )
                    )
                ).scalars()
            )
        products: dict[tuple[str, str], list[OzonOrderDay]] = defaultdict(list)
        days: dict[str, set[date]] = defaultdict(set)
        for amount in amounts:
            products[amount.store_slug, amount.article].append(amount)
        for item in confirmed:
            days[item.store_slug].add(item.day)
        states_by_day = {
            (item.store_slug, item.day, item.scheme): item
            for item in await AsyncOrdersRepository(self.database).days(query.stores)
        }
        eligible: dict[str, set[date]] = defaultdict(set)
        updated = {}
        for store, cohort_day, _ in states_by_day:
            entries = tuple(states_by_day.get((store, cohort_day, scheme)) for scheme in OZON_ORDERS.schemes)
            if all(
                item is not None
                and item.listed
                and item.ok
                and (item.finalized or cohort_day == datetime.now(TIME.moscow_timezone).date())
                for item in entries
            ):
                eligible[store].add(cohort_day)
                updated[store, cohort_day] = min(item.attempted_at for item in entries if item is not None)
            if all(item is not None and item.finalized for item in entries):
                days[store].add(cohort_day)
        return OzonTurnoverData(
            products={key: tuple(rows) for key, rows in products.items()},
            confirmed_days={store: frozenset(values) for store, values in days.items()},
            stale_stores=stale,
            eligible_days={store: frozenset(values) for store, values in eligible.items()},
            updated_at=updated,
        )
