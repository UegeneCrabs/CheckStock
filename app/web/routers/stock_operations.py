from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import db
from app.access.resources import has_store_marketplace, store_marketplace_pairs
from app.core.constants import STOCK_READ, StockOperationFilter
from app.core.logging_config import tagged_logger
from app.core.stores import STORES
from app.ff_import import export as ff_export
from app.stock.export_schemes import schemes_for
from app.web.client_page import client_page
from app.web.downloads import _download_headers
from app.web.stock_scope import stock_scope

router = APIRouter()
logger = tagged_logger(__name__, "web", "stock_operations")


def _history_kinds(kind: str) -> tuple[str, ...] | None:

    kind = (kind or "").strip()
    known = set(StockOperationFilter)
    if kind == "transfer":
        return STOCK_READ.transfer_kinds
    if kind == "shipment":
        return STOCK_READ.shipment_kinds
    return (kind,) if kind in known else None


@router.get("/stock/{slug}/operations", response_class=HTMLResponse)
async def stock_store_operations(
    request: Request, slug: str, kind: str = "", page: Annotated[int, Query(ge=1)] = 1
):

    stock_scope(request, slug)
    return client_page("stock/operations.html")


@router.get("/stock/{slug}/operations/xlsx")
async def stock_store_operations_xlsx(request: Request, slug: str, kind: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    kinds = _history_kinds(kind)
    if not any(
        (
            store_slug == slug.lower()
            and has_store_marketplace(request.state.user, store_slug=store_slug, marketplace=marketplace)
            for store_slug, marketplace in store_marketplace_pairs(request.state.user)
        )
    ):
        raise HTTPException(status_code=403, detail="Нет доступа к выгрузке операций")
    marketplaces = tuple(
        (
            marketplace
            for store_slug, marketplace in store_marketplace_pairs(request.state.user)
            if store_slug == slug.lower()
        )
    )

    def _build():
        operations = db.get_operations_with_items(slug.lower(), kinds, None, marketplaces=marketplaces)
        return ff_export.build_history_xlsx(slug.lower(), store["name"], operations)

    try:
        content, filename = await run_in_threadpool(_build)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


def _warehouse_tables(store_slug: str, marketplace: str) -> list[tuple[str, list[dict]]]:

    marketplace_tables = [
        (label, db.get_mp_warehouse_details(store_slug, marketplace, scheme))
        for scheme, label in schemes_for(marketplace, store_slug)
    ]
    return [
        *marketplace_tables,
        ("ФФ фулфилменты", db.get_ff_warehouse_details_by_mp(store_slug, marketplace)),
        ("Мусорка", db.get_trash_details(store_slug, marketplace)),
    ]


def _fbs_warehouse_rows(store_slug: str, marketplace: str) -> list[dict]:
    return db.get_mp_fbs_warehouse_details(store_slug, marketplace)


@router.get("/stock/{slug}/stock.xlsx")
async def stock_store_xlsx(request: Request, slug: str, mp: str = "", ff: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp if mp in db.MARKETPLACES else db.DEFAULT_MARKETPLACE
    if not has_store_marketplace(request.state.user, store_slug=slug.lower(), marketplace=marketplace):
        raise HTTPException(status_code=403, detail="Нет доступа к выгрузке этой площадки")
    store_slug = slug.lower()
    schemes = schemes_for(marketplace, store_slug)

    def _build():
        items = db.get_stock_items(store_slug, marketplace, tuple(k for k, _ in schemes))

        ff_map = db.get_ff_available_totals(store_slug, ff or None, marketplace)
        fbs_maps = {
            scheme: db.get_mp_stock_by_warehouse(store_slug, marketplace, scheme, ff)
            for scheme, _label in schemes
            if ff and (scheme == "fbs" or scheme.startswith("fbs_"))
        }

        transit_map = db.get_ff_transit_totals(store_slug, marketplace, ff or None)
        columns = [
            "АРТИКУЛ",
            "ШТРИХКОД",
            "НАЗВАНИЕ",
            "ТОТАЛ",
            "ДОСТУПНО ФФ ДЛЯ РАСПРЕДЕЛЕНИЯ",
            "В ПУТИ МЕЖДУ ФФ",
        ]
        columns += [title.upper() for _scheme, title in schemes]

        rows = []
        totals = [0] * len(columns)
        for item in items:
            article = item["article"]
            ff_available = ff_map.get(article, 0) or 0
            transit_quantity = transit_map.get(article, 0) or 0

            by_scheme = []
            for scheme, _title in schemes:
                if scheme in fbs_maps:
                    by_scheme.append(fbs_maps[scheme].get(article, 0) or 0)
                else:
                    by_scheme.append(item[f"{scheme}_stock"] or 0)

            row_total = ff_available + transit_quantity + sum(by_scheme)
            rows.append(
                [
                    article,
                    item["barcode"],
                    item["name"],
                    row_total,
                    ff_available,
                    transit_quantity,
                    *by_scheme,
                ]
            )

            for index, value in enumerate(
                [row_total, ff_available, transit_quantity, *by_scheme],
                start=3,
            ):
                totals[index] += value

        totals[0] = "ИТОГО"
        totals[1] = ""
        totals[2] = f"позиций: {len(rows)}"

        return ff_export.build_stock_xlsx(
            store_slug,
            store["name"],
            marketplace,
            columns,
            rows,
            totals,
            ff,
        )

    try:
        content, filename = await run_in_threadpool(_build)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@router.get("/stock/{slug}/warehouses/xlsx")
async def stock_store_warehouses_xlsx(request: Request, slug: str, mp: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp or db.DEFAULT_MARKETPLACE
    if not has_store_marketplace(request.state.user, store_slug=slug.lower(), marketplace=marketplace):
        raise HTTPException(status_code=403, detail="Нет доступа к выгрузке этой площадки")

    def _build():
        tables = _warehouse_tables(slug.lower(), marketplace)
        return ff_export.build_warehouses_xlsx(slug.lower(), store["name"], marketplace, tables)

    try:
        content, filename = await run_in_threadpool(_build)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@router.post("/stock/{slug}/trash/checked")
async def toggle_trash_checked(
    request: Request,
    slug: str,
    marketplace: str = Form(...),
    article: str = Form(...),
    fulfillment: str = Form(...),
    checked: str = Form(""),
):

    if slug.lower() not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    if not has_store_marketplace(
        request.state.user, store_slug=slug.lower(), marketplace=marketplace.strip()
    ):
        return JSONResponse({"ok": False, "error": "Нет доступа к списанию этой площадки"}, status_code=403)

    value = checked.strip().lower() in ("1", "true", "on", "yes")
    await run_in_threadpool(
        db.set_trash_checked,
        slug.lower(),
        marketplace.strip(),
        article.strip(),
        fulfillment.strip(),
        value,
    )
    return JSONResponse({"ok": True, "checked": value})


@router.get("/stock/{slug}/warehouses", response_class=HTMLResponse)
async def stock_store_warehouses(request: Request, slug: str, mp: str = ""):
    stock_scope(request, slug, mp, select_marketplace=True)
    return client_page("stock/warehouses.html")
