from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app.access.sections import access_level
from app.config import settings
from app.dto.identity import SectionAccessLevel, SectionName
from app.jobs.locks import SyncJobBusyError
from app.jobs.tracking import queue_tracked
from app.stock import supply_arrivals
from app.web.templating import fill_template, render_page

router = APIRouter()


@router.get("/stock/arrivals", response_class=HTMLResponse, include_in_schema=False)
@router.get("/supply-schedule", response_class=HTMLResponse)
async def arrivals_page(request: Request):
    content = fill_template(
        "stock/supply-arrivals.html",
        refresh_hidden=""
        if access_level(request.state.user, SectionName.STOCK_ARRIVALS) is SectionAccessLevel.WRITE
        else " hidden",
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


@router.get("/stock/arrivals/data", include_in_schema=False)
@router.get("/supply-schedule/data")
async def arrivals_data(request: Request):
    result = await run_in_threadpool(supply_arrivals.report, request.state.user)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/stock/arrivals/sync", include_in_schema=False)
@router.post("/supply-schedule/sync")
async def arrivals_sync():
    try:
        run_id = await run_in_threadpool(queue_tracked, supply_arrivals.JOB_NAME, supply_arrivals.sync)
    except SyncJobBusyError:
        return JSONResponse({"ok": True, "status": "running"}, status_code=202)
    return JSONResponse({"ok": True, "status": "queued", "run_id": run_id}, status_code=202)
