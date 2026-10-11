"""WB SPP comparison page and read-only API."""

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app.access.authentication import is_active_user
from app.access.resources import accessible_stores
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.economics.wb import spp_report
from app.web.templating import fill_template, render_page

router = APIRouter()


def _stores(request: Request) -> tuple[str, ...]:
    if not is_active_user(request.state.user):
        raise HTTPException(403, "Нет доступа к отчёту по СПП")
    return accessible_stores(request.state.user, "WB")


@router.get("/sales/unit-economics-1c/reports/spp", response_class=HTMLResponse)
async def page(request: Request):
    stores = _stores(request)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    config = {
        "dateFrom": (today - timedelta(days=6)).isoformat(),
        "dateTo": today.isoformat(),
        "stores": [{"slug": store, "name": STORES[store]["name"]} for store in stores],
        "showManager": True,
    }
    content = fill_template(
        "economics/wb/spp-report.html",
        spp_report_config=json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Отчёт по СПП",
        "unit_1c_spp_report",
        content,
        request.state.user,
        content_class="content--spp-report",
    )


@router.get("/api/unit-economics-1c/reports/spp")
async def data(
    request: Request,
    date_from: date | None = None,
    date_to: date | None = None,
    store: str = "",
    brand: str = "",
    manager: str = "",
    subject: str = "",
    article: str = "",
    search: str = "",
    page: int = Query(default=1, ge=1),
    sort_by: spp_report.SppSortField = spp_report.SppSortField.TURNOVER,
    sort_order: spp_report.SppSortOrder = spp_report.SppSortOrder.DESC,
):
    stores = _stores(request)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    date_to = date_to or today
    date_from = date_from or date_to - timedelta(days=6)
    if date_from > date_to or date_to > today or date_from < date(2000, 1, 1):
        raise HTTPException(422, "Выберите корректный период до сегодняшнего дня")
    if (date_to - date_from).days >= 366:
        raise HTTPException(422, "Максимальный период отчёта — 366 дней")
    if store and store not in stores:
        raise HTTPException(403, "Нет доступа к кабинету")
    result = await run_in_threadpool(
        spp_report.load,
        stores,
        date_from,
        date_to,
        spp_report.SppFilters(store, brand, manager, subject, article, search.strip()),
        manager_scope=None,
        page=page,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return asdict(result)
