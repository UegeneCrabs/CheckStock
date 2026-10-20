"""WB product rating report page and read-only API."""

import json
from dataclasses import asdict
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app.access.authentication import is_active_user
from app.access.resources import accessible_stores
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.economics.wb import rating_report, spp_report
from app.web.routers.spp_report import _period
from app.web.templating import fill_template, render_page

router = APIRouter()


def _stores(request: Request) -> tuple[str, ...]:
    if not is_active_user(request.state.user):
        raise HTTPException(403, "Нет доступа к отчёту по рейтингу")
    return accessible_stores(request.state.user, "WB")


@router.get("/sales/unit-economics-1c/reports/rating", response_class=HTMLResponse)
async def page(request: Request):
    stores = _stores(request)
    today = datetime.now(MOSCOW_TIMEZONE).date().isoformat()
    config = {
        "dateFrom": today,
        "dateTo": today,
        "stores": [{"slug": store, "name": STORES[store]["name"]} for store in stores],
    }
    content = fill_template(
        "economics/wb/rating-report.html",
        rating_report_config=json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Рейтинг товаров WB",
        "unit_1c_rating_report",
        content,
        request.state.user,
        content_class="content--wb-report",
    )


@router.get("/api/unit-economics-1c/reports/rating")
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
    sort_by: rating_report.RatingSortField = rating_report.RatingSortField.CHANGE,
    sort_order: rating_report.RatingSortOrder = rating_report.RatingSortOrder.ASC,
):
    stores = _stores(request)
    date_from, date_to = _period(date_from, date_to)
    if store and store not in stores:
        raise HTTPException(403, "Нет доступа к кабинету")
    result = await run_in_threadpool(
        rating_report.load,
        stores,
        date_from,
        date_to,
        rating_report.RatingFilters(store, brand, manager, subject, article, search.strip()),
        tolerance=tolerance,
        page=page,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return asdict(result)


@router.get("/api/unit-economics-1c/reports/rating/history")
async def history(
    request: Request,
    store: str,
    nm_id: str,
    period: spp_report.SppHistoryPeriod | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    stores = _stores(request)
    if store not in stores:
        raise HTTPException(403, "Нет доступа к кабинету")
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

    def read_history():
        if not rating_report.belongs_to_catalog(store, nm_id):
            return None
        return rating_report.daily_history(store, nm_id, date_from, date_to)

    days = await run_in_threadpool(read_history)
    if days is None:
        raise HTTPException(404, "Товар не найден")
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "days": [asdict(day) for day in days],
    }
