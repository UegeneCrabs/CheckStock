from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

from app import db
from app.ff_import import google_service_account
from app.stores import STORES

logger = logging.getLogger(__name__)

SOURCE_SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1q0WL6OB3Edh2O1ogqx7CK3MAij3O6xjD6gE0i3q3qEY/edit?gid=1248315136"
)
SOURCE_SPREADSHEET_ID = "1q0WL6OB3Edh2O1ogqx7CK3MAij3O6xjD6gE0i3q3qEY"
SOURCE_COLUMNS = {
    "article": "артикулвб",
    "tag": "тег",
    "supplier_external": "артикулпоставщикавнешний",
    "purchase_price": "себесруб",
    "fulfillment_cost": "прочзатрруб",
    "team_commission": "процентдляучетамаркетинговыхзатрат",
}
OPTIONAL_SOURCE_COLUMNS = {
    "manager": "менеджер",
}


class SourceDataError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").strip().split())


def _header_key(value: object) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", _text(value).casefold().replace("ё", "е"))


def _identifier(value: object) -> str:
    normalized = _text(value).replace(" ", "").lstrip("'")
    return normalized[:-2] if normalized.endswith(".0") and normalized[:-2].isdigit() else normalized


def _number(value: object) -> float | None:
    normalized = _text(value).replace(" ", "").replace("%", "").replace(",", ".")
    if not normalized or normalized.casefold() in {"#n/a", "n/a", "null", "none", "-"}:
        return None
    try:
        return round(float(normalized), 4)
    except ValueError:
        return None


def _code(value: object) -> str | None:
    normalized = _text(value).upper()
    return None if normalized in {"", "0", "#N/A", "N/A", "NULL", "-"} else normalized


def _split_tag(value: object) -> dict:
    raw = _text(value)
    parts = [_text(part) for part in raw.split("/")] if raw else []
    return {
        "tag_raw": raw or None,
        "goal_week": _number(parts[0]) if len(parts) > 0 else None,
        "goal_day": _number(parts[1]) if len(parts) > 1 else None,
        "stock_status": parts[2] or None if len(parts) > 2 else None,
        "stock_end_week": parts[3] or None if len(parts) > 3 else None,
    }


def _split_supplier_external(value: object) -> dict:
    raw = _text(value)
    parts = [_text(part) for part in raw.split("/")] if raw else []
    fact_match = re.search(r"(?:Ф|F)\s*:\s*([-+]?\d[\d\s.,]*)", raw, re.IGNORECASE)
    plan_match = re.search(r"(?:П|P)\s*:\s*([-+]?\d[\d\s.,]*)", raw, re.IGNORECASE)
    return {
        "supplier_external_raw": raw or None,
        "abc_code": _code(parts[0]) if parts else None,
        "fact_sales": _number(fact_match.group(1)) if fact_match else None,
        "plan_sales": _number(plan_match.group(1)) if plan_match else None,
    }


def _find_header(rows: list[list[object]]) -> tuple[int, dict[str, int]]:
    required = set(SOURCE_COLUMNS.values())
    for row_index, row in enumerate(rows[:20]):
        columns = {_header_key(value): index for index, value in enumerate(row)}
        if required.issubset(columns):
            return row_index, columns
    raise SourceDataError("В Google-таблице не найдены обязательные колонки: " + ", ".join(SOURCE_COLUMNS))


def _sheet_store_slugs(title: str) -> set[str]:
    normalized = _text(title).casefold()
    matched = {slug for slug, store in STORES.items() if str(store["name"]).casefold() in normalized}
    return matched or set(STORES)


def _quoted_sheet_range(title: str, row_count: int) -> str:
    escaped = title.replace("'", "''")
    return f"'{escaped}'!A1:X{max(20, row_count)}"


def fetch_wb_sheet_rows() -> list[dict]:
    """Read every current tab whose visible title ends with WB."""

    if not google_service_account.has_credentials():
        raise SourceDataError(
            "не настроен сервисный аккаунт Google Sheets: "
            f"нет файла {google_service_account.CREDENTIALS_PATH}"
        )
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as error:
        raise SourceDataError("не установлены google-api-python-client и google-auth") from error
    try:
        credentials = google_service_account.get_credentials()
    except google_service_account.CredentialsUnavailableError as error:
        raise SourceDataError(str(error)) from error

    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    try:
        metadata = (
            service.spreadsheets()
            .get(
                spreadsheetId=SOURCE_SPREADSHEET_ID,
                fields="sheets.properties(sheetId,title,gridProperties.rowCount)",
            )
            .execute()
        )
        sheets = [
            sheet["properties"]
            for sheet in metadata.get("sheets", [])
            if _text(sheet.get("properties", {}).get("title")).upper().endswith("WB")
        ]
        if not sheets:
            raise SourceDataError("в Google-таблице нет листов, название которых оканчивается на WB")
        ranges = [
            _quoted_sheet_range(
                str(sheet["title"]),
                int(sheet.get("gridProperties", {}).get("rowCount") or 1_000),
            )
            for sheet in sheets
        ]
        values = (
            service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=SOURCE_SPREADSHEET_ID,
                ranges=ranges,
                majorDimension="ROWS",
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
    except HttpError as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        if status == 403:
            message = (
                "нет доступа к исходной Google-таблице — выдайте права «Читатель» для "
                f"{google_service_account.get_service_account_email()}"
            )
        elif status == 404:
            message = "исходная Google-таблица не найдена"
        else:
            message = f"Google Sheets API вернул ошибку: {error}"
        raise SourceDataError(message) from error

    value_ranges = values.get("valueRanges", [])
    if len(value_ranges) != len(sheets):
        raise SourceDataError("Google Sheets API вернул не все запрошенные WB-листы")
    return [
        {
            "sheet_id": int(sheet["sheetId"]),
            "title": str(sheet["title"]),
            "rows": value_range.get("values", []),
        }
        for sheet, value_range in zip(sheets, value_ranges, strict=True)
    ]


def parse_source_values(sheets: list[dict], catalog: list[dict]) -> dict:
    by_nm_id: dict[str, list[dict]] = defaultdict(list)
    for item in catalog:
        nm_id = _identifier(str(item.get("article") or "").partition(" / ")[0])
        if nm_id:
            by_nm_id[nm_id].append(item)

    parsed_by_item: dict[int, dict] = {}
    commissions_by_store: dict[str, Counter[float]] = defaultdict(Counter)
    source_rows = 0
    unmatched = 0
    ambiguous = 0
    duplicates = 0

    for sheet in sheets:
        title = _text(sheet.get("title"))
        rows = list(sheet.get("rows") or [])
        header_index, columns = _find_header(rows)
        allowed_stores = _sheet_store_slugs(title)
        for source_row, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
            article_column = columns[SOURCE_COLUMNS["article"]]
            nm_id = _identifier(row[article_column]) if article_column < len(row) else ""
            if not nm_id:
                continue
            source_rows += 1
            candidates = [
                item for item in by_nm_id.get(nm_id, []) if str(item.get("store_slug")) in allowed_stores
            ]
            if not candidates:
                unmatched += 1
                continue
            if len(candidates) > 1:
                ambiguous += 1
                continue
            item = candidates[0]

            def cell(name: str, current_row=row, current_columns=columns) -> object:
                column = current_columns[SOURCE_COLUMNS[name]]
                return current_row[column] if column < len(current_row) else None

            def optional_cell(name: str, current_row=row, current_columns=columns) -> object:
                column = current_columns.get(OPTIONAL_SOURCE_COLUMNS[name])
                return current_row[column] if column is not None and column < len(current_row) else None

            parsed = {
                "stock_item_id": int(item["id"]),
                "manager": _text(optional_cell("manager")) or None,
                "purchase_price": _number(cell("purchase_price")),
                "fulfillment_cost": _number(cell("fulfillment_cost")),
                "team_commission_percent": _number(cell("team_commission")),
                **_split_tag(cell("tag")),
                **_split_supplier_external(cell("supplier_external")),
                "source_sheet_id": int(sheet["sheet_id"]),
                "source_sheet_title": title,
                "source_row": source_row,
            }
            stock_item_id = int(item["id"])
            if stock_item_id in parsed_by_item:
                duplicates += 1
                continue
            parsed_by_item[stock_item_id] = parsed
            commission = parsed["team_commission_percent"]
            if commission is not None:
                commissions_by_store[str(item["store_slug"])][commission] += 1

    commission_conflicts = {
        store_slug: dict(sorted(counts.items()))
        for store_slug, counts in commissions_by_store.items()
        if len(counts) > 1
    }
    team_commissions = {
        store_slug: counts.most_common(1)[0][0]
        for store_slug, counts in commissions_by_store.items()
        if counts
    }
    return {
        "rows": list(parsed_by_item.values()),
        "team_commissions": team_commissions,
        "sheet_count": len(sheets),
        "source_rows": source_rows,
        "matched": len(parsed_by_item),
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "duplicates": duplicates,
        "commission_conflicts": commission_conflicts,
    }


def sync_all(sheets: list[dict] | None = None) -> dict:
    loaded_sheets = sheets if sheets is not None else fetch_wb_sheet_rows()
    catalog = db.list_unit_economics_1c_active_wb_stock_items()
    report = parse_source_values(loaded_sheets, catalog)
    synced_at = _now()
    saved = db.replace_unit_economics_1c_source_values(
        report.pop("rows"),
        report.pop("team_commissions"),
        synced_at,
    )
    logger.info(
        "unit_economics_1c_source_sync sheets=%s source_rows=%s saved=%s unmatched=%s "
        "ambiguous=%s duplicates=%s commission_conflicts=%s",
        report["sheet_count"],
        report["source_rows"],
        saved,
        report["unmatched"],
        report["ambiguous"],
        report["duplicates"],
        len(report["commission_conflicts"]),
    )
    return {"ok": True, "saved": saved, "synced_at": synced_at, **report}
