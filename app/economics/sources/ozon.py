"""Import Ozon 1C inputs from every OZON tab of the shared source sheet."""

import logging
import re
from collections import Counter, defaultdict

from app import db
from app.core.stores import STORES
from app.economics.sources import purchase_prices as source
from app.repositories import unit_economics_1c as repository

logger = logging.getLogger(__name__)
MARKETPLACE = "OZON"
SOURCE_COLUMNS = {
    "product_id": "артикулозон",
    "tag": "тег",
    "purchase_price": "себесруб",
    "fulfillment_cost": "прочзатрруб",
    "team_commission": "дрр",
}


def _find_header(rows: list[list[object]]) -> tuple[int, dict[str, int]]:
    required = set(SOURCE_COLUMNS.values())
    for index, row in enumerate(rows[:20]):
        columns = {source._header_key(value): offset for offset, value in enumerate(row)}
        if required.issubset(columns):
            return index, columns
    raise source.SourceDataError(
        "В листе OZON не найдены колонки: Артикул ОЗОН, Тег, Себес, руб, Проч.затр, руб, ДРР %"
    )


def _parse_tag(value: object) -> dict:
    raw = source._text(value)
    if not raw or raw.upper() in {"#N/A", "N/A", "-"}:
        raw = ""
    main, separator, extra = raw.partition("|")
    tag = source._split_tag(main)
    parts = [source._text(part) for part in main.split("/")]
    if len(parts) >= 4 and re.match(r"^W\d+", parts[2], re.IGNORECASE):
        tag["stock_status"] = parts[3] or None
        tag["stock_end_week"] = parts[2] or None
    external = source._split_supplier_external(extra if separator else "")
    return {**tag, **external, "tag_raw": raw or None}


def parse_source_values(sheets: list[dict], catalog: list[dict]) -> dict:
    by_product_id: dict[str, list[dict]] = defaultdict(list)
    for item in catalog:
        product_id = source._identifier(item.get("mp_product_id"))
        if product_id:
            by_product_id[product_id].append(item)

    parsed_by_item: dict[int, dict] = {}
    commissions_by_store: dict[str, Counter[float]] = defaultdict(Counter)
    source_rows = skipped_not_ozon = unmatched = ambiguous = duplicates = 0
    unmatched_examples: list[str] = []

    for sheet in sheets:
        title = source._text(sheet.get("title"))
        rows = list(sheet.get("rows") or [])
        header_index, columns = _find_header(rows)
        for source_row, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
            def cell(
                name: str,
                current_row: list[object] = row,
                current_columns: dict[str, int] = columns,
            ) -> object:
                column = current_columns[SOURCE_COLUMNS[name]]
                return current_row[column] if column < len(current_row) else None

            product_id = source._identifier(cell("product_id"))
            if not product_id:
                continue
            if source._header_key(product_id) == "нетнаозон":
                skipped_not_ozon += 1
                continue
            source_rows += 1
            candidates = by_product_id.get(product_id, [])
            if not candidates:
                unmatched += 1
                if len(unmatched_examples) < 10:
                    unmatched_examples.append(product_id)
                continue
            if len(candidates) > 1:
                ambiguous += 1
                continue
            item = candidates[0]
            parsed = {
                "stock_item_id": int(item["id"]),
                "manager": source._text(row[columns["менеджер"]]) or None
                if "менеджер" in columns and columns["менеджер"] < len(row)
                else None,
                "purchase_price": source._number(cell("purchase_price")),
                "fulfillment_cost": source._number(cell("fulfillment_cost")),
                "team_commission_percent": source._number(cell("team_commission")),
                **_parse_tag(cell("tag")),
                "source_sheet_id": int(sheet["sheet_id"]),
                "source_sheet_title": title,
                "source_row": source_row,
            }
            item_id = parsed["stock_item_id"]
            previous = parsed_by_item.get(item_id)
            if previous is not None:
                comparable = (
                    "purchase_price", "fulfillment_cost", "team_commission_percent",
                    "tag_raw", "manager",
                )
                if any(previous[key] != parsed[key] for key in comparable):
                    raise source.SourceDataError(
                        f"Разные данные Ozon для артикула {product_id}: "
                        f"{previous['source_sheet_title']}:{previous['source_row']} и {title}:{source_row}"
                    )
                duplicates += 1
                continue
            parsed_by_item[item_id] = parsed
            commission = parsed["team_commission_percent"]
            if commission is not None:
                commissions_by_store[str(item["store_slug"])][commission] += 1

    team_commissions = {
        store_slug: counts.most_common(1)[0][0]
        for store_slug, counts in commissions_by_store.items()
        if counts
    }
    commission_conflicts = {
        store_slug: dict(sorted(counts.items()))
        for store_slug, counts in commissions_by_store.items()
        if len(counts) > 1
    }
    return {
        "rows": list(parsed_by_item.values()),
        "team_commissions": team_commissions,
        "sheet_count": len(sheets),
        "source_rows": source_rows,
        "skipped_not_ozon": skipped_not_ozon,
        "matched": len(parsed_by_item),
        "unmatched": unmatched,
        "unmatched_examples": unmatched_examples,
        "ambiguous": ambiguous,
        "duplicates": duplicates,
        "commission_conflicts": commission_conflicts,
    }


def sync_all(sheets: list[dict] | None = None) -> dict:
    now = source._now()
    try:
        loaded_sheets = sheets if sheets is not None else source.fetch_sheet_rows(sheet_suffix="OZON")
        report = parse_source_values(loaded_sheets, repository.list_ozon_source_items())
        saved = repository.replace_source_values(
            report.pop("rows"), report.pop("team_commissions"), now, marketplace=MARKETPLACE
        )
    except Exception as error:
        for slug in STORES:
            db.record_sync_health(slug, MARKETPLACE, "unit_economics_1c_source", False, str(error), now)
        raise
    for slug in STORES:
        db.record_sync_health(slug, MARKETPLACE, "unit_economics_1c_source", True, None, now)
    logger.info(
        "ozon_source_sync sheets=%s saved=%s skipped_not_ozon=%s unmatched=%s",
        report["sheet_count"], saved, report["skipped_not_ozon"], report["unmatched"],
    )
    return {"ok": True, "saved": saved, "synced_at": now, **report}
