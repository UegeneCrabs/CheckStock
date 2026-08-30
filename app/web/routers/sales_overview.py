import html
import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import sales as sales_service
from app.access_control import ActionPermission, accessible_stores, has_action_permission
from app.domain import MARKETPLACES
from app.dto.identity import SectionName
from app.section_access import has_access as has_section_access
from app.section_access import landing_path
from app.stores import STORES
from app.sync_tracking import run_tracked
from app.wb import api as wb_api
from app.wb import funnel_orders as wb_funnel_orders
from app.web.access import (
    accessible_marketplaces,
    accessible_store_items,
)
from app.web.routers.sales_common import render_sales_placeholder
from app.web.templating import fill_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse(landing_path(request.state.user), status_code=303)


@router.get("/sales", response_class=HTMLResponse)
async def sales(request: Request):
    today = date.today()
    accessible = accessible_store_items(request.state.user).root
    can_see_all = len(accessible) == len(STORES)
    store_options = ['<option value="">Все магазины</option>'] if can_see_all else []
    store_options.extend(
        f'<option value="{html.escape(item.slug)}">{html.escape(item.store.name)}</option>'
        for item in accessible
    )
    marketplace_labels = {"WB": "Wildberries", "OZON": "Ozon", "YANDEX MARKET": "Яндекс Маркет"}
    allowed_marketplaces = accessible_marketplaces(request.state.user)
    marketplace_options = "".join(
        f'<option value="{html.escape(marketplace)}"{" selected" if index == 0 else ""}>'
        f"{html.escape(marketplace_labels[marketplace])}</option>"
        for index, marketplace in enumerate(allowed_marketplaces)
    )
    content = fill_template(
        "sales_content.html",
        default_from=(today - timedelta(days=29)).isoformat(),
        default_to=today.isoformat(),
        date_max=today.isoformat(),
        sales_store_options="".join(store_options),
        sales_marketplace_options=marketplace_options,
        ephemerides_hidden=""
        if has_section_access(request.state.user, SectionName.EPHEMERIDES)
        else " hidden",
    )
    return render_page(
        "CheckStock — Продажи",
        "sales",
        content,
        request.state.user,
    )


@router.get("/api/sales")
async def sales_data(
    request: Request, date_from: str = "", date_to: str = "", marketplace: str = "WB", store: str = ""
):
    marketplace = marketplace.strip().upper()
    if marketplace not in MARKETPLACES:
        return JSONResponse({"ok": False, "error": "Неизвестный маркетплейс"}, status_code=400)
    store_slug = store.lower() or None
    scoped_stores = accessible_stores(request.state.user, marketplace)
    if store_slug and not has_action_permission(
        request.state.user,
        ActionPermission.SALES_VIEW,
        store_slug=store_slug,
        marketplace=marketplace,
    ):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину и площадке"}, status_code=403)
    if store_slug is None and len(scoped_stores) != len(STORES):
        store_slug = scoped_stores[0] if scoped_stores else None
    if store_slug is None and not scoped_stores:
        return JSONResponse({"ok": False, "error": "Нет доступа к этой площадке"}, status_code=403)
    try:
        payload = await run_in_threadpool(
            sales_service.dashboard,
            date_from,
            date_to,
            marketplace,
            store_slug,
        )
        return JSONResponse(payload)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception:
        logger.exception("Не удалось собрать данные вкладки продаж")
        return JSONResponse(
            {"ok": False, "error": "Не удалось прочитать данные продаж из базы"},
            status_code=500,
        )


@router.get("/api/sales/wb-funnel-orders")
async def wb_funnel_orders_data(
    request: Request, date_from: str = "", date_to: str = "", store: str = "", refresh: bool = False
):
    store_slug = store.strip().lower() or None
    scoped_stores = accessible_stores(request.state.user, "WB")
    if store_slug and not has_action_permission(
        request.state.user,
        ActionPermission.SALES_VIEW,
        store_slug=store_slug,
        marketplace="WB",
    ):
        return JSONResponse({"ok": False, "error": "Нет доступа к WB этого магазина"}, status_code=403)
    if store_slug is None and len(scoped_stores) != len(STORES):
        store_slug = scoped_stores[0] if scoped_stores else None
    if store_slug is None and not scoped_stores:
        return JSONResponse({"ok": False, "error": "Нет доступа к WB"}, status_code=403)
    try:
        if refresh:
            if store_slug:
                await run_in_threadpool(
                    run_tracked,
                    "wb_funnel_orders_sync",
                    "manual",
                    lambda: wb_funnel_orders.sync_store(store_slug),
                )
            else:
                await run_in_threadpool(
                    run_tracked,
                    "wb_funnel_orders_sync",
                    "manual",
                    wb_funnel_orders.sync_all,
                )
        return JSONResponse(
            await run_in_threadpool(wb_funnel_orders.dashboard, date_from, date_to, store_slug or None)
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except wb_api.WBApiError as exc:
        logger.warning("Не удалось загрузить заказы воронки WB для %s: %s", store_slug, exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)
    except Exception:
        logger.exception("Не удалось загрузить заказы воронки WB для %s", store_slug)
        return JSONResponse(
            {"ok": False, "error": "Не удалось загрузить заказы из воронки WB"}, status_code=500
        )


@router.get("/sales/orders.xlsx")
async def sales_orders_xlsx(
    request: Request, date_from: str = "", date_to: str = "", marketplace: str = "WB", store: str = ""
):
    marketplace = marketplace.strip().upper()
    if marketplace not in MARKETPLACES:
        raise HTTPException(status_code=400, detail="Неизвестный маркетплейс")
    store_slug = store.lower() or None
    scoped_stores = accessible_stores(request.state.user, marketplace)
    if store_slug and not has_action_permission(
        request.state.user,
        ActionPermission.SALES_EXPORT,
        store_slug=store_slug,
        marketplace=marketplace,
    ):
        raise HTTPException(status_code=403, detail="Нет доступа к выгрузке этого магазина и площадки")
    if store_slug is None and len(scoped_stores) != len(STORES):
        store_slug = scoped_stores[0] if scoped_stores else None
    if store_slug is None and not scoped_stores:
        raise HTTPException(status_code=403, detail="Нет доступа к этой площадке")
    try:
        content = await run_in_threadpool(
            sales_service.export_xlsx,
            date_from,
            date_to,
            marketplace,
            store_slug,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    filename = f"orders_{marketplace.lower().replace(' ', '_')}_{date_from}_{date_to}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sales/ephemerides", response_class=HTMLResponse)
async def sales_ephemerides(request: Request):
    return render_sales_placeholder(
        request,
        "Эфемериды",
        "sales_ephemerides",
        "Сводка продаж и ключевых событий по дням.",
    )
