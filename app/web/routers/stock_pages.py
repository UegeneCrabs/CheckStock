import html
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse

from app import auth, db, health
from app.formatting import format_dt
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
    )
    return render_page("CheckStock — Остатки", "stock", content, request.state.user)


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
