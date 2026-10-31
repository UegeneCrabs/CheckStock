"""Read-only standard WB costs enriched with local calculator inputs."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.access.resources import accessible_stores
from app.core.logging_config import tagged_logger
from app.core.stores import STORES
from app.dto.wb_standard_costs import ExportItem, ExportRequest, Reports, StoreReport
from app.dto.wb_standard_costs import ExportRow as ExportRow
from app.economics.wb.standard_export import build_xlsx
from app.economics.wb.standard_profit import enrich
from app.repositories import wb_standard_costs
from app.web.templating import fill_template, render_page

router = APIRouter()
API = "/api/unit-economics-1c/reports/standard-costs"
logger = tagged_logger(__name__, "web", "wb", "wb_standard_costs")


def load_reports(stores: list[str] | tuple[str, ...]) -> Reports:
    results = []
    for store in stores:
        entry = StoreReport(slug=store, name=STORES[store]["name"], report=None)
        try:
            snapshot = wb_standard_costs.load(store)
            entry = entry.model_copy(update={"report": enrich(store, snapshot) if snapshot else None})
        except Exception:
            logger.exception("standard_costs_read_failed store=%s", store)
            entry = entry.model_copy(
                update={"error": "Не удалось загрузить отчёт магазина. Повторите позже."}
            )
        results.append(entry)
    return Reports(stores=results)


@router.get("/sales/unit-economics-1c/reports/conditions-comparison")
async def old_page() -> RedirectResponse:
    return RedirectResponse("/sales/unit-economics-1c/reports/standard-costs")


@router.get("/sales/unit-economics-1c/reports/standard-costs", response_class=HTMLResponse)
async def page(request: Request) -> str:
    return render_page(
        "CheckStock — Расходы WB без ИУ",
        "wb_standard_costs",
        fill_template("economics/wb/standard-costs.html"),
        request.state.user,
        content_class="content--unit-1c-report",
    )


@router.get(API)
async def data(request: Request) -> JSONResponse:
    stores = accessible_stores(request.state.user, "WB")
    if not stores:
        return JSONResponse({"error": "Нет доступа"}, status_code=403)

    reports = await run_in_threadpool(load_reports, stores)
    return JSONResponse(reports.model_dump(mode="json", exclude_unset=True))


def export_rows(payload: ExportRequest, allowed: set[str]) -> bytes:
    selected = []
    for row in payload.rows:
        if row.store_slug not in selected:
            selected.append(row.store_slug)
    if not set(selected).issubset(allowed):
        raise HTTPException(403, "Нет доступа к магазину")
    lookup: dict[tuple[str, str, str], ExportItem] = {}
    for store in load_reports(selected).stores:
        report = store.report
        if not report:
            raise HTTPException(409, "Данные изменились или недоступны. Обновите отчёт.")
        for row in report.rows:
            if row.scheme == "Не определена":
                continue
            lookup[(store.slug, str(row.article), row.scheme)] = ExportItem(
                row, store.name, report.date_from, report.date_to
            )
    try:
        rows = [lookup[(row.store_slug, row.article, row.scheme)] for row in payload.rows]
    except KeyError as exc:
        raise HTTPException(409, "Данные изменились. Обновите отчёт.") from exc
    if any(
        source.period_from != item.period_from.isoformat() or source.period_to != item.period_to.isoformat()
        for source, item in zip(rows, payload.rows, strict=True)
    ):
        raise HTTPException(409, "Период данных изменился. Обновите отчёт перед выгрузкой.")
    return build_xlsx(rows)


@router.post(API + ".xlsx")
async def export(request: Request, payload: ExportRequest) -> Response:
    allowed = set(accessible_stores(request.state.user, "WB"))
    content = await run_in_threadpool(export_rows, payload, allowed)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="wb-conditions.xlsx"'},
    )
