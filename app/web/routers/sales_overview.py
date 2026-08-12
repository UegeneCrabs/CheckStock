import html
import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import sales as sales_service
from app.stores import STORES
from app.web.access import (
    accessible_store_items,
    accessible_store_slugs,
    first_accessible_store,
    has_store_access,
)
from app.web.routers.sales_common import render_sales_placeholder
from app.web.templating import fill_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return RedirectResponse("/sales", status_code=303)


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
    content = fill_template(
        "sales_content.html",
        default_from=(today - timedelta(days=29)).isoformat(),
        default_to=today.isoformat(),
        date_max=today.isoformat(),
        sales_store_options="".join(store_options),
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
    store_slug = store.lower() or None
    if store_slug and not has_store_access(request.state.user, store_slug):
        return JSONResponse({"ok": False, "error": "Нет доступа к этому магазину"}, status_code=403)
    if store_slug is None and len(accessible_store_slugs(request.state.user)) != len(STORES):
        store_slug = first_accessible_store(request.state.user)
    if store_slug is None and not accessible_store_slugs(request.state.user):
        return JSONResponse({"ok": False, "error": "Нет доступных магазинов"}, status_code=403)
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


@router.get("/sales/orders.xlsx")
async def sales_orders_xlsx(
    request: Request, date_from: str = "", date_to: str = "", marketplace: str = "WB", store: str = ""
):
    store_slug = store.lower() or None
    if store_slug and not has_store_access(request.state.user, store_slug):
        raise HTTPException(status_code=403, detail="Нет доступа к этому магазину")
    if store_slug is None and len(accessible_store_slugs(request.state.user)) != len(STORES):
        store_slug = first_accessible_store(request.state.user)
    if store_slug is None and not accessible_store_slugs(request.state.user):
        raise HTTPException(status_code=403, detail="Нет доступных магазинов")
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
