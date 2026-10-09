import html
import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app import db
from app.access.access_control import ActionPermission, has_action_permission, scope_pairs
from app.config import settings
from app.core.formatting import format_dt
from app.core.stores import STORES
from app.ff_import import export as ff_export
from app.web.common import _fmt_num
from app.web.downloads import _download_headers
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
    ("fbs_transfer", "На FBS"),
    ("shipment", "Отгрузки"),
    ("manual_add", "Ручные докладки"),
]

TRANSFER_KINDS = (
    "transfer",
    "transfer_dispatch",
    "transfer_receive",
    "transfer_receive_revert",
    "transfer_cancel",
)
SHIPMENT_KINDS = ("shipment", "fbo_dispatch", "fbo_receive", "fbo_receive_revert", "fbo_cancel")


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


def render_operation_summary(summary: dict) -> str:
    return (
        '<div class="ops-summary" role="list">'
        f'<div role="listitem"><span>Операций</span><strong>{_fmt_num(summary["total"])}</strong></div>'
        f'<div role="listitem"><span>Товарных позиций</span><strong>{_fmt_num(summary["positions"])}</strong></div>'
        f'<div role="listitem"><span>Движение, ед.</span><strong>{_fmt_num(summary["units"])}</strong></div>'
        f'<div role="listitem"><span>Сотрудников</span><strong>{_fmt_num(summary["employees"])}</strong></div>'
        "</div>"
    )


def render_operation_pagination(slug: str, kind: str, page: int, page_size: int, total: int) -> str:
    pages = max(1, (total + page_size - 1) // page_size)
    first = (page - 1) * page_size + 1 if total else 0
    last = min(page * page_size, total)

    def link(target: int, label: str, title: str) -> str:
        query = urlencode({"kind": kind, "page": target})
        href = html.escape(f"/stock/{slug}/operations?{query}", quote=True)
        return f'<a class="ops-page-link" href="{href}" aria-label="{title}" title="{title}">{label}</a>'

    def arrow(target: int, title: str, direction: str, disabled: bool) -> str:
        path = "m14 6-6 6 6 6" if direction == "previous" else "m10 6 6 6-6 6"
        icon = f'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="{path}"/></svg>'
        if disabled:
            return (
                f'<button class="ops-page-link" type="button" disabled aria-label="{title}">{icon}</button>'
            )
        return link(target, icon, title)

    controls = []
    if pages > 1:
        controls.append(arrow(page - 1, "Предыдущая страница", "previous", page == 1))
        visible_pages = set(range(1, pages + 1)) if pages <= 7 else {1, pages, page - 1, page, page + 1}
        if pages > 7 and page <= 3:
            visible_pages.update(range(1, 6))
        if pages > 7 and page >= pages - 2:
            visible_pages.update(range(pages - 4, pages + 1))
        previous = 0
        for number in sorted(number for number in visible_pages if 1 <= number <= pages):
            if previous and number > previous + 1:
                controls.append('<span class="ops-page-gap" aria-hidden="true">…</span>')
            if number == page:
                controls.append(
                    f'<span class="ops-page-link" aria-current="page" aria-label="Страница {number}">{number}</span>'
                )
            else:
                controls.append(link(number, str(number), f"Страница {number}"))
            previous = number
        controls.append(arrow(page + 1, "Следующая страница", "next", page == pages))
    range_label = (
        f"Записи <strong>{_fmt_num(first)}–{_fmt_num(last)}</strong> из <strong>{_fmt_num(total)}</strong>"
        if total
        else "Пока нет операций"
    )
    return (
        '<nav class="ops-pagination" aria-label="Страницы истории операций">'
        f'<span class="ops-pagination-range">{range_label}</span>'
        '<div class="ops-pagination-navigation">'
        f'<span class="ops-pagination-position">Страница {page} из {pages}</span>'
        f'<div class="ops-pagination-controls">{"".join(controls)}</div></div></nav>'
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
        detail = " · ".join(part for part in (note, source) if part) or "Без примечания"
        kind = op["kind"] if op["kind"] in db.OPERATION_LABELS else "other"
        created = format_dt(op["created_at"])
        created_parts = created.split(" ", 1)
        date = created_parts[0]
        time = created_parts[1] if len(created_parts) > 1 else ""
        from_fallback = (
            "Поставка" if kind == "delivery" else ("Ручной ввод" if kind == "manual_add" else "Не указано")
        )
        to_fallback = (
            "Отгрузка"
            if kind == "shipment"
            else ("FBS" if kind == "fbs_transfer" else ("Мусорка" if kind == "trash" else "Не указано"))
        )
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
    if kind == "transfer":
        return TRANSFER_KINDS
    if kind == "shipment":
        return SHIPMENT_KINDS
    return (kind,) if kind in known else None


@router.get("/stock/{slug}/operations", response_class=HTMLResponse)
async def stock_store_operations(
    request: Request, slug: str, kind: str = "", page: Annotated[int, Query(ge=1)] = 1
):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    kinds = _history_kinds(kind)
    active = kind if kinds else ""
    marketplaces = tuple(
        marketplace
        for store_slug, marketplace in scope_pairs(request.state.user)
        if store_slug == slug.lower()
    )
    stats = await run_in_threadpool(
        db.get_store_operation_stats, slug.lower(), kinds, marketplaces=marketplaces
    )
    page_size = settings.operation_history_limit
    total = stats["summary"]["total"]
    page = min(page, max(1, (total + page_size - 1) // page_size))
    operations = await run_in_threadpool(
        db.get_store_operations,
        slug.lower(),
        kinds,
        page_size,
        offset=(page - 1) * page_size,
        marketplaces=marketplaces,
    )
    counts = stats["counts"]
    counts[""] = sum(counts.values())
    counts["transfer"] = sum(counts.get(item, 0) for item in TRANSFER_KINDS)
    counts["shipment"] = sum(counts.get(item, 0) for item in SHIPMENT_KINDS)

    content = fill_template(
        "stock/operations.html",
        slug=slug.lower(),
        store_name=store["name"],
        kind=active,
        kind_tabs=render_kind_tabs(slug.lower(), active, counts),
        summary=render_operation_summary(stats["summary"]),
        rows=render_operation_rows(operations),
        pagination=render_operation_pagination(slug.lower(), active, page, page_size, total),
    )
    return render_page(
        f"CheckStock — Перемещение стока — {store['name']}",
        "stock_operations",
        content,
        request.state.user,
        content_class="content--operations",
    )


@router.get("/stock/{slug}/operations/xlsx")
async def stock_store_operations_xlsx(request: Request, slug: str, kind: str = ""):

    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")

    kinds = _history_kinds(kind)
    if not any(
        store_slug == slug.lower()
        and has_action_permission(
            request.state.user,
            ActionPermission.STOCK_OPERATIONS_EXPORT,
            store_slug=store_slug,
            marketplace=marketplace,
        )
        for store_slug, marketplace in scope_pairs(request.state.user)
    ):
        raise HTTPException(status_code=403, detail="Нет доступа к выгрузке операций")
    marketplaces = tuple(
        marketplace
        for store_slug, marketplace in scope_pairs(request.state.user)
        if store_slug == slug.lower()
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
    if not has_action_permission(
        request.state.user,
        ActionPermission.STOCK_OPERATIONS_EXPORT,
        store_slug=slug.lower(),
        marketplace=marketplace,
    ):
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
    if not has_action_permission(
        request.state.user,
        ActionPermission.STOCK_OPERATIONS_EXPORT,
        store_slug=slug.lower(),
        marketplace=marketplace,
    ):
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

    if not has_action_permission(
        request.state.user,
        ActionPermission.STOCK_WRITEOFF,
        store_slug=slug.lower(),
        marketplace=marketplace.strip(),
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
    store = STORES.get(slug.lower())
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    marketplace = mp or db.DEFAULT_MARKETPLACE
    if not has_action_permission(
        request.state.user,
        ActionPermission.STOCK_BALANCE_VIEW,
        store_slug=slug.lower(),
        marketplace=marketplace,
    ):
        raise HTTPException(status_code=403, detail="Нет доступа к этой площадке")

    def build_content() -> str:
        return fill_template(
            "stock/warehouses.html",
            store_name=store["name"],
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
                slug.lower(),
                marketplace,
                has_action_permission(
                    request.state.user,
                    ActionPermission.STOCK_WRITEOFF,
                    store_slug=slug.lower(),
                    marketplace=marketplace,
                ),
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
