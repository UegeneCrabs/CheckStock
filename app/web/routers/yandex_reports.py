"""Marketplace-isolated YM reports sharing WB report controls and aggregation."""

import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response
from pydantic import Field

from app.access import auth
from app.access.access_control import accessible_stores
from app.access.sections import has_access
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.identity import SectionAccessLevel, SectionName, coerce_user
from app.dto.unit_economics_1c import (
    UnitEconomics1CProductTargetRequest,
    UnitEconomics1CTargetPriceExportRequest,
    UnitEconomics1CTargetRoiValues,
)
from app.dto.yandex_economics import EconomicsValues
from app.economics.report_cache import reports_cache, user_key
from app.economics.yandex import report_export, reports, target_prices
from app.repositories import yandex_economics as repository
from app.web.downloads import _download_headers
from app.web.routers.unit_economics import _query_values, _report_period, unit_profit_page
from app.web.templating import fill_template, render_page

router = APIRouter()
API = "/api/unit-economics-1c/yandex-market/reports"
PAGES = "/sales/unit-economics-1c/yandex-market/reports"


class TargetPreview(UnitEconomics1CProductTargetRequest):
    values: EconomicsValues = Field(default_factory=EconomicsValues)


class TargetChange(UnitEconomics1CProductTargetRequest):
    revision: int = Field(ge=0)


class CabinetTargets(UnitEconomics1CTargetRoiValues):
    revision: int = Field(ge=0)
    target_drr_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    target_roi_percent: float = Field(ge=0, le=1_000_000, allow_inf_nan=False)


def stores_for(request):
    accessible = accessible_stores(request.state.user, "YANDEX MARKET")
    selected = tuple(value.lower() for value in _query_values(request, "store"))
    if not selected or "all" in selected:
        return accessible
    if any(value not in accessible for value in selected):
        raise HTTPException(403, "Нет доступа к кабинету")
    return tuple(store for store in accessible if store in selected)


def product_access(request, store, article):
    if store not in accessible_stores(request.state.user, "YANDEX MARKET"):
        raise HTTPException(403, "Нет доступа к кабинету")
    if not any(
        row["article"] == article for row in reports.catalog((store,), coerce_user(request.state.user))
    ):
        raise HTTPException(404, "Товар не найден или недоступен")


@router.get(PAGES + "/unit-profit", response_class=HTMLResponse)
async def profit_page(request: Request):
    return unit_profit_page(request, yandex=True)


@router.get(API + "/unit-profit/filters")
async def profit_filters(request: Request):
    stores, user = stores_for(request), coerce_user(request.state.user)
    rows = await run_in_threadpool(reports.catalog, stores, user)
    return {"ok": True, **reports.filter_options(rows, user)}


async def profit_data(request, *, export=False):
    try:
        start, end = _report_period(request)
        if end > datetime.now(MOSCOW_TIMEZONE).date():
            raise ValueError("Период отчёта не может включать будущие даты")
        page = max(int(request.query_params.get("page") or 1), 1)
        page_size = min(max(int(request.query_params.get("page_size") or 50), 25), 100)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    stores, user = stores_for(request), coerce_user(request.state.user)
    subjects = {value.casefold() for value in _query_values(request, "subject")}
    managers = {value.casefold() for value in _query_values(request, "manager")}
    articles = {value.casefold() for value in _query_values(request, "article")}
    group = "subject" if request.query_params.get("group_by") == "subject" else "product"
    details = (
        str(request.query_params.get("daily_details") or "").lower() in {"1", "true", "yes"}
        and group == "product"
    )

    def load():
        def prepare():
            collected = {}
            rows = reports.load_rows(
                stores, user, start, end,
                filters={"subjects": subjects, "managers": managers, "articles": articles},
                collected=collected,
            )
            return {
                "rows": rows,
                "details": collected.get("details", {}),
                "options": reports.filter_options(collected.get("eligible", []), user),
            }

        prepared = reports_cache.get(
            ("ym-profit", stores, user_key(user), start, end, datetime.now(MOSCOW_TIMEZONE).date(),
             tuple(sorted(subjects)), tuple(sorted(managers)), tuple(sorted(articles))),
            prepare,
        )
        rows = prepared["rows"]
        options = prepared["options"]
        if export:
            for row in rows:
                row["daily_calculations"] = reports.daily_details(
                    prepared["details"][(row["store_slug"], row["article"])]
                )
        totals = reports.totals(rows)
        totals["margin_undercovered_days"] = totals["margin_missing_days"]
        categories = reports.categories(rows) if group == "subject" else []
        view = categories if group == "subject" else rows
        count = len(view)
        paged = details and not export
        pages = max((count + page_size - 1) // page_size, 1) if paged else 1
        current = min(page, pages) if paged else 1
        shown = view[(current - 1) * page_size : current * page_size] if paged else view
        if details and not export:
            for row in shown:
                row["daily_calculations"] = reports.daily_details(
                    prepared["details"][(row["store_slug"], row["article"])]
                )
        if not details and not export:
            shown = [{**row, "daily_calculations": []} for row in shown]
        return {
            "ok": True,
            "marketplace": "YANDEX MARKET",
            "period_from": start.isoformat(),
            "period_to": end.isoformat(),
            "rows": rows if export else shown,
            "category_rows": categories if export else [],
            "totals": totals,
            "group_by": group,
            "daily_details": details,
            "pagination": {
                "enabled": paged,
                "page": current,
                "page_size": page_size,
                "total_count": count,
                "total_pages": pages,
            },
            **options,
        }

    return await run_in_threadpool(load)


@router.get(API + "/unit-profit")
async def unit_profit(request: Request):
    return await profit_data(request)


@router.get(PAGES + "/unit-profit.xlsx")
async def profit_xlsx(request: Request):
    data = await profit_data(request, export=True)
    body, filename = await run_in_threadpool(report_export.build_xlsx, data)
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )


@router.get(PAGES + "/target-price", response_class=HTMLResponse)
async def target_page(request: Request):
    stores = stores_for(request)
    config = {
        "marketplace": "YM",
        "dataEndpoint": API + "/target-price",
        "exportEndpoint": API + "/target-price.xlsx",
        "stores": [{"slug": store, "name": STORES[store]["name"]} for store in stores],
        "canEdit": has_access(
            request.state.user, SectionName.REPORT_TARGET_PRICE_YANDEX, SectionAccessLevel.WRITE
        ),
        "canManageGoals": auth.has_role(request.state.user, "superadmin"),
    }
    content = fill_template(
        "economics/yandex/target-price.html",
        target_price_config=json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
    )
    return render_page(
        "CheckStock — Целевая цена · Яндекс Маркет",
        "unit_1c_target_price_yandex",
        content,
        request.state.user,
        content_class="content--unit-1c-report",
    )


@router.get(API + "/target-price")
async def target_data(request: Request):
    return await run_in_threadpool(
        target_prices.load,
        stores_for(request),
        coerce_user(request.state.user),
        article=str(request.query_params.get("article") or ""),
    )


@router.post(API + "/target-price/{store}/preview")
async def target_preview(request: Request, store: str, payload: TargetPreview):
    await run_in_threadpool(product_access, request, store, payload.article)
    data = await run_in_threadpool(
        target_prices.load,
        (store,),
        coerce_user(request.state.user),
        article=payload.article,
        overrides=payload.model_dump(exclude={"article", "values"}),
        scenario=payload.values.model_dump(exclude_none=True),
    )
    return data


@router.put(API + "/target-price/{store}/targets")
async def target_save(request: Request, store: str, payload: TargetChange):
    await run_in_threadpool(product_access, request, store, payload.article)
    try:
        await run_in_threadpool(
            target_prices.save_goals,
            store,
            payload.article,
            payload.model_dump(exclude={"article", "revision"}),
            payload.revision,
            str(request.state.user["full_name"]),
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@router.delete(API + "/target-price/{store}/targets")
async def target_reset(request: Request, store: str, article: str, revision: int):
    await run_in_threadpool(product_access, request, store, article)
    try:
        await run_in_threadpool(
            target_prices.save_goals,
            store,
            article,
            {"target_drr_percent": None, "target_roi_percent": None},
            revision,
            str(request.state.user["full_name"]),
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@router.get(API + "/target-price/settings/{store}")
async def cabinet_read(request: Request, store: str):
    if not auth.has_role(request.state.user, "superadmin") or store not in STORES:
        raise HTTPException(403, "Настройки целей кабинета доступны суперадминистратору")
    saved = await run_in_threadpool(repository.settings, store, "", target_prices.TARGET_SCOPE)
    return {
        "ok": True,
        "revision": saved["revision"],
        "values": {**target_prices.DEFAULTS, **saved["values"]},
    }


@router.put(API + "/target-price/settings/{store}")
async def cabinet_save(request: Request, store: str, payload: CabinetTargets):
    await cabinet_read(request, store)
    try:
        await run_in_threadpool(
            target_prices.save_goals,
            store,
            "",
            payload.model_dump(exclude={"revision"}),
            payload.revision,
            str(request.state.user["full_name"]),
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"ok": True}


@router.post(API + "/target-price.xlsx")
async def target_xlsx(request: Request, payload: UnitEconomics1CTargetPriceExportRequest):
    # Rebuild authorized rows instead of accepting financial values posted by the browser.
    data = await run_in_threadpool(target_prices.load, stores_for(request), coerce_user(request.state.user))
    actual = {(row["store_slug"], row["article"]): row for row in data["rows"]}
    data["rows"] = [
        actual[item.store_slug, item.article]
        for item in payload.rows
        if (item.store_slug, item.article) in actual
    ]
    body, filename = await run_in_threadpool(report_export.build_xlsx, data, target=True)
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )
