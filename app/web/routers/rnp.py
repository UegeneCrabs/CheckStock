import html
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app import rnp as rnp_service
from app import sales as sales_service
from app.dto.rnp import RnpActionRequest, RnpStrategyRequest, RnpSyncRequest
from app.web.access import accessible_store_items, first_accessible_store, has_store_access
from app.web.dependencies import RnpCommandServiceDependency
from app.web.templating import fill_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/sales/rnp", response_class=HTMLResponse)
async def sales_rnp(request: Request):
    accessible = accessible_store_items(request.state.user).root
    if not accessible:
        raise HTTPException(status_code=403, detail="Нет доступных магазинов")
    default_store = accessible[0].slug
    store_options = "".join(
        f'<option value="{html.escape(item.slug)}"'
        f"{' selected' if item.slug == default_store else ''}>"
        f"{html.escape(item.store.name)}</option>"
        for item in accessible
    )
    current_month = date.today().strftime("%Y-%m")
    content = fill_template(
        "rnp_content.html",
        default_month=current_month,
        max_month=current_month,
        default_store=html.escape(default_store),
        store_options=store_options,
    )
    return render_page(
        "CheckStock — РНП",
        "sales_rnp",
        content,
        request.state.user,
    )


@router.get("/api/rnp")
async def rnp_data(
    request: Request,
    month: str = "",
    marketplace: str = "WB",
    store: str = "",
    search: str = "",
    limit: int = 25,
    offset: int = 0,
):
    store_slug = store.lower() or first_accessible_store(request.state.user)
    if not store_slug or not has_store_access(request.state.user, store_slug):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)
    try:
        payload = await run_in_threadpool(
            rnp_service.dashboard,
            month,
            marketplace,
            store_slug,
            search,
            limit,
            offset,
        )
        return JSONResponse(payload)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("Не удалось собрать РНП")
        return JSONResponse(
            {"ok": False, "error": "Не удалось прочитать данные РНП из базы"},
            status_code=500,
        )


@router.post("/api/rnp/sync")
async def rnp_sync(request: Request, payload: RnpSyncRequest):
    store_slug = payload.store.lower()
    if not store_slug or not has_store_access(request.state.user, store_slug):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)
    try:
        marketplace = payload.marketplace.value
        sales_lookback = rnp_service.sales_lookback_days(payload.month, marketplace)
        sales_report = await run_in_threadpool(
            sales_service.sync_store,
            store_slug,
            marketplace,
            sales_lookback,
        )
        report = await run_in_threadpool(
            rnp_service.sync_metrics,
            payload.month,
            marketplace,
            store_slug,
            True,
            list(payload.articles) if payload.articles else None,
        )
        return JSONResponse({"ok": True, "sales_sync": sales_report, "metric_sync": report})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("Ручное обновление дополнительных метрик РНП завершилось ошибкой")
        return JSONResponse(
            {"ok": False, "error": f"Не удалось обновить метрики площадки: {exc}"},
            status_code=500,
        )


@router.post("/api/rnp/strategy")
async def rnp_strategy(
    request: Request,
    payload: RnpStrategyRequest,
    commands: RnpCommandServiceDependency,
):
    try:
        store_slug = payload.store.lower()
        if not has_store_access(request.state.user, store_slug):
            return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)
        item = await run_in_threadpool(commands.save_strategy, payload, request.state.user)
        return JSONResponse({"ok": True, "strategy": item.model_dump(mode="json")})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("Не удалось сохранить стратегию РНП")
        return JSONResponse({"ok": False, "error": "Не удалось сохранить стратегию"}, status_code=500)


@router.post("/api/rnp/action")
async def rnp_action(
    request: Request,
    payload: RnpActionRequest,
    commands: RnpCommandServiceDependency,
):
    try:
        store_slug = payload.store.lower()
        if not has_store_access(request.state.user, store_slug):
            return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)
        item = await run_in_threadpool(commands.add_action, payload, request.state.user)
        return JSONResponse({"ok": True, "action": item.model_dump(mode="json")})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("Не удалось сохранить действие РНП")
        return JSONResponse({"ok": False, "error": "Не удалось сохранить действие"}, status_code=500)
