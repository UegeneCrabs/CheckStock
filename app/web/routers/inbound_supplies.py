from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app.access.authentication import is_active_user
from app.access.resources import store_marketplace_pairs
from app.core.constants import INBOUND_SCHEDULE, WebErrorCode
from app.dto.inbound_supplies import InboundReport, InboundSyncRequest
from app.dto.stock_pages import StockTarget
from app.infrastructure.inbound_read_repository import InboundReadRepository
from app.jobs.locks import SyncJobBusyError
from app.jobs.tracking import queue_tracked
from app.stock.inbound_supplies import JOB_NAME
from app.web.client_page import client_page
from app.web.dependencies import ContainerDependency

router = APIRouter()


def allowed_targets(request: Request, store: str = "", marketplace: str = "") -> tuple[tuple[str, str], ...]:
    if not is_active_user(request.state.user):
        raise HTTPException(status_code=403, detail=WebErrorCode.FORBIDDEN)
    store = store.strip().lower()
    marketplace = marketplace.strip().upper()
    pairs = store_marketplace_pairs(request.state.user)
    filtered = tuple(
        (slug, mp)
        for slug, mp in pairs
        if (not store or slug == store) and (not marketplace or mp == marketplace)
    )
    if not filtered:
        raise HTTPException(status_code=403, detail=WebErrorCode.FORBIDDEN)
    return filtered


@router.get("/stock/inbound", response_class=HTMLResponse)
async def inbound_page(request: Request, store: str = "", mp: str = ""):
    allowed_targets(request, store, mp)
    return client_page("stock/inbound-supplies.html")


@router.get("/stock/inbound/data", response_model=InboundReport)
async def inbound_data(request: Request, container: ContainerDependency, store: str = "", mp: str = ""):
    targets = tuple(
        StockTarget(store_slug=slug, marketplace=marketplace)
        for slug, marketplace in allowed_targets(request, store, mp)
    )
    snapshots = await InboundReadRepository(request.app.state.async_resources.database).snapshots(targets)
    cutoff = datetime.now(UTC) - timedelta(seconds=INBOUND_SCHEDULE.stale_after_seconds)
    return InboundReport(targets=snapshots, stale_before=cutoff.isoformat())


@router.post("/stock/inbound/sync")
async def inbound_sync(
    request: Request,
    payload: InboundSyncRequest,
    container: ContainerDependency,
):
    targets = allowed_targets(request, payload.store, payload.marketplace)
    try:
        run_id = await run_in_threadpool(
            queue_tracked,
            JOB_NAME,
            lambda: container.inbound_supplies.sync(targets, recover_interrupted=True),
        )
    except SyncJobBusyError:
        return JSONResponse(
            {"ok": False, "error": "Выгрузка поставок уже выполняется. Дождитесь завершения."},
            status_code=409,
        )
    return JSONResponse(
        {"ok": True, "started": len(targets), "job_id": run_id},
        status_code=202,
        headers={"Cache-Control": "no-store"},
    )
