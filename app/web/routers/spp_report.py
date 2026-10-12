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


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(MOSCOW_TIMEZONE).date()
    date_to = date_to or today
    date_from = date_from or date_to - timedelta(days=6)
    if date_from > date_to or date_to > today or date_from < date(2000, 1, 1):
        raise HTTPException(422, "Выберите корректный период до сегодняшнего дня")
    if (date_to - date_from).days >= 366:
        raise HTTPException(422, "Максимальный период отчёта — 366 дней")
    return date_from, date_to


@router.get("/sales/unit-economics-1c/reports/spp", response_class=HTMLResponse)
async def page(request: Request):
    stores = _stores(request)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    config = {
        "dateFrom": today.isoformat(),
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
    tolerance: float = Query(default=0, ge=0, allow_inf_nan=False),
    page: int = Query(default=1, ge=1),
    sort_by: spp_report.SppSortField = spp_report.SppSortField.TURNOVER,
    sort_order: spp_report.SppSortOrder = spp_report.SppSortOrder.DESC,
):
    stores = _stores(request)
    date_from, date_to = _period(date_from, date_to)
    if store and store not in stores:
        raise HTTPException(403, "Нет доступа к кабинету")
    result = await run_in_threadpool(
        spp_report.load,
        stores,
        date_from,
        date_to,
        spp_report.SppFilters(store, brand, manager, subject, article, search.strip()),
        tolerance=tolerance,
        manager_scope=None,
        page=page,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return asdict(result)


@router.get("/api/unit-economics-1c/reports/spp/history")
async def history(
    request: Request,
    store: str,
    article: str,
    period: spp_report.SppHistoryPeriod | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    stores = _stores(request)
    if period is not None and (date_from is not None or date_to is not None):
        raise HTTPException(422, "Укажите период графика или даты")
    if date_from is not None or date_to is not None:
        if date_from is None or date_to is None:
            raise HTTPException(422, "Укажите обе даты")
        date_from, date_to = _period(date_from, date_to)
    else:
        date_from, date_to = spp_report.history_dates(
            period or spp_report.SppHistoryPeriod.DAYS_7,
            datetime.now(MOSCOW_TIMEZONE).date(),
        )
    if store not in stores:
        raise HTTPException(403, "Нет доступа к кабинету")

    def authorized_history() -> tuple[spp_report.SppDailyPoint, ...] | None:
        for product in spp_report._catalog((store,)):
            if product["article"] != article:
                continue
            return spp_report.daily_history(store, article, date_from, date_to)
        return None

    points = await run_in_threadpool(authorized_history)
    if points is None:
        raise HTTPException(404, "Товар не найден")
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": [asdict(point) for point in points],
    }
