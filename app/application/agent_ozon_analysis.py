import asyncio
from datetime import date, datetime, timedelta
from http import HTTPStatus

from pydantic import JsonValue

from app.agents.ozon_fields import json_value, row
from app.agents.product_analysis import evaluate, number
from app.application.agent_ozon import AgentOzonService
from app.core.constants import AGENT_OZON, OZON_SCENARIO, TIME, AgentOzonDataStatus
from app.core.logging_config import log_tags, tagged_logger
from app.core.stores import STORES
from app.dto.agent_analysis import AnalysisQuery
from app.dto.agent_ozon import (
    AgentOzonAnalysisResponse,
    AgentOzonAnalysisRow,
    AgentOzonAnalysisTotals,
    AgentOzonRows,
    AgentOzonStoreFailure,
    AgentOzonSummaryMetrics,
    AgentOzonSummaryResponse,
    AgentOzonSummaryStore,
)
from app.dto.agent_query import ReportQuery
from app.dto.agent_summary import SummaryQuery
from app.dto.identity import User
from app.dto.ozon_economics import OzonProductsQuery

logger = tagged_logger(__name__, "agent", "ozon")


def summary_metrics(data: AgentOzonRows) -> AgentOzonSummaryMetrics:
    known = [
        item
        for item in data.rows
        if item.get("orders_amount") is not None and item.get("orders_complete") is True
    ]
    eligible = [item for item in known if (number(item["orders_amount"]) or 0) > 0]
    calculated = [item for item in eligible if item.get("margin_complete") and item.get("margin") is not None]
    orders_complete = bool(data.rows) and len(known) == len(data.rows)
    complete = orders_complete and len(calculated) == len(eligible)
    return AgentOzonSummaryMetrics(
        available=True,
        products_count=len(data.rows),
        orders_complete=orders_complete,
        orders_amount=sum(number(item["orders_amount"]) or 0 for item in known) if known else None,
        ordered_products_report_margin=sum(number(item["margin"]) or 0 for item in calculated)
        if calculated
        else 0.0
        if orders_complete and not eligible
        else None,
        margin_complete=complete,
        margin_included_products=len(calculated),
        margin_excluded_products=len(data.rows) - len(known) + len(eligible) - len(calculated),
        data_status=AgentOzonDataStatus.COMPLETE
        if complete
        else AgentOzonDataStatus.PARTIAL
        if known
        else AgentOzonDataStatus.MISSING,
    )


async def summary(
    service: AgentOzonService,
    query: SummaryQuery,
    stores: tuple[str, ...],
    user: User,
    previous_from: date,
    previous_to: date,
) -> AgentOzonSummaryResponse:
    results: list[AgentOzonSummaryStore] = []
    failures: list[AgentOzonStoreFailure] = []

    async def store_summary(store: str) -> None:
        try:
            request = ReportQuery(
                store=store,
                marketplace="OZON",
                date_from=query.date_from,
                date_to=query.date_to,
                manager=query.manager,
            )
            current = summary_metrics(await service.load("profit", request, user))
            previous = summary_metrics(
                await service.load(
                    "profit",
                    request.model_copy(update={"date_from": previous_from, "date_to": previous_to}),
                    user,
                )
            )
            current_amount, previous_amount = current.orders_amount, previous.orders_amount
            results.append(
                AgentOzonSummaryStore(
                    store=store,
                    name=str(STORES[store]["name"]),
                    current=current,
                    previous=previous,
                    turnover_change_rub=current_amount - previous_amount
                    if current_amount is not None and previous_amount is not None
                    else None,
                    turnover_change_percent=(current_amount - previous_amount)
                    / previous_amount
                    * OZON_SCENARIO.percent_factor
                    if current_amount is not None and previous_amount
                    else None,
                )
            )
        except Exception:
            logger.exception("agent_ozon_summary_failed store=%s", store, extra=log_tags(store))
            failures.append(AgentOzonStoreFailure(store=store, status_code=HTTPStatus.SERVICE_UNAVAILABLE))

    async with asyncio.TaskGroup() as group:
        for store in stores:
            group.create_task(store_summary(store))
    results.sort(
        key=lambda item: (item.current.orders_amount is None, -(item.current.orders_amount or 0), item.store)
    )
    return AgentOzonSummaryResponse(
        marketplace="OZON",
        currency="RUB",
        date_from=query.date_from,
        date_to=query.date_to,
        compare_from=previous_from,
        compare_to=previous_to,
        rows=results,
        failed_stores=failures,
        next_offset=None,
        warnings=(
            "ТО заказов до отмен; прибыль потенциальная по товарам с положительным ТО и полным расчётом. Пропуски не равны нулю.",
        ),
    )


async def analysis(
    service: AgentOzonService,
    query: AnalysisQuery,
    stores: tuple[str, ...],
    user: User,
    previous_from: date | None,
    previous_to: date | None,
    days: int,
) -> AgentOzonAnalysisResponse:
    results: list[AgentOzonAnalysisRow] = []
    unknown: list[AgentOzonAnalysisRow] = []
    failures: list[AgentOzonStoreFailure] = []
    checked = no_match = 0

    async def store_analysis(store: str) -> None:
        nonlocal checked, no_match
        try:
            request = ReportQuery(
                store=store,
                marketplace="OZON",
                date_from=query.date_from,
                date_to=query.date_to,
                manager=query.manager,
            )
            current = await service.load("profit", request, user)
            previous = (
                await service.load(
                    "profit",
                    request.model_copy(update={"date_from": previous_from, "date_to": previous_to}),
                    user,
                )
                if previous_from
                else None
            )
            old = {item["article"]: item for item in previous.rows} if previous else {}
            products = await service.products.products(
                OzonProductsQuery(stores=(store,), period_from=query.date_from, period_to=query.date_to),
                last_complete_day=datetime.now(TIME.moscow_timezone).date() - timedelta(days=1),
                custom=True,
            )
            identities = {item.article: item for item in products.products}
            previous_products = (
                await service.products.products(
                    OzonProductsQuery(
                        stores=(store,), period_from=previous_from, period_to=previous_to or previous_from
                    ),
                    last_complete_day=datetime.now(TIME.moscow_timezone).date() - timedelta(days=1),
                    custom=True,
                )
                if previous_from
                else None
            )
            old_identities = (
                {item.article: item for item in previous_products.products} if previous_products else {}
            )
            campaigns = (
                await service.reader.campaigns(request) if query.scenario == "active_low_ctr" else None
            )
            stock = (
                await service.reader.common(
                    "stocks",
                    ReportQuery(
                        store=store, marketplace="OZON", view="summary", fulfillment=query.fulfillment
                    ),
                    user,
                )
                if query.scenario.startswith("stock_")
                else None
            )
            stocks = {item["article"]: item for item in stock.rows} if stock else {}
            for item in current.rows:
                product = identities.get(str(item["article"]))
                if product is None or (query.manager and product.manager != query.manager):
                    continue
                filter_missing: list[str] = []
                if query.code:
                    if product.tag_data.code is None:
                        filter_missing.append("tag_code_unknown")
                    elif product.tag_data.code.casefold() != query.code.casefold():
                        continue
                if query.is_new is not None:
                    if product.is_new is None:
                        filter_missing.append("newness_unknown")
                    elif product.is_new is not query.is_new:
                        continue
                previous_product = old_identities.get(product.article)
                checked += 1
                state, metrics, reasons = evaluate(
                    query.scenario,
                    item,
                    old.get(product.article),
                    stocks.get(product.article),
                    row(product.tag_data),
                    [value for value in campaigns.rows if value["article"] == product.article]
                    if campaigns and campaigns.context.get("available") is True
                    else None,
                    orders_known=item.get("orders_count") is not None and item.get("orders_complete") is True,
                    previous_orders_known=bool(old.get(product.article))
                    and old[product.article].get("orders_complete") is True,
                    impressions_known=bool(
                        product.advertising.traffic_coverage and product.advertising.traffic_coverage.complete
                    ),
                    previous_impressions_known=bool(
                        previous_product
                        and previous_product.advertising.traffic_coverage
                        and previous_product.advertising.traffic_coverage.complete
                    ),
                    drr_max=query.drr_max,
                    ctr_below=query.ctr_below,
                    days=days,
                    fulfillment=query.fulfillment,
                )
                if filter_missing:
                    state, metrics, reasons = "unknown", {}, filter_missing
                if state == "no_match":
                    no_match += 1
                    continue
                value: dict[str, JsonValue] = {
                    "store": store,
                    "marketplace": "OZON",
                    "article": product.article,
                    "name": product.name,
                    "manager": product.manager,
                    "orders_amount": number(item.get("orders_amount")),
                    "status": state,
                    "missing_data": json_value(reasons),
                    **metrics,
                }
                (results if state == "match" else unknown).append(AgentOzonAnalysisRow.model_validate(value))
        except Exception:
            logger.exception("agent_ozon_analysis_failed store=%s", store, extra=log_tags(store))
            failures.append(AgentOzonStoreFailure(store=store, status_code=HTTPStatus.SERVICE_UNAVAILABLE))

    async with asyncio.TaskGroup() as group:
        for store in stores:
            group.create_task(store_analysis(store))
    rows = results + unknown if query.include_unknown else results
    known_rows = sorted(
        (item for item in rows if item.orders_amount is not None),
        key=lambda item: (item.orders_amount or 0, item.article),
        reverse=True,
    )
    ordered = known_rows + [item for item in rows if item.orders_amount is None]
    next_offset = query.offset + query.limit
    return AgentOzonAnalysisResponse(
        scenario=query.scenario,
        marketplace="OZON",
        currency="RUB",
        date_from=query.date_from,
        date_to=query.date_to,
        compare_from=previous_from,
        compare_to=previous_to,
        rows=ordered[query.offset : next_offset],
        total_rows=len(ordered),
        next_offset=next_offset if next_offset < len(ordered) else None,
        totals=AgentOzonAnalysisTotals(
            checked=checked,
            matched=len(results),
            unknown=len(unknown),
            no_match=no_match,
        ),
        unassessed_sample=unknown[: AGENT_OZON.analysis_sample_limit],
        failed_stores=failures,
        complete=bool(checked) and not failures and not unknown,
        warnings=(
            "Заказы не равны выкупам. Остатки текущие, без транзита. Неполные источники не заменяются нулями.",
        ),
    )
