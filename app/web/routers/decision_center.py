import html
import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app import decision_center as decision_service
from app.dto.decision import DecisionStatusRequest, DecisionSyncRequest
from app.web.access import accessible_store_items, accessible_store_slugs, has_store_access
from app.web.dependencies import DecisionCommandServiceDependency
from app.web.templating import fill_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/sales/decision-center", response_class=HTMLResponse)
async def sales_decision_center(request: Request):
    accessible = accessible_store_items(request.state.user).root
    store_options = ['<option value="">Все доступные магазины</option>']
    store_options.extend(
        f'<option value="{html.escape(item.slug, quote=True)}">{html.escape(item.store.name)}</option>'
        for item in accessible
    )
    content = fill_template(
        "decision_center_content.html",
        store_options="".join(store_options),
    )
    return render_page(
        "CheckStock — Центр решений WB",
        "sales_decision",
        content,
        request.state.user,
        content_class="content--decision-center",
    )


@router.get("/api/decision-center")
async def decision_center_data(request: Request, store: str = ""):
    store_slug = store.strip().lower()
    if store_slug and not has_store_access(request.state.user, store_slug):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)
    allowed = [store_slug] if store_slug else accessible_store_slugs(request.state.user)
    try:
        return await run_in_threadpool(decision_service.dashboard, allowed)
    except Exception as exc:
        logger.exception("Центр решений WB не собран")
        return JSONResponse(
            {"ok": False, "error": f"Не удалось собрать решения: {exc}"},
            status_code=500,
        )


@router.post("/api/decision-center/sync")
async def decision_center_sync(request: Request, payload: DecisionSyncRequest):
    store_slug = payload.store.lower()
    if store_slug and not has_store_access(request.state.user, store_slug):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)
    allowed = [store_slug] if store_slug else accessible_store_slugs(request.state.user)
    report = await run_in_threadpool(decision_service.sync_many, allowed, True)
    return {"ok": True, "marketplace": "WB", "stores": report}


@router.post("/api/decision-center/status")
async def decision_center_status(
    request: Request,
    payload: DecisionStatusRequest,
    commands: DecisionCommandServiceDependency,
):
    fingerprint = payload.fingerprint
    store_slug = fingerprint.split(":", 1)[0].lower() if ":" in fingerprint else ""
    if not store_slug or not has_store_access(request.state.user, store_slug):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому решению"}, status_code=403)
    try:
        result = await run_in_threadpool(commands.set_status, payload, request.state.user)
        return {"ok": True, **result.model_dump(mode="json")}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
