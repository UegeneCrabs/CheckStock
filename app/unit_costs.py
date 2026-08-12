import logging
import re
from datetime import UTC, datetime

from app import db
from app.ff_import import importer

logger = logging.getLogger(__name__)

SHEET_ID = "1q0WL6OB3Edh2O1ogqx7CK3MAij3O6xjD6gE0i3q3qEY"


WB_COST_SHEETS = (
    ("1248315136", ("sokoloff", "trusthome")),
    ("1540118593", ("gogol",)),
    ("2127276266", ("toyka",)),
    ("660341895", ("tris",)),
    ("826785542", ("rockkiddo",)),
    ("754370417", ("rimili",)),
)


class UnitCostSyncError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sheet_url(gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?gid={gid}#gid={gid}"


def _header_key(value: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", str(value or "").strip().lower().replace("ё", "е"))


def _find_column(keys: list[str], *needles: str) -> int | None:
    for index, key in enumerate(keys):
        if any(needle in key for needle in needles):
            return index
    return None


def _find_header(rows: list[list[str]]) -> tuple[int, int, int, int | None]:
    for row_index, row in enumerate(rows[:20]):
        keys = [_header_key(cell) for cell in row]
        article_index = _find_column(keys, "артикулвб")
        cost_index = _find_column(keys, "себесруб", "себестоимостьруб")
        if article_index is None or cost_index is None:
            continue
        other_cost_index = _find_column(keys, "прочзатрруб", "прочиезатратыруб")
        return row_index, article_index, cost_index, other_cost_index
    raise UnitCostSyncError("не найдены колонки «АртикулВБ» и «Себес, руб»")


def _parse_decimal(value: str) -> float | None:
    text = str(value or "").strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not text or text in {"-", "—"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_article(value: str) -> str:
    article = str(value or "").strip().replace("\xa0", "").replace(" ", "")
    if article.endswith(".0") and article[:-2].isdigit():
        article = article[:-2]
    return article


def parse_cost_rows(rows: list[list[str]]) -> list[dict]:
    header_row, article_index, cost_index, other_cost_index = _find_header(rows)
    by_article: dict[str, dict] = {}

    for row in rows[header_row + 1 :]:
        article = _normalize_article(row[article_index] if article_index < len(row) else "")
        purchase_price = _parse_decimal(row[cost_index] if cost_index < len(row) else "")
        if not article or purchase_price is None or purchase_price < 0:
            continue
        other_cost = (
            _parse_decimal(row[other_cost_index])
            if other_cost_index is not None and other_cost_index < len(row)
            else None
        )
        by_article[article] = {
            "article": article,
            "purchase_price": purchase_price,
            "other_cost": other_cost,
        }

    if not by_article:
        raise UnitCostSyncError("в листе не найдено ни одной строки с себестоимостью")
    return list(by_article.values())


def sync_all() -> dict:

    report: dict[str, dict] = {}

    for gid, store_slugs in WB_COST_SHEETS:
        try:
            rows = importer.fetch_google_sheet_rows(_sheet_url(gid))
            entries = parse_cost_rows(rows)
            updated_at = _now_iso()
            for store_slug in store_slugs:
                count = db.replace_unit_costs(store_slug, entries, gid, updated_at)
                db.record_sync_health(store_slug, "WB", "unit_cost", True, None, updated_at)
                report[store_slug] = {"ok": True, "rows": count, "gid": gid}
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            logger.error("Себестоимость WB, лист %s: %s", gid, message)
            for store_slug in store_slugs:
                db.record_sync_health(store_slug, "WB", "unit_cost", False, message, _now_iso())
                report[store_slug] = {"ok": False, "error": message, "gid": gid}

    return report
