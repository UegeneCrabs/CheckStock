from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from app import db
from app.ff_import.importer import (
    FFImportError,
    fetch_google_sheet_rows,
    fetch_google_sheet_rows_via_api,
)
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

ABC_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1uP5RZioJgAoX-LTkXay7nbZvp_tk2EPAl0hCAeM8fdE/edit?gid=516789637"
)
COMMISSION_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1QZjTjvK1Ku2rsOI341msYvIJGP6tmG0AKB6Cxjnmtjs/edit?gid=1368403638"
)
REFERENCE_REFRESH_DAYS = 7


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").strip().split())


def _identifier(value: object) -> str:
    normalized = _text(value).replace(" ", "").lstrip("'")
    return normalized[:-2] if normalized.endswith(".0") and normalized[:-2].isdigit() else normalized


def _category_key(value: object) -> str:
    return _text(value).casefold().replace("ё", "е")


def _abc_code(value: object) -> str | None:
    code = _text(value).upper()
    return code or None


def _turnover_days(code: str | None) -> int:
    if code == "A":
        return 30
    if code == "B":
        return 28
    return 21


def _number(value: object) -> float:
    return float(_text(value).replace(" ", "").replace("%", "").replace(",", "."))


def _sheet_rows(url: str) -> list[list[str]]:
    try:
        return fetch_google_sheet_rows(url)
    except FFImportError:
        rows, _ = fetch_google_sheet_rows_via_api(url)
        return rows


def _header(rows: list[list[object]], required: set[str]) -> tuple[int, dict[str, int]]:
    for row_index, row in enumerate(rows[:20]):
        columns = {_category_key(value): index for index, value in enumerate(row)}
        if required.issubset(columns):
            return row_index, columns
    raise ValueError(f"В Google-таблице не найдены колонки: {', '.join(sorted(required))}")


def parse_classification_rows(rows: list[list[object]]) -> list[dict]:
    header_index, columns = _header(rows, {"артикул", "баркод", "код"})
    result: list[dict] = []
    for source_row, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        article = _identifier(row[columns["артикул"]]) if columns["артикул"] < len(row) else ""
        barcode = _identifier(row[columns["баркод"]]) if columns["баркод"] < len(row) else ""
        code = _abc_code(row[columns["код"]]) if columns["код"] < len(row) else None
        if article or barcode:
            result.append(
                {
                    "source_article": article or None,
                    "source_barcode": barcode or None,
                    "abc_code": code,
                    "source_row": source_row,
                }
            )
    return result


def parse_commission_rows(rows: list[list[object]]) -> list[dict]:
    header_index, columns = _header(rows, {"категория", "комиссия wb, %"})
    by_key: dict[str, dict] = {}
    for row in rows[header_index + 1 :]:
        category = _text(row[columns["категория"]]) if columns["категория"] < len(row) else ""
        raw_percent = row[columns["комиссия wb, %"]] if columns["комиссия wb, %"] < len(row) else ""
        if not category or not _text(raw_percent):
            continue
        category_key = _category_key(category)
        commission_percent = round(_number(raw_percent), 4)
        previous = by_key.get(category_key)
        if previous and previous["commission_percent"] != commission_percent:
            raise ValueError(f"Для категории «{category}» в справочнике указаны разные комиссии")
        by_key[category_key] = {
            "category_key": category_key,
            "category": category,
            "commission_percent": commission_percent,
        }
    return list(by_key.values())


def sync_product_classifications(rows: list[list[object]] | None = None) -> dict:
    logger.info("Коды товаров: читаем Google-таблицу")
    source = parse_classification_rows(rows if rows is not None else _sheet_rows(ABC_SHEET_URL))
    catalog = db.list_unit_economics_1c_active_wb_stock_items()
    logger.info("Коды товаров: строк в таблице=%s, товаров в базе=%s", len(source), len(catalog))
    by_identifier: dict[str, list[dict]] = defaultdict(list)
    for source_row in source:
        for identifier in {source_row.get("source_article"), source_row.get("source_barcode")}:
            if identifier:
                by_identifier[str(identifier)].append(source_row)

    saved_rows: list[dict] = []
    matched_source_rows: set[int] = set()
    conflicts = 0
    matched = 0
    for item in catalog:
        identifiers = {
            _identifier(str(item.get("article") or "").partition(" / ")[0]),
            _identifier(item.get("barcode")),
        }
        candidates = {
            int(candidate["source_row"]): candidate
            for identifier in identifiers
            if identifier
            for candidate in by_identifier.get(identifier, [])
        }
        codes = {candidate.get("abc_code") for candidate in candidates.values() if candidate.get("abc_code")}
        if len(codes) > 1:
            conflicts += 1
            selected = None
            code = None
        else:
            selected = (
                min(candidates.values(), key=lambda value: int(value["source_row"])) if candidates else None
            )
            code = next(iter(codes), None)
        if selected is not None:
            matched += 1
            matched_source_rows.update(candidates)
        saved_rows.append(
            {
                "stock_item_id": int(item["id"]),
                "abc_code": code,
                "turnover_days": _turnover_days(code),
                "source_article": selected.get("source_article") if selected else None,
                "source_barcode": selected.get("source_barcode") if selected else None,
                "source_row": selected.get("source_row") if selected else None,
            }
        )

    saved = db.replace_unit_economics_1c_product_classifications(saved_rows, _now())
    logger.info(
        "Коды товаров: сохранено=%s, сопоставлено=%s, код None=%s, конфликтов=%s",
        saved,
        matched,
        sum(row["abc_code"] is None for row in saved_rows),
        conflicts,
    )
    return {
        "ok": True,
        "sheet_rows": len(source),
        "catalog_rows": len(catalog),
        "saved": saved,
        "matched": matched,
        "code_none": sum(row["abc_code"] is None for row in saved_rows),
        "conflicts": conflicts,
        "sheet_rows_without_catalog": len(source) - len(matched_source_rows),
    }


def sync_wb_commissions(rows: list[list[object]] | None = None) -> dict:
    logger.info("Комиссии СУ: читаем Google-таблицу")
    parsed = parse_commission_rows(rows if rows is not None else _sheet_rows(COMMISSION_SHEET_URL))
    saved = db.replace_unit_economics_1c_wb_commissions(parsed, _now())
    logger.info("Комиссии СУ: сохранено категорий=%s", saved)
    return {"ok": True, "sheet_rows": len(parsed), "saved": saved}


def sync_product_categories(store_slug: str) -> dict:
    if store_slug not in STORES:
        raise ValueError("Неизвестный кабинет")
    catalog = db.list_unit_economics_1c_active_wb_stock_items(store_slug)
    if not catalog:
        return {"ok": True, "store": store_slug, "catalog_rows": 0, "saved": 0, "matched": 0}
    if not wb_tokens.has_token(store_slug):
        return {"ok": False, "store": store_slug, "error": "нет WB-токена", "saved": 0}

    logger.info("Категории WB [%s]: запрашиваем карточки, товаров=%s", store_slug, len(catalog))
    cards = wb_api.get_cards_list(wb_tokens.get_token(store_slug))
    categories = {
        str(card.get("nmID") or "").strip(): {
            "wb_subject_id": card.get("subjectID"),
            "imt_id": card.get("imtID"),
            "created_at": str(card.get("createdAt") or "").strip() or None,
            "category": _text(card.get("subjectName")) or None,
        }
        for card in cards
        if str(card.get("nmID") or "").strip()
    }
    saved_rows = []
    matched = 0
    for item in catalog:
        nm_id = str(item.get("article") or "").partition(" / ")[0].strip()
        category = categories.get(nm_id) or {}
        name = category.get("category")
        if name:
            matched += 1
        saved_rows.append(
            {
                "stock_item_id": int(item["id"]),
                "wb_subject_id": category.get("wb_subject_id"),
                "imt_id": category.get("imt_id"),
                "created_at": category.get("created_at"),
                "category": name,
                "category_key": _category_key(name) or None,
            }
        )
    saved = db.replace_unit_economics_1c_product_categories(store_slug, saved_rows, _now())
    logger.info(
        "Категории WB [%s]: карточек=%s, сохранено=%s, сопоставлено=%s",
        store_slug,
        len(cards),
        saved,
        matched,
    )
    return {
        "ok": True,
        "store": store_slug,
        "cards": len(cards),
        "catalog_rows": len(catalog),
        "saved": saved,
        "matched": matched,
        "category_none": len(saved_rows) - matched,
    }


def _threshold() -> str:
    return (datetime.now(UTC) - timedelta(days=REFERENCE_REFRESH_DAYS)).isoformat()


def _safe_sync(name: str, callback, *args) -> dict:
    try:
        return callback(*args)
    except Exception as error:
        logger.exception(
            "unit_economics_reference_sync_failed source=%s error_type=%s",
            name,
            type(error).__name__,
        )
        return {"ok": False, "error": str(error), "error_type": type(error).__name__}


def sync_product_categories_for_stores(store_slugs: tuple[str, ...]) -> dict[str, dict]:
    return {
        store_slug: _safe_sync(f"categories:{store_slug}", sync_product_categories, store_slug)
        for store_slug in store_slugs
    }


def sync_categories_due(store_slugs: tuple[str, ...] | None = None) -> dict[str, dict]:
    threshold = _threshold()
    stores = tuple(store_slugs or STORES)
    due_stores = tuple(
        store_slug
        for store_slug in stores
        if db.unit_economics_1c_product_categories_due(store_slug, threshold)
    )
    return sync_product_categories_for_stores(due_stores)


def sync_all(*, force: bool = False) -> dict:
    threshold = _threshold()
    report: dict[str, object] = {}
    if force or db.unit_economics_1c_product_classifications_due(threshold):
        report["classifications"] = _safe_sync("classifications", sync_product_classifications)
    if force or db.unit_economics_1c_wb_commissions_due(threshold):
        report["commissions"] = _safe_sync("commissions", sync_wb_commissions)
    report["categories"] = (
        sync_product_categories_for_stores(tuple(STORES)) if force else sync_categories_due()
    )
    return report


def sync_due() -> dict:
    return sync_all(force=False)
