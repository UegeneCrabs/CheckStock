import html
import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app.access_control import ActionPermission, profile_has_permission, scope_pairs
from app.config import settings
from app.dto.identity import SectionAccessLevel, SectionName
from app.dto.inbound_supplies import STAGE_LABELS, InboundSyncRequest
from app.section_access import access_level
from app.stores import STORES
from app.web.dependencies import ContainerDependency
from app.web.templating import fill_template, render_page

router = APIRouter()
MARKETPLACE_LABELS = {"WB": "Wildberries", "OZON": "Ozon", "YANDEX MARKET": "Яндекс Маркет"}


def allowed_targets(request: Request, store: str = "", marketplace: str = "") -> tuple[tuple[str, str], ...]:
    if not profile_has_permission(request.state.user, ActionPermission.STOCK_BALANCE_VIEW):
        raise HTTPException(status_code=403, detail="Нет доступа к остаткам")
    store = store.strip().lower()
    marketplace = marketplace.strip().upper()
    pairs = scope_pairs(request.state.user)
    filtered = tuple(
        (slug, mp)
        for slug, mp in pairs
        if (not store or slug == store) and (not marketplace or mp == marketplace)
    )
    if not filtered:
        raise HTTPException(status_code=403, detail="Нет доступа к выбранному магазину или площадке")
    return filtered


@router.get("/stock/inbound", response_class=HTMLResponse)
async def inbound_page(request: Request, store: str = "", mp: str = ""):
    pairs = allowed_targets(request)
    allowed_targets(request, store, mp)
    stores = tuple(dict.fromkeys(slug for slug, _ in pairs))
    marketplaces = tuple(dict.fromkeys(marketplace for _, marketplace in pairs))

    def options(values, selected):
        return "".join(
            f'<option value="{html.escape(value, quote=True)}"{" selected" if value == selected else ""}>'
            f"{html.escape(label)}</option>"
            for value, label in values
        )

    content = fill_template(
        "inbound_supplies_content.html",
        store_options=options(((slug, STORES[slug].name) for slug in stores), store.strip().lower()),
        marketplace_options=options(
            ((value, MARKETPLACE_LABELS[value]) for value in marketplaces), mp.strip().upper()
        ),
        scopes=html.escape(json.dumps(pairs), quote=True),
        refresh_hidden=""
        if access_level(request.state.user, SectionName.STOCK_INBOUND) is SectionAccessLevel.WRITE
        else " hidden",
        interval_minutes=str(settings.inbound_sync_interval_seconds // 60),
    )
    return render_page("РАКЕТА — Поставки FBO", "stock_inbound", content, request.state.user)


@router.get("/stock/inbound/data")
async def inbound_data(request: Request, container: ContainerDependency, store: str = "", mp: str = ""):
    targets = allowed_targets(request, store, mp)
    snapshots = await run_in_threadpool(container.inbound_supplies.report, targets)
    cutoff = datetime.now(UTC) - timedelta(seconds=max(settings.inbound_sync_interval_seconds * 2, 3600))
    result = []
    for snapshot in snapshots:
        stale = not snapshot.last_success or datetime.fromisoformat(snapshot.last_success) < cutoff
        result.append(
            {
                **snapshot.model_dump(mode="json"),
                "store_name": STORES[snapshot.store_slug].name,
                "marketplace_name": MARKETPLACE_LABELS[snapshot.marketplace],
                "stale": stale,
            }
        )
    return JSONResponse(
        {"ok": True, "targets": result, "stages": STAGE_LABELS}, headers={"Cache-Control": "no-store"}
    )


@router.post("/stock/inbound/sync")
async def inbound_sync(
    request: Request,
    payload: InboundSyncRequest,
    background_tasks: BackgroundTasks,
    container: ContainerDependency,
):
    targets = allowed_targets(request, payload.store, payload.marketplace)
    claimed = await run_in_threadpool(container.inbound_supplies.claim, targets)
    if claimed:
        background_tasks.add_task(container.inbound_supplies.sync_claimed, claimed)
    return JSONResponse(
        {"ok": True, "started": len(claimed)}, status_code=202, headers={"Cache-Control": "no-store"}
    )
