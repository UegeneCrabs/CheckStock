from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime

from app.catalog_identity import barcodes
from app.domain import MOSCOW_TIMEZONE
from app.repositories import stock_total as repository
from app.stores import STORES

MARKETPLACES = (
    ("WB", "wb", "ВБ"),
    ("OZON", "ozon", "ОЗОН"),
    ("YANDEX MARKET", "yandex", "ЯМ"),
)
STOCK_GROUPS = (
    ("ff", "ДОСТУПНО ФФ ДЛЯ РАСПРЕДЕЛЕНИЯ"),
    ("transit", "В ПУТИ МЕЖДУ ФФ"),
    ("fbs", "ТЕКУЩИЙ СТОК В ПРОДАЖЕ FBS"),
    ("rfbs", "ТЕКУЩИЙ СТОК В ПРОДАЖЕ RFBS"),
    ("fbo", "ТЕКУЩИЙ СТОК В ПРОДАЖЕ FBO"),
)
QUANTITY_KEYS = tuple(
    f"{group}_{marketplace_key}"
    for group, _group_label in STOCK_GROUPS
    for _marketplace, marketplace_key, _marketplace_label in MARKETPLACES
)
MARKETPLACE_TOTAL_KEYS = tuple(
    f"total_{marketplace_key}" for _marketplace, marketplace_key, _marketplace_label in MARKETPLACES
)
TOTAL_KEYS = ("grand_total", *MARKETPLACE_TOTAL_KEYS)
VALUE_KEYS = (*TOTAL_KEYS, *QUANTITY_KEYS)


def _normalized(value: object) -> str:
    return str(value or "").replace("\xa0", " ").strip().casefold()


def _scheme_group(scheme: object) -> str | None:
    normalized = _normalized(scheme)
    if normalized == "fbs" or normalized.startswith("fbs_"):
        return "fbs"
    if normalized in {"rfbs", "fbo"}:
        return normalized
    return None


def _empty_row(store_slug: str, identity: tuple[str, ...]) -> dict:
    row = {
        "store_slug": store_slug,
        "store_name": STORES.get(store_slug, {}).get("name", store_slug.upper()),
        "identity": identity,
        "article": "",
        "barcode": "",
        "name": "",
        "purchase_price": None,
        "grand_total": 0,
    }
    row.update({key: 0 for key in MARKETPLACE_TOTAL_KEYS})
    row.update({key: 0 for key in QUANTITY_KEYS})
    return row


def build_rows(
    store_slugs: tuple[str, ...],
    allowed_pairs: tuple[tuple[str, str], ...] | None = None,
    selected_store: str = "",
) -> list[dict]:
    """One row per seller article across authorized stores, with its shared purchase price."""
    sources = repository.get_source_rows(store_slugs)
    allowed = set(allowed_pairs) if allowed_pairs is not None else None
    catalog, marketplace_stock, fulfillment_stock, transit_stock = (
        [
            item
            for item in source
            if str(item["store_slug"]) in store_slugs
            and (allowed is None or (str(item["store_slug"]), str(item["marketplace"])) in allowed)
        ]
        for source in sources
    )
    marketplace_keys = {marketplace: key for marketplace, key, _label in MARKETPLACES}

    def item_codes(item: dict) -> list[str]:
        return sorted({_normalized(code) for code in barcodes(item)} - {"", "—", "-"})

    def identity_for(store_slug: str, marketplace: str, article: str) -> tuple[str, ...]:
        article_key = _normalized(article)
        return ("article", article_key) if article_key else ("missing_article", store_slug, marketplace)

    catalog_by_identity: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for item in catalog:
        identity = identity_for(str(item["store_slug"]), str(item["marketplace"]), str(item["article"]))
        catalog_by_identity[identity].append(item)

    quantities: dict[tuple[str, ...], dict[tuple[str, str], dict[str, int]]] = defaultdict(dict)
    fallback_articles: dict[tuple[str, ...], str] = {}

    def add(item: dict, group: str) -> None:
        store_slug = str(item["store_slug"])
        marketplace = str(item["marketplace"])
        key = marketplace_keys.get(marketplace)
        if key is None or (selected_store and store_slug != selected_store):
            return
        article = str(item["article"])
        identity = identity_for(store_slug, marketplace, article)
        values = quantities[identity].setdefault((store_slug, marketplace), {})
        quantity_key = f"{group}_{key}"
        values[quantity_key] = values.get(quantity_key, 0) + int(item.get("quantity") or 0)
        fallback_articles.setdefault(identity, article)

    for item in fulfillment_stock:
        add(item, "ff")
    for item in transit_stock:
        add(item, "transit")
    for item in marketplace_stock:
        group = _scheme_group(item.get("scheme"))
        if group:
            add(item, group)

    rows = []
    market_order = {marketplace: index for index, (marketplace, _, _) in enumerate(MARKETPLACES)}
    market_labels = {"WB": "WB", "OZON": "OZON", "YANDEX MARKET": "ЯМ"}

    for identity, contributions in quantities.items():
        active_pairs = [pair for pair, values in contributions.items() if any(values.values())]
        if not active_pairs:
            continue
        active_pairs.sort(key=lambda pair: (pair[0], market_order[pair[1]]))
        active_stores = sorted({pair[0] for pair in active_pairs})
        row = _empty_row(active_stores[0], identity)
        matches = catalog_by_identity.get(identity, [])
        active_matches = [
            item for item in matches if (str(item["store_slug"]), str(item["marketplace"])) in active_pairs
        ]
        representative = next(iter(active_matches or matches), {})
        row["article"] = str(representative.get("article") or fallback_articles[identity])
        codes = sorted({code for item in matches for code in item_codes(item)})
        row["barcode"] = str(representative.get("barcode") or (codes[0] if codes else ""))
        if row["barcode"] in {"—", "-"}:
            row["barcode"] = codes[0] if codes else ""
        row["barcodes"] = codes
        row["articles"] = sorted({str(item["article"]) for item in matches} | {row["article"]})
        row["name"] = str(representative.get("name") or row["article"])
        priced = [item for item in matches if item.get("purchase_price") is not None]
        if priced:
            row["purchase_price"] = max(float(item["purchase_price"]) for item in priced)
        row["store_slugs"] = active_stores
        row["store_name"] = ", ".join(
            STORES.get(slug, {}).get("name", slug.upper()) for slug in active_stores
        )
        row["store_marketplaces"] = ", ".join(
            f"{STORES.get(slug, {}).get('name', slug.upper())} {market_labels[marketplace]}"
            for slug, marketplace in active_pairs
        )
        for values in contributions.values():
            for key, quantity in values.items():
                row[key] += quantity
        for _marketplace, key, _label in MARKETPLACES:
            row[f"total_{key}"] = sum(row[f"{group}_{key}"] for group, _ in STOCK_GROUPS)
        row["grand_total"] = sum(row[key] for key in MARKETPLACE_TOTAL_KEYS)
        row.pop("identity")
        rows.append(row)
    rows.sort(key=lambda row: (-row["grand_total"], row["name"].casefold(), row["article"].casefold()))
    return rows


def build_xlsx(rows: list[dict]) -> tuple[bytes, str]:
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as error:
        raise RuntimeError(
            "для выгрузки в .xlsx нужен пакет openpyxl — установи его в .venv (pip install openpyxl)"
        ) from error

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Остатки Тотал"
    sheet.sheet_view.showGridLines = False

    fixed_headers = ("АРТИКУЛ", "ШТРИХКОД", "НАЗВАНИЕ", "ТЕКУЩАЯ ЗЦ, ₽", "МАГАЗИНЫ / ПЛОЩАДКИ")
    for column, title in enumerate(fixed_headers, start=1):
        sheet.cell(row=1, column=column, value=title)
        sheet.merge_cells(start_row=1, start_column=column, end_row=2, end_column=column)

    group_fills = {
        "total": "E4E7F7",
        "ff": "FFF2CC",
        "transit": "FCE4D6",
        "fbs": "DDEBF7",
        "rfbs": "E4DFEC",
        "fbo": "E2F0D9",
    }
    column = len(fixed_headers) + 1
    total_start = column
    total_end = column + len(TOTAL_KEYS) - 1
    sheet.cell(row=1, column=total_start, value="ТОТАЛ")
    sheet.merge_cells(start_row=1, start_column=total_start, end_row=1, end_column=total_end)
    for offset, label in enumerate(("ГРАНД ТОТАЛ", "ВБ", "ОЗОН", "ЯМ")):
        sheet.cell(row=2, column=total_start + offset, value=label)
    for row_number in (1, 2):
        for group_column in range(total_start, total_end + 1):
            sheet.cell(row=row_number, column=group_column).fill = PatternFill(
                "solid", fgColor=group_fills["total"]
            )
    column = total_end + 1
    for group, label in STOCK_GROUPS:
        start = column
        end = column + len(MARKETPLACES) - 1
        sheet.cell(row=1, column=start, value=label)
        sheet.merge_cells(start_row=1, start_column=start, end_row=1, end_column=end)
        for offset, (_marketplace, _key, marketplace_label) in enumerate(MARKETPLACES):
            sheet.cell(row=2, column=start + offset, value=marketplace_label)
        for row_number in (1, 2):
            for group_column in range(start, end + 1):
                sheet.cell(row=row_number, column=group_column).fill = PatternFill(
                    "solid", fgColor=group_fills[group]
                )
        column = end + 1

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="B7C3D0")
    medium = Side(style="medium", color="7F8C9A")
    for row_number in (1, 2):
        for cell in sheet[row_number]:
            if cell.column <= len(fixed_headers):
                cell.fill = header_fill
                cell.font = header_font
            else:
                cell.font = Font(bold=True, color="172033")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=medium)

    total_values = ["ИТОГО", "", f"позиций: {len(rows)}", None, ""]
    total_values.extend(sum(int(row.get(key) or 0) for row in rows) for key in VALUE_KEYS)
    sheet.append(total_values)
    priced_positions = sum(1 for row in rows if row.get("purchase_price") is not None)
    cost_values = ["ИТОГО В ЗЦ", "", f"ЗЦ: {priced_positions} из {len(rows)} поз.", None, ""]
    cost_values.extend(
        round(
            sum(
                float(row.get("purchase_price") or 0) * int(row.get(key) or 0)
                for row in rows
                if row.get("purchase_price") is not None
            ),
            2,
        )
        for key in VALUE_KEYS
    )
    sheet.append(cost_values)
    for total_row_number, fill_color in ((3, "FFF4E5"), (4, "EAF4EE")):
        for cell in sheet[total_row_number]:
            cell.font = Font(bold=True, color="172033")
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(left=thin, right=thin, bottom=medium)

    for row in rows:
        sheet.append(
            [
                str(row["article"]),
                str(row["barcode"]),
                row["name"],
                row.get("purchase_price"),
                row.get("store_marketplaces", row.get("store_name", "")),
                *(int(row.get(key) or 0) for key in VALUE_KEYS),
            ]
        )

    for row_number in range(5, sheet.max_row + 1):
        for column_number in range(1, sheet.max_column + 1):
            cell = sheet.cell(row=row_number, column=column_number)
            cell.border = Border(bottom=Side(style="hair", color="D9E1E8"))
            cell.alignment = Alignment(
                horizontal="left" if column_number in {1, 2, 3, 5} else "right",
                vertical="center",
                wrap_text=column_number in {3, 5},
            )
        sheet.cell(row=row_number, column=1).number_format = "@"
        sheet.cell(row=row_number, column=2).number_format = "@"
        sheet.cell(row=row_number, column=4).number_format = '#,##0.00 "₽"'
        for column_number in range(6, sheet.max_column + 1):
            sheet.cell(row=row_number, column=column_number).number_format = "#,##0"
    for column_number in range(6, sheet.max_column + 1):
        sheet.cell(row=3, column=column_number).number_format = "#,##0"
        sheet.cell(row=4, column=column_number).number_format = '#,##0.00 "₽"'

    widths = [18, 20, 54, 16, 40, 16, 12, 12, 12] + [12] * len(QUANTITY_KEYS)
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 38
    sheet.row_dimensions[2].height = 26
    sheet.row_dimensions[3].height = 24
    sheet.row_dimensions[4].height = 24
    sheet.freeze_panes = "J5"

    buffer = io.BytesIO()
    workbook.save(buffer)
    date_label = datetime.now(MOSCOW_TIMEZONE).strftime("%Y-%m-%d")
    return buffer.getvalue(), f"ostatki_total_{date_label}.xlsx"
