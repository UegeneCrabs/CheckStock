"""Read-only standard WB costs enriched with local calculator inputs."""

import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.access.resources import accessible_stores
from app.core.stores import STORES
from app.economics.wb.standard_profit import enrich
from app.repositories import wb_standard_costs
from app.web.templating import fill_template, render_page

router = APIRouter()
API = "/api/unit-economics-1c/reports/standard-costs"
logger = logging.getLogger(__name__)


def load_reports(stores):
    results = []
    for store in stores:
        entry = {"slug": store, "name": STORES[store]["name"], "report": None}
        try:
            snapshot = wb_standard_costs.load(store)
            entry["report"] = enrich(store, snapshot) if snapshot else None
        except Exception:
            logger.exception("standard_costs_read_failed store=%s", store)
            entry["error"] = "Не удалось загрузить отчёт магазина. Повторите позже."
        results.append(entry)
    return {"stores": results}


@router.get("/sales/unit-economics-1c/reports/conditions-comparison")
async def old_page():
    return RedirectResponse("/sales/unit-economics-1c/reports/standard-costs")


@router.get("/sales/unit-economics-1c/reports/standard-costs", response_class=HTMLResponse)
async def page(request: Request):
    return render_page(
        "CheckStock — Расходы WB без ИУ",
        "wb_standard_costs",
        fill_template("economics/wb/standard-costs.html"),
        request.state.user,
        content_class="content--unit-1c-report",
    )


@router.get(API)
async def data(request: Request):
    stores = accessible_stores(request.state.user, "WB")
    if not stores:
        return JSONResponse({"error": "Нет доступа"}, status_code=403)

    return await run_in_threadpool(load_reports, stores)
