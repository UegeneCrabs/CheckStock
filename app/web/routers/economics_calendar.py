"""Daily economics inside the existing WB/YM section, with server-side scope checks."""

import json
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr

from app import db
from app.access.access_control import accessible_stores, has_scope
from app.access.economics_preview import require_preview
from app.access.sections import has_access
from app.core.domain import MOSCOW_TIMEZONE
from app.core.stores import STORES
from app.dto.identity import SectionAccessLevel, SectionName, coerce_user
from app.economics.daily_calculation import fields
from app.economics.yandex.reports import catalog as yandex_catalog
from app.economics.yandex.reports import permitted
from app.repositories import daily_economics as repository
from app.stock.catalog_identity import display_barcode
from app.web.templating import fill_template, render_page

router = APIRouter()


def columns(marketplace):
    result = [
        {"key": k, "label": label, "unit": unit, "editable": True} for k, label, unit in fields(marketplace)
    ]
    if marketplace == "YANDEX MARKET":
        result += [
            {"key": k, "label": label, "unit": unit, "editable": False}
            for k, label, unit in (
                ("category_id", "Категория тарифа (ID)", ""),
                ("volume_l", "Объём по габаритам", "л"),
                ("return_cost", "Возврат по тарифу", "₽"),
                ("delivery_customer", "Доставка покупателю", "₽"),
                ("delivery_other", "Прочая доставка", "₽"),
            )
        ]
    return result


def market(request):
    return "YANDEX MARKET" if "/yandex-market" in request.url.path else "WB"


def section(marketplace):
    return SectionName.UNIT_ECONOMICS_WB if marketplace == "WB" else SectionName.UNIT_ECONOMICS_YANDEX


def authorize(request, store, *, write=False):
    require_preview(request.state.user)
    marketplace = market(request)
    level = SectionAccessLevel.WRITE if write else SectionAccessLevel.READ
    if (
        store not in STORES
        or not has_scope(request.state.user, store, marketplace)
        or not has_access(request.state.user, section(marketplace), level)
    ):
        raise HTTPException(403, "Нет доступа к магазину или прав на это действие.")
    return marketplace


def products(marketplace, store, user):
    user = coerce_user(user)
    if marketplace == "YANDEX MARKET":
        return [
            {
                **product,
                "barcode": display_barcode(product, marketplace),
                "barcodes": [
                    code for code in product.get("barcodes", []) if not str(code).strip().startswith("0")
                ],
            }
            for product in yandex_catalog((store,), user)
        ]
    refs = {r["article"]: r for r in db.get_unit_economics_1c_product_reference_rows((store,))}
    return [
        p
        for p in db.get_catalog_items(store, "WB")
        if permitted(refs.get(p["article"], {}).get("manager"), user)
    ]


def product_access(request, store, article, *, write=False):
    marketplace = authorize(request, store, write=write)
    if not any(p["article"] == article for p in products(marketplace, store, request.state.user)):
        raise HTTPException(404, "Товар не найден или недоступен в пределах прав менеджера.")
    return marketplace


def validate_day(day):
    if day > datetime.now(MOSCOW_TIMEZONE).date():
        raise HTTPException(422, "Будущие даты недоступны.")


def page(request):
    require_preview(request.state.user)
    marketplace = market(request)
    if not has_access(request.state.user, section(marketplace), SectionAccessLevel.READ):
        raise HTTPException(403, "Нет доступа к юнит-экономике.")
    base = "/sales/unit-economics-1c" + ("/yandex-market" if marketplace != "WB" else "")
    config = {
        "endpoint": base.replace("/sales/", "/api/") + "/calendar",
        "stores": [
            {"slug": s, "name": STORES[s]["name"]} for s in accessible_stores(request.state.user, marketplace)
        ],
        "today": datetime.now(MOSCOW_TIMEZONE).date().isoformat(),
        "marketplace": marketplace,
    }
    content = fill_template(
        "economics/shared/calendar.html",
        calendar_config=json.dumps(config, ensure_ascii=False).replace("</", "<\\/"),
        economics_url=base,
    )
    return render_page(
        "CheckStock — Календарь юнит-экономики",
        "unit_1c_wb" if marketplace == "WB" else "unit_1c_yandex",
        content,
        request.state.user,
        content_class="content--unit-1c",
    )


def listing(request, store, day, query, page_number, page_size):
    marketplace = authorize(request, store)
    validate_day(day)
    catalog = products(marketplace, store, request.state.user)
    first = repository.first_day(marketplace, store, {p["article"] for p in catalog})
    saved = repository.day_records(marketplace, store, day.isoformat())
    query = query.casefold().strip()
    catalog = [
        p
        for p in catalog
        if not query
        or query
        in " ".join(str(p.get(k) or "") for k in ("name", "article", "barcode", "barcodes")).casefold()
    ]
    catalog.sort(key=lambda p: (str(p.get("name") or "").casefold(), p["article"]))
    results = [saved.get(p["article"], {}).get("result", {}) for p in catalog]
    complete = sum(
        1 for r in results if r.get("margin") is not None and r.get("complete", not r.get("missing"))
    )
    profits = [r.get("day_profit") for r in results]
    known_profits = [v for v in profits if v is not None]
    profit_complete = (
        bool(profits)
        and len(known_profits) == len(profits)
        and all(r.get("daily_complete", not r.get("daily_missing")) for r in results)
    )
    missing = [
        {
            "article": p["article"],
            "parameters": r.get("daily_missing") or (["snapshot"] if not r else []),
            "unavailable": r.get("day_profit") is None,
        }
        for p, r in zip(catalog, results, strict=True)
        if r.get("day_profit") is None or r.get("daily_missing")
    ]
    offset = (page_number - 1) * page_size
    rows = []
    for p in catalog[offset : offset + page_size]:
        snapshot = saved.get(p["article"])
        rows.append(
            {
                "product": p,
                "snapshot": bool(snapshot),
                "values": snapshot["values"] if snapshot else {},
                "result": snapshot["result"] if snapshot else {},
                "corrected": list(snapshot["overrides"]) if snapshot else [],
                "basis": snapshot["source"]["basis"] if snapshot else None,
            }
        )
    return {
        "rows": rows,
        "total": len(catalog),
        "complete": complete,
        "day_profit": round(sum(known_profits), 2) if known_profits else None,
        "profit_complete": profit_complete,
        "profit_missing": missing,
        "first_day": first,
        "day": day.isoformat(),
        "preliminary": day == datetime.now(MOSCOW_TIMEZONE).date(),
        "can_edit": has_access(request.state.user, section(marketplace), SectionAccessLevel.WRITE),
        "fields": columns(marketplace),
    }


@router.get("/api/unit-economics-1c/calendar")
@router.get("/api/unit-economics-1c/yandex-market/calendar")
async def read_calendar(
    request: Request,
    store: str,
    day: date,
    q: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=20, le=100),
):
    if page_size not in {20, 50, 100}:
        raise HTTPException(422, "Размер страницы: 20, 50 или 100 товаров.")
    return await run_in_threadpool(listing, request, store, day, q, page, page_size)


@router.get("/api/unit-economics-1c/calendar/cell")
@router.get("/api/unit-economics-1c/yandex-market/calendar/cell")
async def read_cell(request: Request, store: str, day: date, article: str):
    marketplace = await run_in_threadpool(product_access, request, store, article)
    validate_day(day)
    data = await run_in_threadpool(
        repository.get, (marketplace, store, article, day.isoformat()), events=True
    )
    if not data:
        raise HTTPException(404, "Снимок за день отсутствует.")
    return data


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    store: str
    day: date
    article: str = Field(min_length=1, max_length=500)
    field: str
    value: StrictFloat | StrictInt | StrictStr | None = None
    undo: bool = False
    token: str
    reason: str = Field(min_length=3, max_length=2000)
    preview_token: str = ""


async def change_context(request, payload):
    marketplace = await run_in_threadpool(product_access, request, payload.store, payload.article, write=True)
    validate_day(payload.day)
    return marketplace, payload.store, payload.article, payload.day.isoformat()


@router.post("/api/unit-economics-1c/calendar/preview")
@router.post("/api/unit-economics-1c/yandex-market/calendar/preview")
async def preview(request: Request, payload: Correction):
    key = await change_context(request, payload)
    data = await run_in_threadpool(repository.get, key)
    if not data or data["token"] != payload.token:
        raise HTTPException(409, "Данные изменились. Откройте ячейку заново.")
    try:
        result, _ = repository.proposal(key, data, payload.field, payload.value, payload.undo)
        return result
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.post("/api/unit-economics-1c/calendar/correction")
@router.post("/api/unit-economics-1c/yandex-market/calendar/correction")
async def correct(request: Request, payload: Correction):
    key = await change_context(request, payload)
    user = coerce_user(request.state.user)
    try:
        return await run_in_threadpool(
            repository.correct,
            key,
            token=payload.token,
            preview_token=payload.preview_token,
            field=payload.field,
            value=payload.value,
            reason=payload.reason,
            actor=f"{user.id}: {user.full_name}",
            undo=payload.undo,
        )
    except repository.Conflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
