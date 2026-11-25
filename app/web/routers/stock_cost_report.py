from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from app.access.authentication import is_active_user
from app.access.resources import store_marketplace_pairs
from app.core.constants import WEB, WebErrorCode
from app.core.domain import MARKETPLACES, MOSCOW_TIMEZONE
from app.dto.stock_pages import StockHistoryPeriod
from app.ff_import.export import build_filtered_history_xlsx
from app.stock import cost_report as stock_cost_report
from app.stock import cost_report_export as stock_cost_report_export
from app.web.access import accessible_store_slugs
from app.web.client_page import client_page
from app.web.downloads import _download_headers
from app.web.stock_scope import stock_scope

router = APIRouter()


def _default_period(today: date | None = None) -> tuple[date, date]:
    current = today or datetime.now(MOSCOW_TIMEZONE).date()
    return current - timedelta(days=current.weekday()), current


def _period(date_from: str, date_to: str) -> tuple[date, date]:
    default_from, default_to = _default_period()
    try:
        start = date.fromisoformat(date_from) if date_from else default_from
        end = date.fromisoformat(date_to) if date_to else default_to
    except ValueError as error:
        raise HTTPException(status_code=400, detail=WebErrorCode.INVALID_PERIOD) from error
    if end < start:
        raise HTTPException(status_code=400, detail=WebErrorCode.INVALID_PERIOD)
    if (end - start).days > WEB.report_max_days:
        raise HTTPException(status_code=400, detail=WebErrorCode.INVALID_PERIOD)
    return start, end


@router.get("/stock/cost-report", response_class=HTMLResponse)
async def stock_cost_report_page(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    store: str = "",
    marketplace: str = "",
    view: str = "summary",
):
    stock_scope(request, store, marketplace)
    return client_page("stock/cost-report.html")


@router.get("/stock/cost-report.xlsx")
async def stock_cost_report_xlsx(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    store: str = "",
    marketplace: str = "",
    view: str = "summary",
):
    if not is_active_user(request.state.user):
        raise HTTPException(status_code=403, detail="Нет доступа к выгрузке отчёта")
    start, end = _period(date_from, date_to)
    allowed_stores = accessible_store_slugs(request.state.user)
    store = store if store in allowed_stores else ""
    store_slugs = (store,) if store else allowed_stores
    allowed_pairs = store_marketplace_pairs(request.state.user)
    allowed_marketplaces = tuple(
        item for item in MARKETPLACES if any(pair[1] == item for pair in allowed_pairs)
    )
    marketplace = marketplace if marketplace in allowed_marketplaces else ""
    marketplaces = (marketplace,) if marketplace else allowed_marketplaces
    view = view if view in stock_cost_report.VIEW_KINDS else "summary"
    report = await run_in_threadpool(
        stock_cost_report.build_report,
        store_slugs,
        start,
        end,
        marketplaces,
        allowed_pairs,
    )
    try:
        content, filename = await run_in_threadpool(stock_cost_report_export.build_xlsx, report, view)
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@router.get("/stock/cost-report/movements.xlsx")
async def stock_movements_xlsx(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    store: str = "",
    marketplace: str = "",
) -> Response:
    scope = stock_scope(request, store, marketplace)
    start, end = _period(date_from, date_to)
    try:
        period = StockHistoryPeriod(
            start=datetime.combine(start, time.min, tzinfo=MOSCOW_TIMEZONE).astimezone(UTC),
            end_exclusive=datetime.combine(
                end + timedelta(days=1), time.min, tzinfo=MOSCOW_TIMEZONE
            ).astimezone(UTC),
        )
    except OverflowError as error:
        raise HTTPException(status_code=400, detail=WebErrorCode.INVALID_PERIOD) from error
    try:
        content, filename = await run_in_threadpool(build_filtered_history_xlsx, scope, period)
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )
