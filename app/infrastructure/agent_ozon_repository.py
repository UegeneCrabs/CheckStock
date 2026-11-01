from datetime import datetime, timedelta

from pydantic import JsonValue
from sqlalchemy import text

from app.agents import reports
from app.agents.ozon_campaigns import campaign_rows
from app.agents.ozon_fields import json_value, row
from app.core.constants import AGENT_OZON, TIME, WEB, AgentOzonRecordType, Marketplace
from app.dto.agent_ozon import AgentOzonRows
from app.dto.agent_query import ReportQuery
from app.dto.identity import User
from app.dto.ozon_advertising import AdvertisingPeriod
from app.dto.ozon_analytics import OzonAnalyticsCommentKey
from app.dto.ozon_commissions import FboCommissionValue
from app.dto.ozon_economics import OzonCoverage, OzonProductsQuery
from app.dto.stock_pages import StockTarget
from app.infrastructure.async_advertising_repository import AsyncAdvertisingRepository
from app.infrastructure.async_database import AsyncDatabase
from app.infrastructure.async_prices_repository import stored_price_values
from app.infrastructure.async_read_models import AsyncReadModels
from app.infrastructure.async_turnover_repository import AsyncTurnoverRepository
from app.infrastructure.inbound_read_repository import InboundReadRepository
from app.infrastructure.ozon_analytics_repository import OzonAnalyticsRepository
from app.ozon.turnover_normalizer import period_turnover


class AgentOzonRepository:
    def __init__(self, database: AsyncDatabase):
        self.database = database

    async def order_coverage(self, query: ReportQuery) -> OzonCoverage:
        if query.store is None or query.date_from is None or query.date_to is None:
            raise ValueError("store_and_period_required")
        data = await AsyncTurnoverRepository(self.database).load(
            OzonProductsQuery(stores=(query.store,), period_from=query.date_from, period_to=query.date_to)
        )
        return period_turnover(
            data, query.store, query.article or "", query.date_from, query.date_to
        ).coverage

    async def common(self, name: str, query: ReportQuery, user: User) -> AgentOzonRows:
        from app.agents.extended_reports import arrivals, cost_report, orders, stock_history

        def load() -> AgentOzonRows:
            context: dict[str, JsonValue] = {}
            sources: list[dict[str, JsonValue]] = []
            if name == "products":
                rows = reports.catalog(query.store, query.marketplace)
            elif name == "stocks":
                if query.view == "summary":
                    rows, labels = reports.stock_summary(query)
                    context = {"labels": json_value(labels), "totals_include_transit": True}
                else:
                    rows = reports.stocks(query)
            elif name == "stock-history":
                rows = stock_history(query)
                context = {"transit_included": False}
            elif name == "orders":
                rows = orders(query.model_copy(update={"manager": None}), None)
                context = {"date_filter": "ordered_at", "status_basis": "latest_observed"}
            elif name == "stock-operations":
                rows = reports.operations(query, ((query.store, query.marketplace),))
            elif name == "supply-arrivals":
                rows, sources = arrivals(query, user)
                context = {"marketplace_scope": "shared_store_register", "date_filter": "actual_arrival"}
            elif name == "stock-cost-report":
                rows, context = cost_report(query, user)
            else:
                raise ValueError(name)
            return AgentOzonRows(rows=rows, context=context, sources=sources)

        return await AsyncReadModels(self.database).read(load)

    async def prices(self, query: ReportQuery) -> AgentOzonRows:
        statement = (
            "SELECT offer_id AS article, day, first_payload, last_payload, closed_at, complete "
            "FROM ozon_prices_daily WHERE store_slug=:store AND day BETWEEN :start AND :end "
            "AND product_id IN (SELECT mp_product_id FROM stock_items WHERE store_slug=:store "
            "AND marketplace='OZON' AND is_service=0) ORDER BY day, offer_id"
        )
        async with self.database.transaction() as connection:
            result = await connection.execute(
                text(statement),
                {"store": query.store, "start": str(query.date_from), "end": str(query.date_to)},
            )
            return AgentOzonRows(
                rows=[
                    {
                        "article": item.article,
                        "day": item.day,
                        "closed_at": item.closed_at,
                        "complete": bool(item.complete),
                        "first": row(stored_price_values(item.first_payload)),
                        "last": row(stored_price_values(item.last_payload)),
                    }
                    for item in result.mappings()
                ]
            ).model_copy(update={"context": {"daily_averages": False}})

    async def commissions(self, query: ReportQuery) -> AgentOzonRows:
        async with self.database.transaction() as connection:
            values = await connection.execute(
                text(
                    "SELECT offer_id AS article, day, first_payload, last_payload, closed_at "
                    "FROM ozon_fbo_commissions_daily WHERE store_slug=:store AND day BETWEEN :start AND :end "
                    "ORDER BY day, offer_id"
                ),
                {"store": query.store, "start": str(query.date_from), "end": str(query.date_to)},
            )
            return AgentOzonRows(
                rows=[
                    {
                        "article": item.article,
                        "day": item.day,
                        "closed_at": item.closed_at,
                        "first": row(FboCommissionValue.model_validate_json(item.first_payload)),
                        "last": row(FboCommissionValue.model_validate_json(item.last_payload)),
                    }
                    for item in values.mappings()
                ],
                context={"historical_price_basis_available": False},
            )

    async def status(self, store: str) -> AgentOzonRows:
        async with self.database.transaction() as connection:
            rows = []
            for table, daily in AGENT_OZON.source_tables:
                dates = ", MIN(day) AS first_observed, MAX(day) AS last_observed" if daily else ""
                row = (
                    (
                        await connection.execute(
                            text(f"SELECT COUNT(*) AS records{dates} FROM {table} WHERE store_slug=:store"),
                            {"store": store},
                        )
                    )
                    .mappings()
                    .one()
                )
                rows.append({"source": table, **row})
            return AgentOzonRows(rows=rows)

    async def inbound(self, query: ReportQuery) -> AgentOzonRows:
        if query.store is None:
            raise ValueError("store_required")
        snapshots = await InboundReadRepository(self.database).snapshots(
            (StockTarget(store_slug=query.store, marketplace=Marketplace.OZON),)
        )
        rows: list[dict[str, JsonValue]] = []
        sources: list[dict[str, JsonValue]] = []
        for snapshot in snapshots:
            sources.append(
                {
                    "source": "inbound_supplies",
                    "updated_at": snapshot.last_success,
                    "status": snapshot.status,
                    "has_error": bool(snapshot.error),
                }
            )
            for supply in snapshot.supplies:
                if query.supply_id and query.supply_id != supply.supply_id:
                    continue
                if query.status and query.status != supply.stage:
                    continue
                for item in supply.items:
                    rows.append(
                        {
                            **supply.model_dump(mode="json", exclude={"items"}),
                            **item.model_dump(mode="json"),
                            "items_missing": False,
                        }
                    )
        return AgentOzonRows(rows=rows, sources=sources)

    async def campaigns(self, query: ReportQuery) -> AgentOzonRows:
        if query.store is None or query.date_from is None or query.date_to is None:
            raise ValueError("store_and_period_required")
        states = await AsyncAdvertisingRepository(self.database).overlapping(
            (query.store,), AdvertisingPeriod(start=query.date_from, end=query.date_to)
        )
        return campaign_rows(query, states)

    async def comments(self, query: ReportQuery) -> AgentOzonRows:
        if query.store is None:
            raise ValueError("store_required")
        today = datetime.now(TIME.moscow_timezone).date()
        start = query.week or today
        start -= timedelta(days=start.weekday())
        repository = OzonAnalyticsRepository(self.database)
        values = await repository.comments((query.store,), start, start + timedelta(days=WEB.week_days - 1))
        rows: list[dict[str, JsonValue]] = []
        for item in values:
            if query.article and item.article != query.article:
                continue
            value = {key: value for key, value in row(item).items() if key not in {"author_id", "author"}}
            value["record_type"] = AgentOzonRecordType.COMMENT
            if query.include_history:
                revisions = await repository.comment_revisions(
                    OzonAnalyticsCommentKey(
                        store=query.store, article=item.article, week=item.week, kind=item.kind
                    )
                )
                value["history"] = json_value(
                    [
                        {
                            "text": entry.text,
                            "previous_text": entry.previous_text,
                            "version": entry.version,
                            "updated_at": entry.updated_at,
                        }
                        for entry in revisions
                    ]
                )
            rows.append(value)
        notes = await repository.notes(
            (query.store,), start, start + timedelta(days=WEB.week_days - 1), today
        )
        for note in notes:
            if query.article and note.article != query.article:
                continue
            value = {
                "record_type": AgentOzonRecordType.NOTE,
                "record_id": str(note.id),
                "store_slug": note.store_slug,
                "article": note.article,
                "week": str(start),
                "day": str(note.action_date),
                "text": note.note,
                "created_at": note.created_at.isoformat(),
                "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            }
            if query.include_history:
                value["history"] = json_value(
                    [
                        {
                            "text": entry.note,
                            "previous_text": entry.previous_note,
                            "updated_at": entry.edited_at,
                        }
                        for entry in note.revisions
                    ]
                )
            rows.append(value)
        return AgentOzonRows(rows=rows, context={"history_included": query.include_history})
