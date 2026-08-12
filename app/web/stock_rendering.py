import html

from app import db, health
from app.web.common import _cell, _fmt_num
from app.yandex import sync as ya_sync

READY_MARKETPLACES = {"WB", "OZON", "YANDEX MARKET"}


MARKETPLACE_SCHEMES = {
    "WB": [("fbs", "Текущий сток в продаже FBS"), ("fbo", "Текущий сток в продаже FBO")],
    "OZON": [
        ("fbs", "Текущий сток в продаже FBS"),
        ("rfbs", "Текущий сток в продаже rFBS"),
        ("fbo", "Текущий сток в продаже FBO"),
    ],
    "YANDEX MARKET": [("fbo", "FBY — склады Маркета")],
}


def schemes_for(marketplace: str, store_slug: str = "") -> list[tuple[str, str]]:

    if marketplace == "YANDEX MARKET" and store_slug:
        schemes = ya_sync.store_schemes(store_slug)
        if schemes:
            return schemes
    return MARKETPLACE_SCHEMES.get(marketplace, MARKETPLACE_SCHEMES["WB"])


def render_stock_head(marketplace: str, store_slug: str = "") -> str:

    schemes = schemes_for(marketplace, store_slug)
    items = db.get_stock_items(store_slug, marketplace, tuple(key for key, _ in schemes))
    ff_total = sum(item["ff_available"] or 0 for item in items)
    scheme_totals = {scheme: sum(item[f"{scheme}_stock"] or 0 for item in items) for scheme, _ in schemes}
    grand_total = ff_total + sum(scheme_totals.values())

    cells = [
        '<th class="col-product"><span class="stock-head-heading">'
        '<span class="stock-head-label">Товар / артикул</span>'
        '<strong class="stock-head-total stock-head-total--label">Итого</strong></span></th>',
        '<th class="col-row-total"><span class="stock-head-heading">'
        '<span class="stock-head-label">Тотал</span>'
        f'<strong class="stock-head-total tot-grand">{_fmt_num(grand_total)}</strong></span></th>',
        '<th class="col-ff-available"><span class="stock-head-heading">'
        '<span class="stock-head-label">Доступно ФФ для распределения</span>'
        f'<strong class="stock-head-total tot-ff">{_fmt_num(ff_total)}</strong></span></th>',
    ]
    cells += [
        f'<th class="col-scheme col-{scheme}"><span class="stock-head-heading">'
        f'<span class="stock-head-label">{html.escape(title)}</span>'
        f'<strong class="stock-head-total tot-{scheme}">{_fmt_num(scheme_totals[scheme])}</strong>'
        "</span></th>"
        for scheme, title in schemes
    ]
    cells.append('<th class="col-filler"></th>')
    return "<tr>" + "".join(cells) + "</tr>"


def render_ff_options() -> str:
    return "\n".join(
        f'                    <option value="{html.escape(name)}">{html.escape(name)}</option>'
        for name in db.get_fulfillments()
    )


UNALLOCATED_SUFFIX = "не распределено"


def marketplace_move_label(name: str) -> str:
    return f"{name} — {UNALLOCATED_SUFFIX}"


def render_mp_move_options() -> str:

    return "\n".join(
        f'                                <option value="{html.escape(name)}"'
        f"{' selected' if name == db.DEFAULT_MARKETPLACE else ''}>"
        f"{html.escape(marketplace_move_label(name))}</option>"
        for name in db.MARKETPLACES
    )


def marketplace_ready(marketplace: str, store_slug: str) -> bool:

    if marketplace not in READY_MARKETPLACES:
        return False
    return health.has_token(marketplace, store_slug)


def render_mp_tabs(active: str, store_slug: str = "") -> str:

    tabs = []
    for name in db.MARKETPLACES:
        ready = marketplace_ready(name, store_slug)
        classes = "mp-tab" + (" active" if name == active else "")
        classes += "" if ready else " mp-tab--nokey"
        tabs.append(
            f'            <button class="{classes}" type="button" role="tab" '
            f'data-mp="{html.escape(name)}" data-ready="{"1" if ready else "0"}">'
            f"{html.escape(name)}</button>"
        )
    return "\n".join(tabs)


def render_product_cell(
    article: str, barcode: str, name: str, image_url: str = "", cell_class: str = "col-product"
) -> str:

    article = str(article or "")
    barcode = str(barcode or "")
    name = str(name or article)
    image_url = str(image_url or "").strip()
    if not image_url.startswith(("https://", "http://")):
        image_url = ""

    initial = (name[:1] or article[:1] or "?").upper()
    tone = sum(ord(char) for char in article) % 5
    image = (
        f'<img class="stock-product-image" src="{html.escape(image_url, quote=True)}" '
        'alt="" loading="lazy" decoding="async">'
        if image_url
        else ""
    )
    article_copy = (
        '<button class="stock-copy" type="button" data-copy-kind="Артикул" '
        f'data-copy-value="{html.escape(article, quote=True)}" '
        f'aria-label="Скопировать артикул {html.escape(article, quote=True)}">'
        f"<span>Арт.</span> {html.escape(article)}</button>"
    )
    barcode_copy = (
        '<button class="stock-copy" type="button" data-copy-kind="Баркод" '
        f'data-copy-value="{html.escape(barcode, quote=True)}" '
        f'aria-label="Скопировать баркод {html.escape(barcode, quote=True)}">'
        f"<span>Баркод</span> {html.escape(barcode)}</button>"
        if barcode
        else '<span class="stock-code-empty">Баркод —</span>'
    )
    return (
        f'<td class="{html.escape(cell_class, quote=True)}"><div class="stock-product">'
        f'<span class="stock-product-media stock-product-media--{tone}" aria-hidden="true">'
        f"<span>{html.escape(initial)}</span>{image}</span>"
        '<span class="stock-product-copy">'
        f'<strong class="stock-product-name" title="{html.escape(name, quote=True)}">'
        f"{html.escape(name)}</strong>"
        f'<span class="stock-product-meta">{article_copy}'
        '<span class="stock-meta-separator" aria-hidden="true">·</span>'
        f"{barcode_copy}</span></span></div></td>"
    )


def render_stock_rows(store_slug: str, marketplace: str) -> str:
    schemes = schemes_for(marketplace, store_slug)
    items = db.get_stock_items(store_slug, marketplace, tuple(k for k, _ in schemes))
    if not items:
        colspan = 4 + len(schemes)
        return (
            f'                            <tr class="empty-row"><td colspan="{colspan}">'
            "Пока нет остатков по этому магазину</td></tr>"
        )

    rows = []
    for item in items:
        ff_available = item["ff_available"] or 0

        by_scheme = {scheme: (item[f"{scheme}_stock"] or 0) for scheme, _ in schemes}
        sale_total = sum(by_scheme.values())
        row_total = ff_available + sale_total

        stuck = ff_available > 0 and sale_total == 0
        row_class = ' class="row-alert"' if stuck else ""
        article = str(item["article"] or "")
        barcode = str(item["barcode"] or "")
        name = str(item["name"] or article)
        product_cell = render_product_cell(article, barcode, name, str(item.get("image_url") or ""))
        rows.append(
            f'                            <tr{row_class} data-article="{html.escape(article, quote=True)}" '
            f'tabindex="0" aria-label="Открыть карточку {html.escape(name, quote=True)}">'
            + product_cell
            + f'<td class="col-row-total">{_cell(row_total)}</td>'
            f'<td class="col-ff-available">{_cell(ff_available)}</td>'
            + "".join(
                f'<td class="col-scheme col-{scheme}">{_cell(by_scheme[scheme])}</td>'
                for scheme, _ in schemes
            )
            + f'<td class="col-filler"><button class="stock-row-open" type="button" '
            f'aria-label="Открыть карточку {html.escape(name, quote=True)}">›</button></td>'
            "</tr>"
        )
    return "\n".join(rows)


def render_stock_totals(store_slug: str, marketplace: str) -> str:

    schemes = schemes_for(marketplace, store_slug)
    items = db.get_stock_items(store_slug, marketplace, tuple(k for k, _ in schemes))

    ff_total = sum(item["ff_available"] or 0 for item in items)
    scheme_totals = {scheme: sum(item[f"{scheme}_stock"] or 0 for item in items) for scheme, _ in schemes}
    grand_total = ff_total + sum(scheme_totals.values())

    return (
        '<tr class="totals-row">'
        '<th class="totals-label">Итого</th>'
        f'<th class="col-row-total tot-grand">{_fmt_num(grand_total)}</th>'
        f'<th class="col-ff-available tot-ff">{_fmt_num(ff_total)}</th>'
        + "".join(
            f'<th class="col-scheme tot-{scheme}">{_fmt_num(scheme_totals[scheme])}</th>'
            for scheme, _ in schemes
        )
        + '<th class="col-filler"></th>'
        "</tr>"
    )


OTHER_WAREHOUSES_LABEL = "Другие склады"


def _pick_top_warehouses(rows_data: list[dict], top_n: int) -> tuple[list[str], set[str]]:

    totals: dict[str, int] = {}
    for row in rows_data:
        totals[row["warehouse"]] = totals.get(row["warehouse"], 0) + (row["quantity"] or 0)

    if len(totals) <= top_n:
        return sorted(totals), set()

    ranked = sorted(totals, key=lambda w: (-totals[w], w))
    top = ranked[:top_n]
    return top, set(ranked[top_n:])


def render_trash_table(store_slug: str, marketplace: str, can_edit: bool) -> str:

    rows_data = db.get_trash_details(store_slug, marketplace)
    if not rows_data:
        return (
            '<table class="data-table data-table--trash">'
            "<thead><tr><th>Нет данных</th></tr></thead>"
            '<tbody><tr class="empty-row">'
            "<td>Мусорка пуста — потерянного товара по этому маркетплейсу нет</td>"
            "</tr></tbody></table>"
        )

    total = sum(int(r["quantity"] or 0) for r in rows_data)
    body = []
    for row in rows_data:
        checked = bool(row.get("checked"))
        quantity = int(row["quantity"] or 0)

        qty_class = "trash-qty trash-qty--surplus" if quantity < 0 else "trash-qty"
        body.append(
            f'<tr class="{"is-checked" if checked else ""}">'
            + render_product_cell(
                row["article"],
                row["barcode"] or "",
                row["name"] or "",
                str(row.get("image_url") or ""),
            )
            + f'<td data-label="Склад">{html.escape(row["warehouse"] or "")}</td>'
            f'<td data-label="Количество" class="{qty_class}">{_fmt_num(quantity)}</td>'
            f'<td data-label="Проконтролировано">'
            f'<label class="trash-check">'
            f'<input type="checkbox" class="trash-checkbox"'
            f' data-article="{html.escape(row["article"], quote=True)}"'
            f' data-warehouse="{html.escape(row["warehouse"] or "", quote=True)}"'
            f"{' checked' if checked else ''}{'' if can_edit else ' disabled'}>"
            f"</label></td>"
            "</tr>"
        )

    return (
        '<table class="data-table data-table--trash" data-table-filter>'
        "<thead><tr>"
        '<th class="col-product"><span class="stock-head-heading">'
        '<span class="stock-head-label">Товар / артикул</span>'
        '<strong class="stock-head-total stock-head-total--label">Итого</strong></span></th>'
        '<th><span class="stock-head-heading"><span class="stock-head-label">Склад</span>'
        '<strong class="stock-head-total stock-head-total--empty" aria-hidden="true">0</strong></span></th>'
        '<th><span class="stock-head-heading"><span class="stock-head-label">Количество</span>'
        f'<strong class="stock-head-total tot-grand">{_fmt_num(total)}</strong></span></th>'
        '<th><span class="stock-head-heading"><span class="stock-head-label">Проконтролировано</span>'
        '<strong class="stock-head-total stock-head-total--empty" aria-hidden="true">0</strong></span></th>'
        "</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def render_warehouse_table(rows_data: list[dict], empty_text: str, top_n: int | None = None) -> str:

    if not rows_data:
        return (
            '<div class="warehouse-table-block">'
            '<div class="table-wrap table-wrap--scroll-10">'
            '<table class="data-table data-table--warehouses">'
            "<thead><tr><th>Нет данных</th></tr></thead>"
            '<tbody><tr class="empty-row">'
            f"<td>{html.escape(empty_text)}</td>"
            "</tr></tbody></table></div></div>"
        )

    all_warehouses = {row["warehouse"] for row in rows_data}
    other: set[str] = set()
    if top_n:
        warehouse_list, other = _pick_top_warehouses(rows_data, top_n)
    else:
        warehouse_list = sorted({row["warehouse"] for row in rows_data})

    products: dict[tuple[str, str, str, str], dict[str, int]] = {}

    for row in rows_data:
        key = (
            row["barcode"],
            row["article"],
            row["name"],
            str(row.get("image_url") or ""),
        )
        column = OTHER_WAREHOUSES_LABEL if row["warehouse"] in other else row["warehouse"]
        cells = products.setdefault(key, {})
        cells[column] = cells.get(column, 0) + (row["quantity"] or 0)

    if other:
        warehouse_list = warehouse_list + [OTHER_WAREHOUSES_LABEL]

    column_totals: dict[str, int] = {}
    for cells in products.values():
        for column, quantity in cells.items():
            column_totals[column] = column_totals.get(column, 0) + quantity
    grand_total = sum(column_totals.values())

    header_parts = []
    for warehouse in warehouse_list:
        is_other = warehouse == OTHER_WAREHOUSES_LABEL and bool(other)
        cell_class = "col-warehouse col-other" if is_other else "col-warehouse"
        title_text = f"Сумма по {len(other)} складам вне топ-{top_n}" if is_other else warehouse
        title = f' title="{html.escape(title_text, quote=True)}"'
        header_parts.append(
            f'<th class="{cell_class}"{title}><span class="stock-head-heading">'
            f'<span class="stock-head-label">{html.escape(warehouse)}</span>'
            f'<strong class="stock-head-total tot-warehouse">'
            f"{_fmt_num(column_totals.get(warehouse, 0))}</strong></span></th>"
        )
    header_cells = "".join(header_parts)

    thead = (
        "<thead><tr>"
        '<th class="col-product"><span class="stock-head-heading">'
        '<span class="stock-head-label">Товар / артикул</span>'
        '<strong class="stock-head-total stock-head-total--label">Итого</strong></span></th>'
        '<th class="col-total"><span class="stock-head-heading">'
        '<span class="stock-head-label">Тотал</span>'
        f'<strong class="stock-head-total tot-grand">{_fmt_num(grand_total)}</strong></span></th>'
        f"{header_cells}"
        "</tr></thead>"
    )

    body_rows = []
    for (barcode, article, name, image_url), by_warehouse in products.items():
        total = sum(by_warehouse.values())
        cells = "".join(
            f'<td class="col-warehouse col-other">{by_warehouse.get(w, "—")}</td>'
            if w == OTHER_WAREHOUSES_LABEL and other
            else f'<td class="col-warehouse">{by_warehouse.get(w, "—")}</td>'
            for w in warehouse_list
        )
        body_rows.append(
            "<tr>"
            + render_product_cell(article, barcode, name, image_url)
            + f'<td class="col-total">{total}</td>'
            f"{cells}"
            "</tr>"
        )

    density = ""
    if len(warehouse_list) > 14:
        density = " is-dense-2"
    elif len(warehouse_list) > 7:
        density = " is-dense"

    min_width = 460 + len(warehouse_list) * 106
    folded = (
        f'<span class="warehouse-summary-note">Показан топ-{top_n}; остальные склады собраны отдельно</span>'
        if other and top_n
        else ""
    )
    return (
        '<div class="warehouse-table-block">'
        '<div class="warehouse-summary">'
        f"<span><small>Товаров</small><strong>{_fmt_num(len(products))}</strong></span>"
        f"<span><small>Складов</small><strong>{_fmt_num(len(all_warehouses))}</strong></span>"
        f"<span><small>Единиц</small><strong>{_fmt_num(grand_total)}</strong></span>"
        f"{folded}</div>"
        '<div class="table-wrap table-wrap--scroll-10">'
        f'<table class="data-table data-table--warehouses{density}" '
        f'style="--warehouse-min-width:{min_width}px">'
        f"{thead}<tbody>{''.join(body_rows)}</tbody>"
        "</table></div></div>"
    )
