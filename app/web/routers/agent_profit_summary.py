"""Compact store summaries from the website report, before product pagination."""

import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app import db
from app.agents.product_analysis import covered_articles, number
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.identity import SectionName
from app.web.routers.agent_analytics import Employee
from app.web.routers.unit_economics import (
    _unit_economics_1c_unit_profit_report_data,
    _unit_profit_report_totals,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class SummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    date_from: date
    date_to: date
    compare_from: date | None = None
    compare_to: date | None = None
    store: str | None = Field(default=None, min_length=1, max_length=100)
    manager: str | None = Field(default=None, min_length=1, max_length=100)


async def order_coverage(store, start, end):
    raw = await run_in_threadpool(
        db.get_unit_economics_1c_funnel_daily_order_rows, (store,), str(start), str(end)
    )
    covered = covered_articles(raw, start, end, ("orders_amount",))
    # The website also accepts a saved aggregate for this exact period.
    aggregates = await run_in_threadpool(db.get_unit_economics_1c_funnel_product_metrics, (store,))
    for row in aggregates:
        if str(row.get("period_from")) == str(start) and str(row.get("period_to")) == str(end):
            article = str(row.get("article"))
            if number(row.get("orders_amount")) is not None:
                covered.add(article)
            else:
                covered.discard(article)
    return covered


def summarize(data, covered):
    rows = data["rows"]
    known = [
        r for r in rows if str(r.get("article")) in covered and number(r.get("orders_amount")) is not None
    ]
    eligible = [r for r in known if r["orders_amount"] > 0]
    calculated = [
        r for r in eligible if r.get("margin_complete") is True and number(r.get("margin")) is not None
    ]
    unknown_orders = len(rows) - len(known)
    incomplete = len(eligible) - len(calculated)
    orders_complete = bool(rows) and not unknown_orders
    complete = orders_complete and not incomplete
    totals = _unit_profit_report_totals(eligible)
    # A zero is justified by an observed sum, or a fully known empty selection.
    margin = (
        round(sum(r["margin"] for r in calculated), 2)
        if calculated
        else (0.0 if orders_complete and not eligible else None)
    )
    status = "complete" if complete else ("partial" if known else "missing")
    return {
        "products_count": len(rows),
        "orders_complete": orders_complete,
        "data_status": status,
        "data_message": {
            "complete": None,
            "partial": None,
            "missing": "Нет подтверждённых данных за выбранный период. Отсутствующие значения не являются нулями.",
        }[status],
        "products_with_orders": len(eligible),
        "orders_included_products": len(known),
        "unknown_orders_products": unknown_orders,
        "margin_included_products": len(calculated),
        "margin_excluded_products": unknown_orders + incomplete,
        "incomplete_margin_products": incomplete,
        "orders_amount": round(sum(r["orders_amount"] for r in known), 2) if known else None,
        "website_margin": data["totals"].get("margin")
        if orders_complete and data["totals"].get("margin_complete") is True
        else None,
        "website_margin_complete": orders_complete and data["totals"].get("margin_complete") is True,
        "ordered_products_report_margin": margin,
        "ordered_products_margin": margin,
        "margin_complete": complete,
        "margin_missing_days": totals["margin_missing_days"],
        "manager_scope": data.get("manager_scope"),
    }


@router.get(
    "/profit-summary",
    operation_id="getAnalyticsProfitSummary",
    description="Store totals from website Unit Profit, no product pagination. Margin for products with positive order turnover; disclose incomplete values. Compare equal periods; default preceding period. Omit store for all accessible WB stores.",
)
async def profit_summary(request: Request, user: Employee, query: Annotated[SummaryQuery, Query()]):
    from app.web.routers.agent_full import guard, permitted

    if (query.compare_from is None) != (query.compare_to is None):
        raise HTTPException(422, "Укажите обе даты сравнения")
    days = (query.date_to - query.date_from).days + 1
    compare_to = query.compare_to or query.date_from - timedelta(days=1)
    compare_from = query.compare_from or compare_to - timedelta(days=days - 1)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    if (
        not 1 <= days <= 90
        or (compare_to - compare_from).days + 1 != days
        or max(query.date_to, compare_to) > today
    ):
        raise HTTPException(422, "Нужны равные периоды 1–90 дней без будущих дат")
    stores = (
        [query.store.lower()]
        if query.store
        else [s for s in STORES if permitted(user, SectionName.REPORT_UNIT_PROFIT, s, "WB")]
    )
    for store in stores:
        if store not in STORES:
            raise HTTPException(422, "Неизвестный магазин")
        guard(user, SectionName.REPORT_UNIT_PROFIT, SimpleNamespace(store=store, marketplace="WB"))
    result = []
    for store in stores:
        item = {"store": store, "name": STORES[store]["name"]}
        for label, start, end in (
            ("current", query.date_from, query.date_to),
            ("previous", compare_from, compare_to),
        ):
            params = {"store": store, "date_from": str(start), "date_to": str(end)}
            if query.manager:
                params["manager"] = query.manager
            scoped = Request({**request.scope, "query_string": urlencode(params).encode()})
            try:
                data = await _unit_economics_1c_unit_profit_report_data(scoped)
                if isinstance(data, JSONResponse):
                    item[label] = {"available": False, "status_code": data.status_code}
                else:
                    coverage = await order_coverage(store, start, end)
                    item[label] = {"available": True, **summarize(data, coverage)}
            except Exception:
                logger.exception("agent_profit_summary_failed store=%s period=%s", store, label)
                item[label] = {"available": False, "status_code": 503}
        current = item["current"].get("orders_amount")
        previous = item["previous"].get("orders_amount")
        item["turnover_change_rub"] = (
            round(current - previous, 2) if current is not None and previous is not None else None
        )
        item["turnover_change_percent"] = (
            round((current - previous) / previous * 100, 2)
            if current is not None and previous is not None and previous > 0
            else None
        )
        item["turnover_comparison_partial"] = (
            not (item["current"].get("orders_complete") and item["previous"].get("orders_complete"))
            if current is not None and previous is not None
            else None
        )
        result.append(item)
    result.sort(
        key=lambda r: (r["current"].get("orders_amount") is None, -(r["current"].get("orders_amount") or 0))
    )
    return {
        "marketplace": "WB",
        "currency": "RUB",
        "source": "website_unit_profit_report",
        "date_from": query.date_from,
        "date_to": query.date_to,
        "compare_from": compare_from,
        "compare_to": compare_to,
        "rows": result,
        "next_offset": None,
        "warnings": [
            "Итоги по всей выборке отчёта, не по странице. ТО по заказам; сумма выкупов может быть меньше.",
            "Маржа расчётная по методике сайта, не подтверждённая финансовая прибыль. ",
            "Показывай суммы и прирост без фраз «неполное сравнение», «неполный итог» и предупреждающих значков из-за пропусков. Маржа — по товарам с расчётами. Счётчики исключений и признаки partial выводи только по запросу. Нет данных — не ноль; при нулевой базе процент неизвестен.",
            *(
                ["Текущий день не завершён; сравнение не является сравнением на одинаковый час."]
                if today in (query.date_to, compare_to)
                else []
            ),
        ],
    }
