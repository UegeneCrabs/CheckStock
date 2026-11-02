from datetime import datetime, timedelta

from pydantic import BaseModel, JsonValue

from app.agents import reports
from app.agents.ozon_fields import json_value, product_row, row
from app.application.ozon_analytics import OzonAnalyticsService
from app.application.ozon_history import OzonHistoryService
from app.application.ozon_reports import OzonReportsService
from app.application.ports import AgentOzonProductsReader, AgentOzonReader
from app.core.constants import TIME, WEB, OzonHistoryPeriod
from app.dto.agent_ozon import AgentOzonRows
from app.dto.agent_query import ReportQuery
from app.dto.identity import User
from app.dto.ozon_analytics import OzonAnalyticsSalesQuery, OzonAnalyticsWeekQuery
from app.dto.ozon_economics import OzonProductsQuery
from app.dto.ozon_history import OzonHistoryQuery
from app.dto.ozon_reports import OzonProfitQuery


class AgentOzonService:
    def __init__(
        self,
        reader: AgentOzonReader,
        products: AgentOzonProductsReader,
        report: OzonReportsService,
        history: OzonHistoryService,
        analytics: OzonAnalyticsService,
    ):
        self.reader = reader
        self.products = products
        self.report = report
        self.history = history
        self.analytics = analytics

    async def with_managers(self, data: AgentOzonRows, stores: tuple[str, ...]) -> AgentOzonRows:
        catalog = await self.report.repository.catalog(stores)
        managers = {item.article: item.manager for item in catalog}
        return data.model_copy(
            update={
                "rows": [{**item, "manager": managers.get(str(item.get("article")))} for item in data.rows]
            }
        )

    async def load(self, name: str, query: ReportQuery, user: User) -> AgentOzonRows:
        today = datetime.now(TIME.moscow_timezone).date()
        end = query.date_to or today - timedelta(days=1)
        start = query.date_from or end - timedelta(days=WEB.week_days - 1)
        if query.store is None:
            raise ValueError("store_required")
        stores = (query.store,)
        if name == "product-details":
            catalog = await self.reader.common("products", query, user)
            stocks = await self.reader.common("stocks", query, user)
            rows = reports.filtered(catalog.rows, query)
            for item in rows:
                item["stock"] = [stock for stock in stocks.rows if stock.get("article") == item["article"]]
            return AgentOzonRows(rows=rows)
        if name in {
            "products",
            "stocks",
            "stock-history",
            "orders",
            "stock-operations",
            "supply-arrivals",
            "stock-cost-report",
        }:
            common_data = await self.reader.common(name, query, user)
            return await self.with_managers(common_data, stores) if name == "orders" else common_data
        if name == "inbound-supplies":
            return await self.reader.inbound(query)
        if name == "data-status":
            return await self.reader.status(query.store)
        if name == "advertising-campaigns":
            return await self.with_managers(await self.reader.campaigns(query), stores)
        if name == "prices" and query.date_from:
            return await self.reader.prices(query)
        if name == "ozon-commissions" and query.date_from:
            return await self.reader.commissions(query)
        if name == "economics-history":
            if query.article is None:
                raise ValueError("article_required")
            history = await self.history.history(
                OzonHistoryQuery(
                    store=query.store, article=query.article, days=OzonHistoryPeriod(query.days)
                ),
                today,
            )
            return AgentOzonRows(
                rows=[
                    {"article": query.article, "day": item.date.isoformat(), **row(item)}
                    for item in history.days
                    if history.period_from <= item.date <= history.period_to
                ],
                context={
                    "period_from": str(history.period_from),
                    "period_to": str(history.period_to),
                    "margin_available": False,
                    "current_day_partial": True,
                },
            )
        if name in {"ozon-analyzer", "ozon-ephemerides", "ozon-sales", "ozon-analytics-comments"}:
            data: BaseModel
            week = OzonAnalyticsWeekQuery(week=query.week)
            if name == "ozon-analyzer":
                data = await self.analytics.analyzer(stores, week)
            elif name == "ozon-ephemerides":
                data = await self.analytics.ephemerides(stores, week)
            elif name == "ozon-sales":
                data = await self.analytics.sales(
                    stores, OzonAnalyticsSalesQuery(date_from=start, date_to=end)
                )
            else:
                return await self.reader.comments(query)
            payload = row(data)
            parsed = AgentOzonRows.model_validate({"rows": payload.pop("rows", []), "context": payload})
            rows = parsed.rows
            for item in rows:
                for key in ("notes", "noteHistory", "comments"):
                    item.pop(key, None)
            return AgentOzonRows(rows=rows, context=payload)
        if name in {"profit", "ozon-profit-details"}:
            profit_report = await self.report.profit(
                OzonProfitQuery(
                    date_from=start,
                    date_to=end,
                    store=stores,
                    manager=(query.manager,) if query.manager else (),
                    article=(query.article,) if query.article else (),
                    daily_details=name == "ozon-profit-details",
                ),
                stores,
                export=True,
            )
            rows = [row(item) for item in profit_report.rows]
            coverage = await self.reader.order_coverage(
                query.model_copy(update={"date_from": start, "date_to": end})
            )
            for item in rows:
                item["orders_complete"] = coverage.complete
            if name == "profit":
                for item in rows:
                    item.pop("daily_calculations", None)
            return AgentOzonRows(
                rows=rows,
                context={
                    "website_totals": json_value(profit_report.totals),
                    "profit_basis": "estimated_expected_buyouts",
                    "category_unavailable_stores": list(profit_report.category_unavailable_stores),
                    "orders_coverage": json_value(coverage),
                },
            )
        if name == "target-prices":
            target_report = await self.report.targets(stores, article=query.article or "")
            return await self.with_managers(
                AgentOzonRows(
                    rows=[row(item) for item in target_report.rows],
                    context={
                        "period_from": str(target_report.period_from),
                        "period_to": str(target_report.period_to),
                    },
                ),
                stores,
            )
        products = await self.products.products(
            OzonProductsQuery(stores=stores, article=query.article or "", period_from=start, period_to=end),
            last_complete_day=today - timedelta(days=1),
            custom=True,
        )
        selected = [
            item
            for item in products.products
            if (not query.manager or item.manager == query.manager)
            and (query.is_new is None or item.is_new is query.is_new)
        ]
        rows = [product_row(name, item, details=query.details) for item in selected]
        if name == "advertising" and query.group_by == "day":
            daily: list[dict[str, JsonValue]] = []
            for selected_product in selected:
                summary = selected_product.advertising.spend_summary
                if summary is None:
                    continue
                for value in summary.days:
                    daily.append(
                        {
                            "article": selected_product.article,
                            "name": selected_product.name,
                            "day": str(value.day),
                            "spend": json_value(value.amount),
                            "complete": value.complete,
                            "stale": value.stale,
                        }
                    )
            rows = reports.grouped(daily, "day", ("spend",))
            for daily_row in rows:
                entries = [item for item in daily if item["day"] == daily_row["day"]]
                daily_row["complete"] = len(entries) == len(selected) and all(
                    item["complete"] is True for item in entries
                )
                daily_row["stale"] = any(item["stale"] for item in entries)
        context: dict[str, JsonValue] = {}
        if name in {"advertising", "ozon-advertising-breakdown", "profit-calculator"}:
            context.update(period_from=str(products.period_from), period_to=str(products.period_to))
        if name in {"ozon-buyout", "ozon-logistics", "ozon-storage"}:
            context.update(latest_saved_snapshot=True, source_periods_in_rows=True)
        if name in {"advertising", "ozon-advertising-breakdown"}:
            context["unallocated_advertising"] = json_value(products.advertising_unallocated)
        filtered_query = (
            query.model_copy(update={"article": None})
            if name == "advertising" and query.group_by == "day"
            else query
        )
        return AgentOzonRows(rows=reports.filtered(rows, filtered_query), context=context)
