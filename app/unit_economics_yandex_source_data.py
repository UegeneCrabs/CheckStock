"""Import the single YM sheet using explicit store and ARTICLE identifiers."""

import logging
from collections import Counter, defaultdict

from app import db
from app import unit_economics_1c_source_data as source
from app.repositories import yandex_source_values as repository
from app.stores import STORES

logger = logging.getLogger(__name__)
MARKETPLACE = "YANDEX MARKET"
SHEET_TITLE = "YM"
SOURCE_COLUMNS = {**source.SOURCE_COLUMNS, "article": "article", "store": "магазин"}
STORE_ALIASES = {
    **{source._header_key(slug): slug for slug in STORES},
    **{source._header_key(store["name"]): slug for slug, store in STORES.items()},
    "хочушар": "rimili",
    "bth": "trusthome",
    "гоголь": "gogol",
}


def _number(value: object) -> float | None:

    return source._number(str(value) if value is not None else None)


def _find_header(rows: list[list[object]]) -> tuple[int, dict[str, int]]:
    for header_index, row in enumerate(rows[:20]):
        columns = {source._header_key(value): index for index, value in enumerate(row)}
        if set(SOURCE_COLUMNS.values()).issubset(columns):
            return header_index, columns
    raise source.SourceDataError("В листе YM не найдены обязательные колонки: Магазин, ARTICLE и данные 1С")


def parse_source_values(sheets: list[dict]) -> dict:
    if len(sheets) != 1 or source._text(sheets[0].get("title")).upper() != SHEET_TITLE:
        raise source.SourceDataError("Для ЯМ нужен ровно один лист YM")
    sheet = sheets[0]
    rows = list(sheet.get("rows") or [])
    header_index, columns = _find_header(rows)
    parsed_by_item = {}
    commissions_by_store = defaultdict(Counter)
    source_rows = duplicates = 0
    for source_row, row in enumerate(rows[header_index + 1 :], start=header_index + 2):

        def cell(name, current_row=row):
            column = columns.get(SOURCE_COLUMNS.get(name, source.OPTIONAL_SOURCE_COLUMNS.get(name)))
            return current_row[column] if column is not None and column < len(current_row) else None

        article = source._identifier(cell("article"))
        if not article:
            continue
        source_rows += 1
        store_slug = STORE_ALIASES.get(source._header_key(cell("store")))
        if not store_slug:
            raise source.SourceDataError(
                f"YM, строка {source_row}: неизвестный магазин {source._text(cell('store'))!r}"
            )
        parsed = {
            "store_slug": store_slug,
            "article": article,
            "manager": source._text(cell("manager")) or None,
            "purchase_price": _number(cell("purchase_price")),
            "fulfillment_cost": _number(cell("fulfillment_cost")),
            "team_commission_percent": _number(cell("team_commission")),
            **source._split_tag(cell("tag")),
            **source._split_supplier_external(cell("supplier_external")),
            "source_sheet_id": int(sheet["sheet_id"]),
            "source_sheet_title": SHEET_TITLE,
            "source_row": source_row,
        }
        key = (store_slug, article)
        if key in parsed_by_item:
            previous = parsed_by_item[key]
            if any(previous[field] != value for field, value in parsed.items() if field != "source_row"):
                raise source.SourceDataError(
                    f"YM: разные данные для {store_slug} / {article} в строках {previous['source_row']} и {source_row}"
                )
            duplicates += 1
            continue
        parsed_by_item[key] = parsed
        if parsed["team_commission_percent"] is not None:
            commissions_by_store[store_slug][parsed["team_commission_percent"]] += 1
    return {
        "rows": list(parsed_by_item.values()),
        "team_commissions": {
            slug: counts.most_common(1)[0][0] for slug, counts in commissions_by_store.items()
        },
        "commission_conflicts": {
            slug: dict(sorted(counts.items()))
            for slug, counts in commissions_by_store.items()
            if len(counts) > 1
        },
        "sheet_count": 1,
        "source_rows": source_rows,
        "matched": len(parsed_by_item),
        "unmatched": 0,
        "duplicates": duplicates,
    }


def sync_all(sheets: list[dict] | None = None) -> dict:
    now = source._now()
    try:
        report = parse_source_values(sheets if sheets is not None else source.fetch_sheet_rows(SHEET_TITLE))
        saved = repository.replace_values(report.pop("rows"), report.pop("team_commissions"), now)
    except Exception as error:
        for slug in STORES:
            db.record_sync_health(slug, MARKETPLACE, "unit_economics_1c_source", False, str(error), now)
        raise
    for slug in STORES:
        db.record_sync_health(slug, MARKETPLACE, "unit_economics_1c_source", True, None, now)
    logger.info("yandex_source_sync saved=%s source_rows=%s", saved, report["source_rows"])
    return {"ok": True, "saved": saved, "synced_at": now, **report}
