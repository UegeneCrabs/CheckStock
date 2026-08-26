import html
import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app import auth, db, health, supply_planning
from app.domain import MOSCOW_TIMEZONE
from app.dto.identity import SectionAccessLevel, SectionName, coerce_user
from app.dto.stock import StockRandomizerGenerateRequest
from app.formatting import format_dt
from app.section_access import access_level
from app.stores import STORES
from app.web.access import accessible_store_slugs
from app.web.common import _fmt_num
from app.web.stock_rendering import (
    fbs_schemes_for,
    marketplace_ready,
    render_ff_options,
    render_mp_move_options,
    render_mp_tabs,
    render_stock_head,
    render_stock_rows,
    schemes_for,
)
from app.web.templating import fill_template, render_page
from app.yandex import sync as ya_sync

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


def _get_fbs_stock(store_slug: str, marketplace: str, warehouse: str = "") -> dict[str, int]:
    totals: dict[str, int] = {}
    for scheme in fbs_schemes_for(marketplace, store_slug):
        rows = (
            db.get_mp_stock_by_warehouse(store_slug, marketplace, scheme, warehouse)
            if warehouse
            else db.get_mp_stock_totals(store_slug, marketplace, scheme)
        )
        for article, quantity in rows.items():
            totals[article] = totals.get(article, 0) + int(quantity or 0)
    return totals


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
    barcode_html = f"<small>Баркод {html.escape(barcode)}</small>" if barcode else ""
    return (
        '<div class="randomizer-result is-ready" data-randomizer-result>'
        "<span>АРТИКУЛ ДЛЯ СВЕРКИ</span>"
        f"<strong>{html.escape(str(article))}</strong>"
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
    allowed_stores = accessible_store_slugs(request.state.user)
    allowed_set = set(allowed_stores)
    overview = await run_in_threadpool(db.get_stock_overview)
    overview = {slug: item for slug, item in overview.items() if slug in allowed_set}
    problems = await run_in_threadpool(health.stores_with_problems)
    problems = {slug: items for slug, items in problems.items() if slug in allowed_set}

    marketplace_labels = {
        "WB": "WB",
        "OZON": "Ozon",
        "YANDEX MARKET": "Яндекс",
    }
    cards = []
    for slug in allowed_stores:
        store = STORES[slug]
        item = overview.get(slug, {})
        store_problems = problems.get(slug) or []
        marketplaces = item.get("marketplaces") or []
        badges = (
            "".join(
                f"<span>{html.escape(marketplace_labels.get(mp, mp))}</span>"
                for mp in db.MARKETPLACES
                if mp in marketplaces
            )
            or '<span class="is-muted">Нет данных</span>'
        )
        if store_problems:
            status_class = " store-card-status--warning"
            status_text = f"Проблем с доступом: {len(store_problems)}"
        elif marketplaces:
            status_class = ""
            status_text = "Данные доступны"
        else:
            status_class = " store-card-status--muted"
            status_text = "Ожидает синхронизации"

        cards.append(
            f'<a class="store-card stock-store-card" href="/stock/{slug}" '
            f'style="--store-color:{store["color"]};--store-text:{store["text"]}">'
            '<span class="stock-store-head">'
            f'<span class="store-avatar" style="background:{store["color"]};color:{store["text"]}">'
            f"{html.escape(store['initials'])}</span>"
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>'
            "</span>"
            f'<strong class="store-name">{html.escape(store["name"])}</strong>'
            f'<span class="store-card-status{status_class}"><i aria-hidden="true"></i>'
            f"{html.escape(status_text)}</span>"
            '<span class="store-card-metrics">'
            f"<span><small>Остаток</small><b>{_fmt_num(int(item.get('total_stock') or 0))}</b></span>"
            f"<span><small>Товаров</small><b>{_fmt_num(int(item.get('sku_count') or 0))}</b></span>"
            "</span>"
            f'<span class="store-marketplaces">{badges}</span>'
            "</a>"
        )

    total_stock = sum(int(item.get("total_stock") or 0) for item in overview.values())
    total_sku = sum(int(item.get("sku_count") or 0) for item in overview.values())
    total_connections = sum(int(item.get("marketplace_count") or 0) for item in overview.values())
    problem_count = sum(len(items) for items in problems.values())
    stock_summary = (
        '<div class="stock-summary" role="list">'
        f'<div role="listitem"><span>Магазинов</span><strong>{len(allowed_stores)}</strong></div>'
        f'<div role="listitem"><span>Общий остаток</span><strong>{_fmt_num(total_stock)}</strong></div>'
        f'<div role="listitem"><span>Товаров в каталогах</span><strong>{_fmt_num(total_sku)}</strong></div>'
        f'<div role="listitem"><span>Подключений к площадкам</span><strong>{_fmt_num(total_connections)}</strong></div>'
        f'<div class="stock-summary-health{" is-warning" if problem_count else ""}" role="listitem">'
        f"<span>Состояние данных</span><strong>{problem_count if problem_count else 'В норме'}</strong></div>"
        "</div>"
    )
    last_sync = await run_in_threadpool(db.get_last_sync_at)
    content = fill_template(
        "stock_content.html",
        last_sync=html.escape(format_dt(last_sync)),
        stock_summary=stock_summary,
        store_cards="\n".join(cards),
        sync_products_button=(
            '<button class="btn-primary btn-sync" type="button" id="sync-products-btn" '
            'aria-describedby="sync-products-status">Обновить остатки</button>'
            if auth.has_role(request.state.user, "admin")
            and access_level(request.state.user, SectionName.STOCK) is SectionAccessLevel.WRITE
            else ""
        ),
    )
    return render_page("CheckStock — Остатки", "stock", content, request.state.user)


@router.get("/stock/supplies", response_class=HTMLResponse)
async def stock_supplies(request: Request):
    allowed_stores = accessible_store_slugs(request.state.user)
    date_bounds = supply_planning.planned_supply_date_bounds()
    wb_store_options = "".join(
        f'<option value="{html.escape(slug)}">{html.escape(STORES[slug]["name"])}</option>'
        for slug in allowed_stores
    )
    content = fill_template(
        "stock_supplies_content.html",
        wb_store_options=wb_store_options,
        manual_store_options=wb_store_options,
        wb_date_min=date_bounds["min_date"].isoformat(),
        wb_date_max=date_bounds["max_date"].isoformat(),
        wb_date_from=date_bounds["default_from"].isoformat(),
        wb_date_to=date_bounds["default_to"].isoformat(),
        can_edit_supply="1" if auth.can_edit_stock(request.state.user) else "0",
    )
    return render_page(
        "CheckStock — Поставки",
        "stock_supplies",
        content,
        request.state.user,
        content_class="content--stock-supplies",
    )


@router.get("/stock/randomizer", response_class=HTMLResponse)
async def stock_randomizer(request: Request, ff: str = ""):
    allowed_stores = accessible_store_slugs(request.state.user)
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
        "stock_randomizer_content.html",
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
    allowed_stores = accessible_store_slugs(user)
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
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp if mp in db.MARKETPLACES else db.DEFAULT_MARKETPLACE
    content = fill_template(
        "store_content.html",
        store_name=store["name"],
        store_color=store["color"],
        store_text=store["text"],
        store_initials=store["initials"],
        slug=slug.lower(),
        ff_options=render_ff_options(),
        mp_tabs=render_mp_tabs(marketplace, slug.lower()),
        mp_move_options=render_mp_move_options(),
        marketplace=html.escape(marketplace),
        mp_ready="1" if marketplace_ready(marketplace, slug.lower()) else "0",
        can_edit_stock="1" if auth.can_edit_stock(request.state.user) else "0",
        access_problems=html.escape(json.dumps(health.store_problems(slug.lower()), ensure_ascii=False)),
        stock_head=render_stock_head(marketplace, slug.lower()),
        scheme_list=",".join(scheme for scheme, _ in schemes_for(marketplace, slug.lower())),
        scheme_count=str(len(schemes_for(marketplace, slug.lower()))),
        stock_rows=render_stock_rows(slug.lower(), marketplace),
    )
    return render_page(
        f"РАКЕТА — Остатки — {store['name']}",
        "stock",
        content,
        request.state.user,
        content_class="content--store",
    )


@router.get("/stock/{slug}/fbs")
async def stock_store_fbs_by_ff(slug: str, ff: str = "", mp: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp or db.DEFAULT_MARKETPLACE
    stock = await run_in_threadpool(_get_fbs_stock, slug.lower(), marketplace, ff)

    return JSONResponse({"fbs": stock})


@router.get("/stock/{slug}/ff-available")
async def stock_store_ff_available(slug: str, ff: str = "", mp: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    available = await run_in_threadpool(db.get_ff_available_totals, slug.lower(), ff or None, mp or None)
    return JSONResponse({"ff_available": available})


@router.get("/stock/{slug}/article-detail")
async def stock_store_article_detail(slug: str, article: str = "", mp: str = ""):

    store_slug = slug.lower()
    if store_slug not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    article = article.strip()
    if not article:
        raise HTTPException(status_code=400, detail="Артикул не указан")

    marketplace = mp or db.DEFAULT_MARKETPLACE
    if marketplace not in db.MARKETPLACES:
        raise HTTPException(status_code=400, detail="Неизвестный маркетплейс")

    def load_warehouses() -> list[dict]:
        fulfillments = db.get_fulfillments()
        fbs_by_warehouse: dict[str, int] = {}
        yandex_names = ya_sync.known_ff_by_campaign() if marketplace == "YANDEX MARKET" else {}
        for row in db.get_mp_fbs_warehouse_details(store_slug, marketplace, article):
            warehouse = str(row["warehouse"])
            if yandex_names:
                warehouse = yandex_names.get(ya_sync._normalize_ff_name(warehouse), warehouse)
            fbs_by_warehouse[warehouse] = fbs_by_warehouse.get(warehouse, 0) + int(row["quantity"] or 0)

        warehouses = []
        for fulfillment in fulfillments:
            available = db.get_ff_available_totals(store_slug, fulfillment, marketplace).get(article, 0)
            warehouses.append(
                {
                    "name": fulfillment,
                    "available": int(available or 0),
                    "fbs": fbs_by_warehouse.pop(fulfillment, 0),
                }
            )
        warehouses.extend(
            {"name": warehouse, "available": 0, "fbs": quantity}
            for warehouse, quantity in sorted(fbs_by_warehouse.items())
        )
        return warehouses

    warehouses = await run_in_threadpool(load_warehouses)
    return JSONResponse(
        {
            "article": article,
            "marketplace": marketplace,
            "warehouses": warehouses,
        }
    )
