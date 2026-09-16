"""Server-side product joins and filtering for common analyst questions."""

import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app.agents.product_analysis import covered_articles, evaluate, manager_key, number
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.identity import SectionName
from app.web.routers.agent_analytics import Employee

router = APIRouter()
logger = logging.getLogger(__name__)
SCENARIOS = (
    "stock_without_orders",
    "turnover_drop",
    "drr_negative_roi",
    "stock_impressions_drop",
    "active_low_ctr",
    "goal_gap",
)


class AnalysisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scenario: Literal[
        "stock_without_orders",
        "turnover_drop",
        "drr_negative_roi",
        "stock_impressions_drop",
        "active_low_ctr",
        "goal_gap",
    ]
    date_from: date
    date_to: date
    compare_from: date | None = None
    compare_to: date | None = None
    store: str | None = Field(default=None, min_length=1, max_length=100)
    manager: str | None = Field(default=None, min_length=1, max_length=100)
    code: str | None = Field(default=None, min_length=1, max_length=100)
    is_new: bool | None = None
    fulfillment: str | None = Field(default=None, min_length=1, max_length=100)
    drr_max: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)
    ctr_below: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    include_unknown: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


def required_sections(query):
    result = {SectionName.REPORT_UNIT_PROFIT}
    if query.scenario.startswith("stock_"):
        result.add(SectionName.STOCK_BALANCES)
    if query.scenario in {"active_low_ctr", "goal_gap"} or query.code or query.is_new is not None:
        result.add(SectionName.UNIT_ECONOMICS_WB)
    return result


def periods(query):
    days = (query.date_to - query.date_from).days + 1
    today = datetime.now(MOSCOW_TIMEZONE).date()
    if not 1 <= days <= 90 or query.date_to > today:
        raise HTTPException(422, "Период 1–90 дней, без будущих дат")
    compare = query.scenario in {"turnover_drop", "stock_impressions_drop"}
    if not compare and (query.compare_from or query.compare_to):
        raise HTTPException(422, "Сравнение дат не применяется к этому сценарию")
    if (query.compare_from is None) != (query.compare_to is None):
        raise HTTPException(422, "Укажите обе даты сравнения")
    previous_from = previous_to = None
    if compare:
        try:
            shift = 7 if query.scenario == "turnover_drop" else days
            previous_from = query.compare_from or query.date_from - timedelta(days=shift)
            previous_to = query.compare_to or query.date_to - timedelta(days=shift)
        except OverflowError as error:
            raise HTTPException(422, "Некорректные даты сравнения") from error
        if (previous_to - previous_from).days + 1 != days or previous_to > today:
            raise HTTPException(422, "Нужны равные периоды без будущих дат")
    if query.scenario == "goal_gap" and query.date_from < today - timedelta(days=today.weekday()):
        raise HTTPException(422, "В ТЕГе текущая цель: проверка доступна только за дни текущей недели ПН–ВС")
    if query.fulfillment and not query.scenario.startswith("stock_"):
        raise HTTPException(422, "Фулфилмент применяется только к проверкам стока")
    return previous_from, previous_to, days, today


async def load_source(name, request, user, store, start=None, end=None, **extra):
    from app.web.routers.agent_full import ReportQuery, execute

    params = {"store": store, **extra}
    if start is not None:
        params.update(date_from=str(start), date_to=str(end))
    scoped = Request({**request.scope, "query_string": urlencode(params).encode()})
    result = await execute(name, scoped, user, ReportQuery(**params), paginate=False)
    if isinstance(result, JSONResponse):
        raise HTTPException(result.status_code, "Источник недоступен")
    return result


async def load_store(request, user, store, query, previous_from, previous_to):
    data = {}
    data["profit"] = await load_source("profit", request, user, store, query.date_from, query.date_to)
    if previous_from:
        data["previous"] = await load_source("profit", request, user, store, previous_from, previous_to)
    if query.scenario.startswith("stock_"):
        extra = {"view": "summary"}
        if query.fulfillment:
            extra["fulfillment"] = query.fulfillment
        data["stocks"] = await load_source("stocks", request, user, store, **extra)
    if query.scenario == "goal_gap" or query.code:
        data["tags"] = await load_source("product-tags", request, user, store)
    if query.is_new is not None:
        data["newness"] = await load_source("product-newness", request, user, store)
    if query.scenario == "active_low_ctr":
        data["campaigns"] = await load_source(
            "advertising-campaigns", request, user, store, query.date_from, query.date_to
        )
    if query.scenario in {"stock_without_orders", "turnover_drop", "goal_gap", "stock_impressions_drop"}:
        advertising = query.scenario == "stock_impressions_drop"
        loader = (
            db.get_unit_economics_1c_daily_advertising
            if advertising
            else db.get_unit_economics_1c_funnel_daily_order_rows
        )
        fields = ("impressions",) if advertising else ("orders_count", "orders_amount")
        for label, start, end in (
            ("current_coverage", query.date_from, query.date_to),
            ("previous_coverage", previous_from, previous_to),
        ):
            if start:
                raw = await run_in_threadpool(loader, (store,), str(start), str(end))
                data[label] = covered_articles(raw, start, end, fields)
    return data


def indexed(data, name):
    return {str(r["article"]): r for r in data.get(name, {}).get("rows", [])}


@router.get(
    "/product-analysis",
    operation_id="getAnalyticsProductAnalysis",
    description="Server-side stock/order, turnover, DRR/ROI, ad impressions, active campaign CTR and current-week goal checks. No client-side joins. Omit store for all allowed WB stores. Sort by order turnover; unknown data is counted separately.",
)
async def product_analysis(request: Request, user: Employee, query: Annotated[AnalysisQuery, Query()]):
    from app.web.routers.agent_full import guard, permitted

    previous_from, previous_to, days, today = periods(query)
    sections = required_sections(query)
    allowed = [s for s in STORES if all(permitted(user, section, s, "WB") for section in sections)]
    stores = [query.store.lower()] if query.store else allowed
    for store in stores:
        if store not in STORES:
            raise HTTPException(422, "Неизвестный магазин")
        for section in sections:
            guard(user, section, SimpleNamespace(store=store, marketplace="WB"))
    snapshots, failures = {}, []
    for store in stores:
        try:
            snapshots[store] = await load_store(request, user, store, query, previous_from, previous_to)
        except Exception as error:
            logger.error(
                "agent_analysis_source_failed store=%s scenario=%s type=%s",
                store,
                query.scenario,
                type(error).__name__,
            )
            failures.append(
                {
                    "store": store,
                    "status_code": error.status_code if isinstance(error, HTTPException) else 503,
                }
            )
    names = {
        str(r.get("manager") or "").strip()
        for d in snapshots.values()
        for r in d["profit"]["rows"]
        if r.get("manager")
    }
    resolved_manager = None
    if query.manager:
        requested = set(manager_key(query.manager))
        matches = {manager_key(n) for n in names if requested <= set(manager_key(n))}
        if len(matches) > 1:
            raise HTTPException(
                422,
                {
                    "reason": "ambiguous_manager",
                    "matches": sorted(n for n in names if manager_key(n) in matches),
                },
            )
        resolved_manager = next(iter(matches), ())
    results, unknown, checked, no_match = [], [], 0, 0
    source_context = {}
    for store, data in snapshots.items():
        base, previous, stocks, tags, newness = (
            indexed(data, key) for key in ("profit", "previous", "stocks", "tags", "newness")
        )
        campaigns = {}
        for row in data.get("campaigns", {}).get("rows", []):
            campaigns.setdefault(str(row["article"]), []).append(row)
        campaign_available = data.get("campaigns", {}).get("context", {}).get("available") is True
        articles = set(base)
        if query.scenario.startswith("stock_"):
            articles |= set(stocks)
        if query.scenario == "active_low_ctr":
            articles |= set(campaigns)
        source_context[store] = {
            k: {"context": v.get("context", {}), "warnings": v.get("warnings", [])}
            for k, v in data.items()
            if isinstance(v, dict)
        }
        for article in articles:
            product = base.get(article, {})
            if query.manager and (
                not resolved_manager or manager_key(product.get("manager")) != resolved_manager
            ):
                continue
            filter_unknown = []
            if query.code:
                code = tags.get(article, {}).get("code")
                if code is None:
                    filter_unknown.append("tag_code_unknown")
                elif str(code).casefold() != query.code.casefold():
                    continue
            if query.is_new is not None:
                value = newness.get(article, {}).get("is_new")
                if value is None:
                    filter_unknown.append("newness_unknown")
                elif value is not query.is_new:
                    continue
            checked += 1
            if filter_unknown:
                state, metrics, reasons = "unknown", {}, filter_unknown
            else:
                state, metrics, reasons = evaluate(
                    query.scenario,
                    product,
                    previous.get(article),
                    stocks.get(article),
                    tags.get(article),
                    campaigns.get(article, []) if campaign_available else None,
                    orders_known=article in data.get("current_coverage", set()),
                    previous_orders_known=article in data.get("previous_coverage", set()),
                    impressions_known=article in data.get("current_coverage", set()),
                    previous_impressions_known=article in data.get("previous_coverage", set()),
                    drr_max=query.drr_max,
                    ctr_below=query.ctr_below,
                    days=days,
                    fulfillment=query.fulfillment,
                )
            if state == "no_match":
                no_match += 1
                continue
            row = {
                "store": store,
                "marketplace": "WB",
                "article": article,
                "name": product.get("name")
                or stocks.get(article, {}).get("name")
                or tags.get(article, {}).get("name")
                or newness.get(article, {}).get("name")
                or next((r.get("name") for r in campaigns.get(article, []) if r.get("name")), None)
                or "Название не найдено",
                "manager": product.get("manager"),
                "orders_amount": number(product.get("orders_amount")),
                "status": state,
                "missing_data": reasons,
                **metrics,
            }
            (results if state == "match" else unknown).append(row)

    def key(r):
        return (r["orders_amount"] is None, -(r["orders_amount"] or 0), r["store"], r["article"])

    results.sort(key=key)
    unknown.sort(key=key)
    output = sorted(results + unknown, key=key) if query.include_unknown else results
    total = len(output)
    return {
        "scenario": query.scenario,
        "marketplace": "WB",
        "currency": "RUB",
        "date_from": query.date_from,
        "date_to": query.date_to,
        "compare_from": previous_from,
        "compare_to": previous_to,
        "generated_at": datetime.now(MOSCOW_TIMEZONE).isoformat(),
        "rows": output[query.offset : query.offset + query.limit],
        "total_rows": total,
        "next_offset": query.offset + query.limit if query.offset + query.limit < total else None,
        "totals": {
            "checked": checked,
            "matched": len(results),
            "unknown": len(unknown),
            "no_match": no_match,
            "matched_orders_amount": sum(r["orders_amount"] for r in results)
            if results and all(r["orders_amount"] is not None for r in results)
            else None,
        },
        "unassessed_sample": unknown[:10],
        "failed_stores": failures,
        "complete": not failures and not unknown and bool(snapshots) and checked > 0,
        "manager_resolution": {"requested": query.manager, "matched": bool(resolved_manager)}
        if query.manager
        else None,
        "sources": source_context,
        "warnings": [
            "Отбор, соединение и итоги выполнены на сервере по всем строкам. Страницы содержат только результат.",
            "ТО — заказы, не выкупы. Показы — рекламные. Остатки — наблюдаемые снимки без транзита, не реальное время.",
            "Пропуски не равны нулю. include_unknown=true включает все непроверенные товары в страницы; unassessed_sample — только пример.",
            *(
                [
                    "Текущий день не завершён; сравнение полных дней с ним не является сравнением на одинаковый час."
                ]
                if query.date_to == today
                else []
            ),
            *(
                [
                    "Цель из текущего ТЕГа: неделя ПН–ВС, дневная цель равна недельной / 7. Это текущее отставание от равномерного плана, не прогноз провала недели."
                ]
                if query.scenario == "goal_gap"
                else []
            ),
        ],
    }
