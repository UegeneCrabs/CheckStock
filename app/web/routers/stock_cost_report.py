from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.access.authentication import is_active_user
from app.access.resources import store_marketplace_pairs
from app.core.constants import WEB, WebErrorCode
from app.core.domain import MARKETPLACES, MOSCOW_TIMEZONE
from app.dto.integrations import WebFailure
from app.dto.stock_pages import StockClassificationCommand
from app.infrastructure.stock_classification_repository import StockClassificationRepository
from app.stock import cost_report as stock_cost_report
from app.stock import cost_report_export as stock_cost_report_export
from app.web.access import accessible_store_slugs
from app.web.client_page import client_page
from app.web.common import _now_iso
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


@router.post("/stock/cost-report/operations/{operation_id}/fbs-transfer")
async def classify_shipment_as_fbs_transfer(
    request: Request,
    operation_id: int,
    is_fbs_transfer: int = Form(0),
    date_from: str = Form(""),
    date_to: str = Form(""),
    store: str = Form(""),
    marketplace: str = Form(""),
    view: str = Form("shipments"),
):
    scope = stock_scope(request)
    if is_fbs_transfer not in (0, 1):
        return JSONResponse(
            WebFailure(code=WebErrorCode.INVALID_TARGET).model_dump(mode="json"), status_code=400
        )
    command = StockClassificationCommand(
        operation_id=operation_id,
        is_fbs_transfer=bool(is_fbs_transfer),
        user_id=request.state.user.id,
        updated_at=_now_iso(),
    )
    try:
        result = await StockClassificationRepository(request.app.state.async_resources.database).classify(
            scope, command
        )
    except ValueError as error:
        code = WebErrorCode(str(error))
        return JSONResponse(
            WebFailure(code=code).model_dump(mode="json"),
            status_code=404 if code == WebErrorCode.NOT_FOUND else 400,
        )
    if request.headers.get("X-Requested-With") == "fetch":
        return result
    return RedirectResponse(
        "/stock/cost-report?"
        + urlencode(
            {
                "date_from": date_from,
                "date_to": date_to,
                "store": store,
                "marketplace": marketplace,
                "view": view,
            }
        ),
        status_code=303,
    )
