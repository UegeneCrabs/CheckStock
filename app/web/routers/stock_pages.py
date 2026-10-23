import html
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app import db
from app.access.resources import accessible_stores
from app.core.constants import WebErrorCode
from app.core.domain import MOSCOW_TIMEZONE
from app.core.formatting import format_dt
from app.core.stores import STORES
from app.dto.identity import coerce_user
from app.dto.stock import StockRandomizerGenerateRequest
from app.infrastructure.stock_page_repository import StockPageRepository
from app.web.client_page import client_page
from app.web.identifiers import copy_identifier
from app.web.stock_scope import stock_scope
from app.web.templating import fill_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)

DEFAULT_RANDOMIZER_FULFILLMENT = "ФулСервис Подольск"
RUSSIAN_MONTHS = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)


def _randomizer_period(now: datetime) -> tuple[str, str]:
    return now.strftime("%Y-%m"), f"{RUSSIAN_MONTHS[now.month - 1]} {now.year}"


def _randomizer_fulfillment_options(fulfillments: list[str], selected: str) -> str:
    return "".join(
        f'<option value="{html.escape(name, quote=True)}"{" selected" if name == selected else ""}>'
        f"{html.escape(name)}</option>"
        for name in fulfillments
    )


def _randomizer_result(item: dict) -> str:
    article = item.get("article")
    if not article:
        message = str(item.get("message") or "Нажмите «Сгенерировать», чтобы выбрать артикул")
        return (
            '<div class="randomizer-result is-empty" data-randomizer-result>'
            '<span class="randomizer-result-icon" aria-hidden="true">?</span>'
            f"<p>{html.escape(message)}</p>"
            "</div>"
        )
    barcode = str(item.get("barcode") or "")
    barcode_html = (
        f"<small>{copy_identifier(barcode, 'Баркод', f'Баркод {barcode}')}</small>" if barcode else ""
    )
    return (
        '<div class="randomizer-result is-ready" data-randomizer-result>'
        "<span>АРТИКУЛ ДЛЯ СВЕРКИ</span>"
        f"<strong>{copy_identifier(article, 'Артикул')}</strong>"
        f"<p>{html.escape(str(item.get('name') or 'Без названия'))}</p>"
        f"{barcode_html}"
        '<div class="randomizer-stock-pair">'
        f"<span><small>Учёт ФФ</small><b>{int(item.get('ff_quantity') or 0)} шт.</b></span>"
        f"<span><small>WB FBS</small><b>{int(item.get('fbs_quantity') or 0)} шт.</b></span>"
        "</div></div>"
    )


def _randomizer_card(store_slug: str, item: dict, month_label: str) -> str:
    store = STORES[store_slug]
    return (
        f'<article class="randomizer-store-card" data-randomizer-card data-store="{store_slug}" '
        f'style="--store-color:{store["color"]};--store-text:{store["text"]}">'
        '<div class="randomizer-store-head">'
        f'<span class="store-avatar" style="background:{store["color"]};color:{store["text"]}">'
        f"{html.escape(store['initials'])}</span>"
        f"<div><strong>{html.escape(store['name'])}</strong><small>Wildberries</small></div>"
        '<span class="randomizer-channel">FBS</span>'
        "</div>"
        f"{_randomizer_result(item)}"
        '<div class="randomizer-card-foot">'
        f"<span>Проверено за {html.escape(month_label)}: <b data-randomizer-used>"
        f"{int(item.get('used_count') or 0)}</b></span>"
        "<span>Осталось доступных: <b data-randomizer-remaining>"
        f"{int(item.get('remaining_count') or 0)}</b></span>"
        "</div></article>"
    )


@router.get("/stock", response_class=HTMLResponse)
async def stock(request: Request):
    stock_scope(request)
    return client_page("stock/index.html")


@router.get("/stock/supplies", response_class=HTMLResponse)
async def stock_supplies(request: Request):
    stock_scope(request)
    return client_page("stock/supplies.html")


@router.get("/stock/randomizer", response_class=HTMLResponse)
async def stock_randomizer(request: Request, ff: str = ""):
    allowed_stores = accessible_stores(request.state.user, "WB")
    if not allowed_stores:
        raise HTTPException(status_code=403, detail="Рандомайзер доступен только сотрудникам с доступом к WB")
    fulfillments = await run_in_threadpool(db.get_fulfillments)
    selected = (
        ff
        if ff in fulfillments
        else (
            DEFAULT_RANDOMIZER_FULFILLMENT
            if DEFAULT_RANDOMIZER_FULFILLMENT in fulfillments
            else (fulfillments[0] if fulfillments else "")
        )
    )
    now = datetime.now(MOSCOW_TIMEZONE)
    month_key, month_label = _randomizer_period(now)
    state = (
        await run_in_threadpool(db.get_stock_audit_state, allowed_stores, selected, month_key)
        if selected
        else {"generated_at": None, "items": []}
    )
    state_by_store = {item["store_slug"]: item for item in state["items"]}
    cards = "\n".join(
        _randomizer_card(
            slug,
            state_by_store.get(
                slug,
                {"article": None, "used_count": 0, "remaining_count": 0},
            ),
            month_label,
        )
        for slug in allowed_stores
    )
    last_generated = (
        format_dt(str(state["generated_at"])) if state.get("generated_at") else "Ещё не запускался"
    )
    content = fill_template(
        "stock/randomizer.html",
        fulfillment_options=_randomizer_fulfillment_options(fulfillments, selected),
        fulfillment_disabled="" if fulfillments else " disabled",
        month_label=html.escape(month_label),
        last_generated=html.escape(last_generated),
        store_cards=cards,
    )
    return render_page(
        "CheckStock — Рандомайзер",
        "stock_randomizer",
        content,
        request.state.user,
        content_class="content--stock-randomizer",
    )


@router.post("/stock/randomizer/generate")
async def generate_stock_randomizer(request: Request, payload: StockRandomizerGenerateRequest):
    fulfillments = await run_in_threadpool(db.get_fulfillments)
    if payload.fulfillment not in fulfillments:
        raise HTTPException(status_code=400, detail="Неизвестный фулфилмент")

    user = coerce_user(request.state.user)
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется вход в систему")
    allowed_stores = accessible_stores(user, "WB")
    if not allowed_stores:
        raise HTTPException(status_code=403, detail="Рандомайзер доступен только сотрудникам с доступом к WB")
    now = datetime.now(MOSCOW_TIMEZONE)
    month_key, month_label = _randomizer_period(now)
    result = await run_in_threadpool(
        db.generate_stock_audit_sample,
        allowed_stores,
        payload.fulfillment,
        month_key,
        user.id,
        user.full_name,
        now.isoformat(timespec="seconds"),
    )
    items = []
    for item in result["items"]:
        store_slug = item["store_slug"]
        items.append({**item, "store_name": STORES[store_slug]["name"]})
    return JSONResponse(
        {
            "ok": True,
            "fulfillment": payload.fulfillment,
            "month": month_label,
            "generated_at": result["generated_at"],
            "generated_count": sum(1 for item in items if item["article"]),
            "items": items,
        }
    )


@router.get("/stock/{slug}", response_class=HTMLResponse)
async def stock_store(request: Request, slug: str, mp: str = ""):
    stock_scope(request, slug, mp, select_marketplace=True)
    return client_page("stock/store.html")


@router.get("/stock/{slug}/total-data")
async def stock_store_total_data(request: Request, slug: str):
    return await StockPageRepository(request.app.state.async_resources.database).totals(
        stock_scope(request, slug)
    )


@router.get("/stock/{slug}/fbs")
async def stock_store_fbs_by_ff(request: Request, slug: str, ff: str = "", mp: str = ""):

    return await StockPageRepository(request.app.state.async_resources.database).fbs(
        stock_scope(request, slug, mp, select_marketplace=True), ff
    )


@router.get("/stock/{slug}/ff-available")
async def stock_store_ff_available(request: Request, slug: str, ff: str = "", mp: str = ""):

    return await StockPageRepository(request.app.state.async_resources.database).fulfillment(
        stock_scope(request, slug, mp, select_marketplace=True), ff
    )


@router.get("/stock/{slug}/transit")
async def stock_store_transit(request: Request, slug: str, ff: str = "", mp: str = ""):
    return await StockPageRepository(request.app.state.async_resources.database).transit(
        stock_scope(request, slug, mp, select_marketplace=True), ff
    )


@router.get("/stock/{slug}/article-detail")
async def stock_store_article_detail(request: Request, slug: str, article: str = "", mp: str = ""):

    if not article.strip():
        raise HTTPException(status_code=400, detail=WebErrorCode.INVALID_ARTICLE)
    return await StockPageRepository(request.app.state.async_resources.database).article(
        stock_scope(request, slug, mp, select_marketplace=True), article.strip()
    )
