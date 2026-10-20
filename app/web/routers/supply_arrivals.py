from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app.access.authentication import is_active_user, require_active_user
from app.config import settings
from app.container import supply_arrival_stock_service
from app.dto.supply_arrivals import (
    SupplyArrivalId,
    SupplyArrivalMissingError,
    SupplyArrivalsReport,
    SupplyArrivalsStockReport,
    SupplyStockChange,
    SupplyStockConflictError,
    SupplyStockState,
)
from app.jobs.locks import SyncJobBusyError
from app.jobs.tracking import queue_tracked
from app.stock import supply_arrivals
from app.web.dependencies import AsyncResourcesDependency
from app.web.templating import fill_template, render_page

router = APIRouter()


@router.get("/stock/arrivals", response_class=HTMLResponse, include_in_schema=False)
@router.get("/supply-schedule", response_class=HTMLResponse)
async def arrivals_page(request: Request):
    content = fill_template(
        "stock/supply-arrivals.html",
        refresh_hidden="" if is_active_user(request.state.user) else " hidden",
        interval_minutes=str(settings.supply_arrivals_sync_interval_seconds // 60),
        preference_key=str(request.state.user["id"]),
    )
    return render_page(
        "РАКЕТА — Расписание поставок",
        "stock_arrivals",
        content,
        request.state.user,
        content_class="arrivals-content",
    )


@router.get("/stock/arrivals/data", response_model=SupplyArrivalsStockReport, include_in_schema=False)
@router.get("/supply-schedule/data", response_model=SupplyArrivalsStockReport)
async def arrivals_data(request: Request, resources: AsyncResourcesDependency):
    user = require_active_user(request)
    result = await run_in_threadpool(supply_arrivals.report, request.state.user)
    report = await supply_arrival_stock_service(resources).report(
        SupplyArrivalsReport.model_validate(result), user
    )
    return JSONResponse(report.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


@router.put("/stock/arrivals/{supply_id}/stock", response_model=SupplyStockState, include_in_schema=False)
@router.put("/supply-schedule/{supply_id}/stock", response_model=SupplyStockState)
async def arrivals_stock(
    supply_id: SupplyArrivalId,
    change: SupplyStockChange,
    request: Request,
    resources: AsyncResourcesDependency,
):
    user = require_active_user(request)
    try:
        result = await supply_arrival_stock_service(resources).change(supply_id, change, user)
    except SupplyArrivalMissingError:
        raise HTTPException(404, "supply_not_found") from None
    except SupplyStockConflictError:
        raise HTTPException(409, "supply_stock_conflict") from None
    return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


@router.post("/stock/arrivals/sync", include_in_schema=False)
@router.post("/supply-schedule/sync")
async def arrivals_sync():
    try:
        run_id = await run_in_threadpool(queue_tracked, supply_arrivals.JOB_NAME, supply_arrivals.sync)
    except SyncJobBusyError:
        return JSONResponse({"ok": True, "status": "running"}, status_code=202)
    return JSONResponse({"ok": True, "status": "queued", "run_id": run_id}, status_code=202)
