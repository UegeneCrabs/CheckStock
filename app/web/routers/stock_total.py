import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response

from app import stock_total as stock_total_service
from app.access_control import ActionPermission, profile_has_permission, scope_pairs
from app.stores import STORES
from app.web.access import accessible_store_slugs
from app.web.common import _fmt_num
from app.web.downloads import _download_headers
from app.web.identifiers import copy_identifier
from app.web.templating import fill_template, render_page

router = APIRouter()


def _quantity_cell(value: int) -> str:
    number = int(value or 0)
    return f'<td data-filter-value="{number}">{_fmt_num(number)}</td>'


def _render_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr class="empty-row"><td colspan="19">Пока нет товаров и остатков</td></tr>'

    result = []
    for row in rows:
        article = str(row.get("article") or "")
        barcode = str(row.get("barcode") or "")
        name = str(row.get("name") or article or "Без названия")
        quantities = [
            int(row["grand_total"] or 0),
            *(int(row[key] or 0) for key in stock_total_service.QUANTITY_KEYS),
        ]
        result.append(
            f'<tr data-store="{html.escape(str(row["store_slug"]), quote=True)}" '
            f'data-grand-total="{quantities[0]}">'
            f"<td>{copy_identifier(article, 'Артикул')}</td>"
            f"<td>{copy_identifier(barcode, 'Баркод')}</td>"
            f'<td title="{html.escape(name, quote=True)}">{html.escape(name)}</td>'
            + "".join(_quantity_cell(value) for value in quantities)
            + "</tr>"
        )
    return "".join(result)


def _render_totals(rows: list[dict]) -> str:
    values = [
        sum(int(row["grand_total"] or 0) for row in rows),
        *(sum(int(row[key] or 0) for row in rows) for key in stock_total_service.QUANTITY_KEYS),
    ]
    cells = "".join(
        f'<th data-total-column="{column}">{_fmt_num(value)}</th>'
        for column, value in enumerate(values, start=3)
    )
    return (
        '<tr class="totals-row">'
        "<th>ИТОГО</th><th></th>"
        f"<th data-total-positions>позиций: {len(rows)}</th>"
        f"{cells}</tr>"
    )


def _store_options(store_slugs: tuple[str, ...], selected_store: str = "") -> str:
    options = ['<option value="">Все магазины</option>']
    options.extend(
        f'<option value="{html.escape(slug, quote=True)}"'
        f'{" selected" if slug == selected_store else ""}>'
        f'{html.escape(STORES[slug]["name"])}</option>'
        for slug in store_slugs
    )
    return "".join(options)


def _selected_store(store: str, accessible: tuple[str, ...]) -> str:
    selected = store.strip().lower()
    if not selected:
        return ""
    if selected not in STORES:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if selected not in accessible:
        raise HTTPException(status_code=403, detail="Нет доступа к этому магазину")
    return selected


@router.get("/stock/total", response_class=HTMLResponse)
async def stock_total(request: Request, store: str = ""):
    if not profile_has_permission(request.state.user, ActionPermission.STOCK_TOTAL_VIEW):
        raise HTTPException(status_code=403, detail="Нет доступа к сводным остаткам")
    store_slugs = accessible_store_slugs(request.state.user)
    selected_store = _selected_store(store, store_slugs)
    rows = await run_in_threadpool(
        stock_total_service.build_rows,
        store_slugs,
        scope_pairs(request.state.user),
    )
    content = fill_template(
        "stock_total_content.html",
        store_options=_store_options(store_slugs, selected_store),
        download_href=(
            f"/stock/total.xlsx?store={html.escape(selected_store, quote=True)}"
            if selected_store
            else "/stock/total.xlsx"
        ),
        total_row=_render_totals(rows),
        rows=_render_rows(rows),
    )
    return render_page(
        "CheckStock — Остатки Тотал",
        "stock_total",
        content,
        request.state.user,
        content_class="content--stock-total",
    )


@router.get("/stock/total.xlsx")
async def stock_total_xlsx(request: Request, store: str = ""):
    if not profile_has_permission(request.state.user, ActionPermission.STOCK_TOTAL_EXPORT):
        raise HTTPException(status_code=403, detail="Нет доступа к выгрузке сводных остатков")
    store_slugs = accessible_store_slugs(request.state.user)
    selected_store = _selected_store(store, store_slugs)
    export_store_slugs = (selected_store,) if selected_store else store_slugs
    allowed_pairs = scope_pairs(request.state.user)
    if selected_store:
        allowed_pairs = tuple(
            pair for pair in allowed_pairs if pair[0] == selected_store
        )

    def build() -> tuple[bytes, str]:
        return stock_total_service.build_xlsx(
            stock_total_service.build_rows(export_store_slugs, allowed_pairs)
        )

    content, filename = await run_in_threadpool(build)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_download_headers(filename),
    )
