import html
import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import auth, db
from app.config import settings
from app.ff_import import export as ff_export
from app.formatting import format_dt
from app.stores import STORES
from app.web.common import _fmt_num
from app.web.downloads import _download_headers
from app.web.routers.stock_mutations import _guard_stock_edit
from app.web.stock_rendering import (
    render_trash_table,
    render_warehouse_table,
    schemes_for,
)
from app.web.templating import fill_template, render_page

router = APIRouter()
logger = logging.getLogger(__name__)


OPERATION_FILTERS = [
    ("", "Все"),
    ("delivery", "Поставки"),
    ("transfer", "Перемещения"),
    ("shipment", "Отгрузки"),
    ("manual_add", "Ручные докладки"),
]


def render_kind_tabs(slug: str, active: str, counts: dict[str, int]) -> str:
    parts = []
    for kind, label in OPERATION_FILTERS:
        cls = "ops-filter active" if kind == active else "ops-filter"
        href = f"/stock/{slug}/operations" + (f"?kind={kind}" if kind else "")
        count = counts.get("", 0) if not kind else counts.get(kind, 0)
        parts.append(
            f'<a class="{cls}" href="{href}">{html.escape(label)}'
            f'<span class="ops-filter-count">{_fmt_num(count)}</span></a>'
        )
    return "\n".join(parts)


def _plural_positions(value: int) -> str:
    value = abs(value)
    if value % 10 == 1 and value % 100 != 11:
        return "позиция"
    if value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        return "позиции"
    return "позиций"


def render_operation_summary(operations: list[dict]) -> str:
    positions = sum(int(op.get("positions") or 0) for op in operations)
    units = sum(abs(int(op.get("units") or 0)) for op in operations)
    employees = len({op.get("user_name") for op in operations if op.get("user_name")})
    return (
        '<div class="ops-summary" role="list">'
        f'<div role="listitem"><span>Операций</span><strong>{_fmt_num(len(operations))}</strong></div>'
        f'<div role="listitem"><span>Товарных позиций</span><strong>{_fmt_num(positions)}</strong></div>'
        f'<div role="listitem"><span>Движение, ед.</span><strong>{_fmt_num(units)}</strong></div>'
        f'<div role="listitem"><span>Сотрудников</span><strong>{_fmt_num(employees)}</strong></div>'
        "</div>"
    )


def render_operation_rows(operations: list[dict]) -> str:
    if not operations:
        return '<tr class="empty-row"><td colspan="6">Движений пока не было</td></tr>'

    def endpoint(fulfillment, marketplace, fallback: str) -> str:
        title = fulfillment or fallback
        marketplace_html = f"<small>{html.escape(marketplace)}</small>" if marketplace else ""
        return f'<span class="ops-endpoint"><strong>{html.escape(title)}</strong>{marketplace_html}</span>'

    rows = []
    for op in operations:
        note = op.get("note") or ""
        source = op.get("source_name") or db.SOURCE_LABELS.get(op.get("source_type"), "")
        detail = note or source or "Без примечания"
        kind = op["kind"] if op["kind"] in db.OPERATION_LABELS else "other"
        created = format_dt(op["created_at"])
        created_parts = created.split(" ", 1)
        date = created_parts[0]
        time = created_parts[1] if len(created_parts) > 1 else ""
        from_fallback = (
            "Поставка" if kind == "delivery" else ("Ручной ввод" if kind == "manual_add" else "Не указано")
        )
        to_fallback = "Отгрузка" if kind == "shipment" else ("Мусорка" if kind == "trash" else "Не указано")
        units = int(op.get("units") or 0)
        unit_class = " ops-volume-value--negative" if units < 0 else ""
        rows.append(
            f'<tr class="ops-row ops-row--{kind}">'
            '<td data-label="Когда"><time class="ops-time">'
            f"<strong>{html.escape(date)}</strong><small>{html.escape(time)}</small></time></td>"
            '<td data-label="Операция">'
            f'<span class="ops-kind ops-kind--{kind}">'
            f'<i aria-hidden="true"></i>{html.escape(db.OPERATION_LABELS.get(op["kind"], op["kind"]))}'
            "</span></td>"
            '<td data-label="Маршрут"><span class="ops-route">'
            f"{endpoint(op['from_fulfillment'], op['from_marketplace'], from_fallback)}"
            '<span class="ops-route-arrow" aria-hidden="true">→</span>'
            f"{endpoint(op['to_fulfillment'], op['to_marketplace'], to_fallback)}"
            "</span></td>"
            '<td data-label="Объём"><span class="ops-volume">'
            f'<strong class="ops-volume-value{unit_class}">{_fmt_num(units)} ед.</strong>'
            f"<small>{op['positions']} {_plural_positions(int(op['positions']))}</small>"
            "</span></td>"
            '<td data-label="Сотрудник"><span class="ops-person">'
            f"<strong>{html.escape(op['user_name'])}</strong>"
            f'<small title="{html.escape(detail, quote=True)}">{html.escape(detail)}</small>'
            "</span></td>"
            f'<td data-label="Выгрузка"><a class="ops-download" '
            f'href="/admin/operations/{op["id"]}/xlsx" title="Скачать Excel операции">'
            '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12"></path>'
            '<path d="m7 10 5 5 5-5"></path><path d="M5 21h14"></path></svg>'
            "<span>Excel</span></a></td>"
            "</tr>"
        )
    return "".join(rows)


def _history_kinds(kind: str) -> tuple[str, ...] | None:

    kind = (kind or "").strip()
    known = {k for k, _ in OPERATION_FILTERS if k}
    return (kind,) if kind in known else None


@router.get("/stock/{slug}/operations", response_class=HTMLResponse)
async def stock_store_operations(request: Request, slug: str, kind: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    kinds = _history_kinds(kind)
    active = kinds[0] if kinds else ""
    all_operations = await run_in_threadpool(
        db.get_store_operations, slug.lower(), None, settings.operation_history_limit
    )
    operations = (
        await run_in_threadpool(
            db.get_store_operations, slug.lower(), kinds, settings.operation_history_limit
        )
        if kinds
        else all_operations
    )
    counts = {"": len(all_operations)}
    for operation in all_operations:
        op_kind = operation.get("kind") or ""
        counts[op_kind] = counts.get(op_kind, 0) + 1

    content = fill_template(
        "operations_content.html",
        slug=slug.lower(),
        store_name=store["name"],
        store_color=store["color"],
        store_initials=store["initials"],
        store_text=store["text"],
        limit=str(settings.operation_history_limit),
        kind=active,
        kind_tabs=render_kind_tabs(slug.lower(), active, counts),
        summary=render_operation_summary(operations),
        rows=render_operation_rows(operations),
    )
    return render_page(
        f"CheckStock — Перемещение стока — {store['name']}",
        "stock",
        content,
        request.state.user,
        content_class="content--operations",
    )


@router.get("/stock/{slug}/operations/xlsx")
async def stock_store_operations_xlsx(slug: str, kind: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    kinds = _history_kinds(kind)

    def _build():
        operations = db.get_operations_with_items(slug.lower(), kinds, settings.operation_history_limit)
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
async def stock_store_xlsx(slug: str, mp: str = "", ff: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp if mp in db.MARKETPLACES else db.DEFAULT_MARKETPLACE
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

        columns = ["АРТИКУЛ", "ШТРИХКОД", "НАЗВАНИЕ", "ТОТАЛ", "ДОСТУПНО ФФ ДЛЯ РАСПРЕДЕЛЕНИЯ"]
        columns += [title.upper() for _scheme, title in schemes]

        rows = []
        totals = [0] * len(columns)
        for item in items:
            article = item["article"]
            ff_available = ff_map.get(article, 0) or 0

            by_scheme = []
            for scheme, _title in schemes:
                if scheme in fbs_maps:
                    by_scheme.append(fbs_maps[scheme].get(article, 0) or 0)
                else:
                    by_scheme.append(item[f"{scheme}_stock"] or 0)

            row_total = ff_available + sum(by_scheme)
            rows.append([article, item["barcode"], item["name"], row_total, ff_available, *by_scheme])

            for index, value in enumerate([row_total, ff_available, *by_scheme], start=3):
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
async def stock_store_warehouses_xlsx(slug: str, mp: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    marketplace = mp or db.DEFAULT_MARKETPLACE

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

    denied = _guard_stock_edit(request.state.user)
    if denied:
        return JSONResponse({"ok": False, "error": denied}, status_code=403)

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
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    marketplace = mp or db.DEFAULT_MARKETPLACE

    def build_content() -> str:
        return fill_template(
            "warehouse_content.html",
            store_name=store["name"],
            store_color=store["color"],
            store_text=store["text"],
            store_initials=store["initials"],
            slug=slug.lower(),
            marketplace=html.escape(marketplace),
            fbo_table=render_warehouse_table(
                db.get_mp_warehouse_details(slug.lower(), marketplace, "fbo"),
                f"Пока нет данных по складам {marketplace} — запустите синхронизацию на странице «Остатки»",
                top_n=settings.warehouse_display_limit,
            ),
            fbs_table=render_warehouse_table(
                _fbs_warehouse_rows(slug.lower(), marketplace),
                "Пока нет данных по складам продавца — запустите синхронизацию на странице «Остатки»",
            ),
            ff_table=render_warehouse_table(
                db.get_ff_warehouse_details_by_mp(slug.lower(), marketplace),
                "Пока нет остатков на фулфилментах — загрузите поставку на странице магазина",
            ),
            trash_table=render_trash_table(
                slug.lower(), marketplace, auth.can_edit_stock(request.state.user)
            ),
        )

    content = await run_in_threadpool(build_content)
    return render_page(
        f"CheckStock — Склады — {store['name']}",
        "stock",
        content,
        request.state.user,
        content_class="content--warehouses",
    )
