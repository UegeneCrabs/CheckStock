from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from app.access.authentication import is_active_user
from app.access.resources import store_marketplace_pairs
from app.core.stores import STORES
from app.stock import total as stock_total_service
from app.web.access import accessible_store_slugs
from app.web.client_page import client_page
from app.web.downloads import _download_headers
from app.web.stock_scope import stock_scope

router = APIRouter()


def _selected_store(store: str, accessible: tuple[str, ...]) -> str:
    selected = store.strip().lower()
    if not selected:
        return ""
    if selected not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if selected not in accessible:
        raise HTTPException(status_code=403, detail="Нет доступа к этому магазину")
    return selected


@router.get("/stock/total", response_class=HTMLResponse)
async def stock_total(request: Request, store: str = ""):
    stock_scope(request, store)
    return client_page("stock/total.html")


@router.get("/stock/total.xlsx")
async def stock_total_xlsx(request: Request, store: str = ""):
    if not is_active_user(request.state.user):
        raise HTTPException(status_code=403, detail="Нет доступа к выгрузке сводных остатков")
    store_slugs = accessible_store_slugs(request.state.user)
    selected_store = _selected_store(store, store_slugs)
    allowed_pairs = store_marketplace_pairs(request.state.user)

    def build() -> tuple[bytes, str]:
        return stock_total_service.build_xlsx(
            stock_total_service.build_rows(store_slugs, allowed_pairs, selected_store)
        )

    content, filename = await run_in_threadpool(build)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )
