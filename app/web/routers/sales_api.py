"""A separate Analytics page for saved Wildberries funnel orders."""

import json
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from app.access.access_control import accessible_stores
from app.access.sections import has_access
from app.analytics import sales_api
from app.core.domain import MOSCOW_TIMEZONE
from app.dto.identity import SectionName
from app.web.templating import fill_template, render_page

router = APIRouter()


def authorize(request):
    if not has_access(request.state.user, SectionName.ANALYTICS_SALES_API):
        raise HTTPException(403, "Нет доступа к разделу «Продажи по API»")
    return accessible_stores(request.state.user, "WB")


@router.get("/analytics/sales-api", response_class=HTMLResponse)
async def sales_api_page(request: Request):
    authorize(request)
    content = fill_template(
        "analytics/sales-api.html",
        sales_api_config=json.dumps(
            {"today": datetime.now(MOSCOW_TIMEZONE).date().isoformat(), "maxDays": sales_api.MAX_DAYS}
        ),
    )
    return render_page(
        "CheckStock — Продажи по API",
        "analytics_sales_api",
        content,
        request.state.user,
        content_class="content--sales-api",
    )


@router.get("/api/analytics/sales-api")
async def sales_api_data(request: Request, date_from: date | None = None, date_to: date | None = None):
    stores = authorize(request)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    if (date_from is None) != (date_to is None):
        raise HTTPException(422, "Укажите начало и конец периода")
    if date_from is not None:
        if date_from > date_to or date_from < date(2000, 1, 1) or date_to > today:
            raise HTTPException(422, "Выберите корректный период до сегодняшнего дня включительно")
        if (date_to - date_from).days >= sales_api.MAX_DAYS:
            raise HTTPException(422, f"Период не должен превышать {sales_api.MAX_DAYS} дней")
    return await run_in_threadpool(
        sales_api.load, stores, request.state.user, date_from, date_to, today=today
    )
