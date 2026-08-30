import csv
import hashlib
import html
import io
import json
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime

from app import db
from app.config import settings
from app.ff_import import google_service_account

CODE_COLUMNS = ("barcode", "article")
QUANTITY_COLUMNS = ("wb", "ozon", "yandex", "ym", "количество", "кол-во", "колво", "qty")
REQUIRED_COLUMNS = CODE_COLUMNS
REQUEST_TIMEOUT = settings.ff_import_timeout_seconds


class FFImportError(Exception):
    pass


class FFImportConfirmationError(FFImportError):
    pass


class _SheetAccessDenied(FFImportError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_cell(v) -> str:
    return str(v if v is not None else "").strip()


def _find_header_row(rows: list[list[str]]) -> tuple[int, dict[str, int]]:

    for row_idx, row in enumerate(rows[:10]):
        normalized = {_normalize_cell(cell).casefold(): i for i, cell in enumerate(row)}
        if not all(col in normalized for col in CODE_COLUMNS):
            continue

        quantity_col = next((normalized[name] for name in QUANTITY_COLUMNS if name in normalized), None)
        if quantity_col is None:
            continue

        found = {col: normalized[col] for col in CODE_COLUMNS}
        found["quantity"] = quantity_col
        return row_idx, found

    raise FFImportError(
        "не нашёл в таблице строку с заголовками. Нужны BARCODE и ARTICLE, "
        "а также колонка с количеством — WB, OZON, YANDEX или КОЛИЧЕСТВО"
    )


def _parse_quantity(raw: str) -> int:
    cleaned = raw.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not cleaned:
        return 0
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def _rows_to_entries(
    rows: list[list[str]],
) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]]:

    if not rows:
        raise FFImportError("таблица пустая")

    header_idx, idx = _find_header_row(rows)
    entries = []
    negative_skipped = []
    for row in rows[header_idx + 1 :]:
        if not row or all(not _normalize_cell(c) for c in row):
            continue
        barcode = _normalize_cell(row[idx["barcode"]]) if idx["barcode"] < len(row) else ""
        article = _normalize_cell(row[idx["article"]]) if idx["article"] < len(row) else ""
        wb_raw = _normalize_cell(row[idx["quantity"]]) if idx["quantity"] < len(row) else ""
        if not barcode and not article:
            continue
        quantity = _parse_quantity(wb_raw)
        if quantity < 0:
            negative_skipped.append((barcode, article, quantity))
            continue
        entries.append((barcode, article, quantity))
    return entries, negative_skipped


def _parse_sheet_id_and_gid(sheet_url: str) -> tuple[str, str | None]:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_url)
    if not match:
        raise FFImportError("не похоже на ссылку на Google Таблицу (нет /spreadsheets/d/<id> в адресе)")
    sheet_id = match.group(1)
    gid_match = re.search(r"[?#&]gid=(\d+)", sheet_url)
    return sheet_id, (gid_match.group(1) if gid_match else None)


def _extract_sheet_export_url(sheet_url: str) -> str:
    sheet_id, gid = _parse_sheet_id_and_gid(sheet_url)
    export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    if gid:
        export_url += f"&gid={gid}"
    return export_url


def _fallback_title(sheet_id: str) -> str:
    return f"Google Таблица {sheet_id[:8]}…"


def _fetch_public_sheet_title(sheet_url: str) -> str:

    sheet_id, _ = _parse_sheet_id_and_gid(sheet_url)
    view_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    try:
        req = urllib.request.Request(view_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            head = resp.read(200_000).decode("utf-8", errors="replace")
    except Exception:
        return _fallback_title(sheet_id)

    match = re.search(r"<title>(.*?)</title>", head, re.S)
    if not match:
        return _fallback_title(sheet_id)

    title = html.unescape(match.group(1)).strip()
    title = re.sub(r"\s*[-—]\s*Google (Sheets|Таблицы)\s*$", "", title).strip()
    return title or _fallback_title(sheet_id)


def fetch_google_sheet_rows(sheet_url: str) -> list[list[str]]:

    export_url = _extract_sheet_export_url(sheet_url)

    req = urllib.request.Request(export_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise _SheetAccessDenied("нет публичного доступа к таблице по ссылке") from e
        raise FFImportError(f"Google вернул ошибку {e.code} при скачивании таблицы") from e
    except urllib.error.URLError as e:
        raise FFImportError(f"не удалось скачать таблицу: {e.reason}") from e

    text = raw.decode("utf-8-sig", errors="replace")

    if "text/csv" not in content_type and (
        text.lstrip().lower().startswith("<!doctype") or text.lstrip().lower().startswith("<html")
    ):
        raise _SheetAccessDenied("вместо таблицы пришла страница авторизации Google (таблица не публичная)")

    reader = csv.reader(io.StringIO(text))
    return [row for row in reader]


def _api_http_error_to_ffimport(e, sheet_id: str) -> FFImportError:
    status = getattr(getattr(e, "resp", None), "status", None)
    if status == 403:
        return FFImportError(
            "нет доступа к таблице через сервисный аккаунт — расшарь таблицу на "
            f"{google_service_account.get_service_account_email()} с правами «Читатель»"
        )
    if status == 404:
        return FFImportError("таблица не найдена через API — проверь ссылку (или id таблицы)")
    return FFImportError(f"Google Sheets API вернул ошибку: {e}")


def fetch_google_sheet_rows_via_api(sheet_url: str) -> tuple[list[list[str]], str]:

    if not google_service_account.has_credentials():
        raise FFImportError(
            "таблица не расшарена по ссылке, а сервисный аккаунт Google не настроен "
            f"(нет файла {google_service_account.CREDENTIALS_PATH}) — либо открой доступ "
            'по ссылке ("Доступно всем, у кого есть ссылка"), либо настрой сервисный аккаунт'
        )

    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as e:
        raise FFImportError(
            "для чтения приватных Google Таблиц на сервере нужен пакет "
            "google-api-python-client — установи его в .venv "
            "(pip install google-api-python-client google-auth) и попробуй снова"
        ) from e

    try:
        creds = google_service_account.get_credentials()
    except google_service_account.CredentialsUnavailableError as e:
        raise FFImportError(str(e)) from e

    sheet_id, gid = _parse_sheet_id_and_gid(sheet_url)
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    try:
        meta = (
            service.spreadsheets()
            .get(spreadsheetId=sheet_id, fields="properties.title,sheets.properties(sheetId,title)")
            .execute()
        )
    except HttpError as e:
        raise _api_http_error_to_ffimport(e, sheet_id) from e

    title = meta.get("properties", {}).get("title") or _fallback_title(sheet_id)

    sheet_title = None
    if gid is not None:
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if str(props.get("sheetId")) == gid:
                sheet_title = props.get("title")
                break

    range_ = f"'{sheet_title}'!A1:ZZ20000" if sheet_title else "A1:ZZ20000"
    try:
        result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_).execute()
    except HttpError as e:
        raise _api_http_error_to_ffimport(e, sheet_id) from e

    return result.get("values", []), title


def _parse_xlsx_rows(file_bytes: bytes) -> list[list[str]]:
    try:
        import openpyxl
    except ImportError as e:
        raise FFImportError(
            "для загрузки .xlsx на сервере нужен пакет openpyxl — установи его в .venv "
            "(pip install openpyxl) и попробуй снова, либо используй ссылку на Google Таблицу"
        ) from e

    workbook = None
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
        sheet = workbook.worksheets[0]
        rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    except Exception as e:
        raise FFImportError(f"не удалось прочитать .xlsx: {e}") from e
    finally:
        if workbook is not None:
            workbook.close()

    return [[_normalize_cell(c) for c in row] for row in rows]


def _apply_entries(
    store_slug: str,
    fulfillment: str,
    entries: list[tuple[str, str, int]],
    *,
    marketplace: str,
    source_type: str,
    sheet_url: str | None,
    table_title: str,
    negative_skipped: list[tuple[str, str, int]] | None = None,
    preview: bool = False,
    confirmation_token: str | None = None,
) -> dict:
    table_title = (table_title or "").strip() or "(без названия)"
    negative_skipped = negative_skipped or []

    catalog = db.get_catalog_items(store_slug, marketplace)
    by_barcode = {item["barcode"]: item["article"] for item in catalog}
    known_articles = {item["article"] for item in catalog}
    meta_by_article = {item["article"]: item for item in catalog}

    def _label(barcode: str, article: str) -> str:

        target = by_barcode.get(barcode)
        if target is None and article in known_articles:
            target = article
        return target or article or barcode or "?"

    resolved: dict[str, int] = {}
    unmatched = 0
    unmatched_quantity = 0

    for barcode, article, quantity in entries:
        target_article = by_barcode.get(barcode)
        if target_article is None and article in known_articles:
            target_article = article
        if target_article is None:
            unmatched += 1
            unmatched_quantity += quantity
            continue

        resolved[target_article] = resolved.get(target_article, 0) + quantity

    skipped_labels = [
        {"article": _label(barcode, article), "quantity": quantity}
        for barcode, article, quantity in negative_skipped
    ]

    source_key = _source_key(source_type, sheet_url, table_title)
    source_quantity = sum(quantity for _barcode, _article, quantity in entries)

    def calculate(previous: dict[str, int]) -> dict:
        report = _build_import_report(
            resolved,
            previous,
            meta_by_article,
            table_title=table_title,
            total_rows=len(entries),
            source_quantity=source_quantity,
            unmatched=unmatched,
            unmatched_quantity=unmatched_quantity,
            negative_skipped=skipped_labels,
        )
        report["confirmation_token"] = _confirmation_token(
            store_slug,
            fulfillment,
            marketplace,
            source_type,
            source_key,
            resolved,
            previous,
            source_quantity,
            unmatched_quantity,
        )
        return report

    if preview:
        previous = db.get_ff_import_snapshot(
            store_slug,
            fulfillment,
            marketplace,
            source_type,
            source_key,
            sheet_url=sheet_url,
            table_title=table_title,
        )
        return calculate(previous) | {"preview": True}

    with db.WRITE_LOCK:
        previous = db.get_ff_import_snapshot(
            store_slug,
            fulfillment,
            marketplace,
            source_type,
            source_key,
            sheet_url=sheet_url,
            table_title=table_title,
        )
        report = calculate(previous)
        if confirmation_token is not None and confirmation_token != report["confirmation_token"]:
            raise FFImportConfirmationError(
                "Расчёт изменился после проверки. Сток не внесён — проверьте цифры ещё раз."
            )
        db.apply_ff_import_snapshot(
            store_slug,
            fulfillment,
            marketplace,
            source_type,
            source_key,
            resolved,
            _now(),
            sheet_url=sheet_url,
            table_title=table_title,
            total_rows=len(entries),
            unmatched=unmatched,
        )
    report.pop("confirmation_token", None)
    return report


def _build_import_report(
    resolved: dict[str, int],
    previous: dict[str, int],
    meta_by_article: dict[str, dict],
    *,
    table_title: str,
    total_rows: int,
    source_quantity: int,
    unmatched: int,
    unmatched_quantity: int,
    negative_skipped: list[dict],
) -> dict:
    new_items = []
    increased = []
    unchanged = []
    decreased = []
    applied_items = []
    for article, quantity in resolved.items():
        old_quantity = previous.get(article, 0)
        delta = quantity - old_quantity
        item = {
            "article": article,
            "barcode": meta_by_article.get(article, {}).get("barcode", ""),
            "name": meta_by_article.get(article, {}).get("name", ""),
            "previous_quantity": old_quantity,
            "source_quantity": quantity,
        }
        if article not in previous and quantity > 0:
            new_items.append({**item, "quantity": quantity})
            applied_items.append({**item, "quantity": quantity})
        elif delta > 0:
            increased.append({**item, "quantity": delta})
            applied_items.append({**item, "quantity": delta})
        elif delta == 0:
            unchanged.append(item)
        else:
            decreased.append({**item, "difference": delta})

    removed = [
        {
            "article": article,
            "previous_quantity": quantity,
            "source_quantity": 0,
            "difference": -quantity,
        }
        for article, quantity in previous.items()
        if article not in resolved
    ]
    return {
        "total_rows": total_rows,
        "source_quantity": source_quantity,
        "matched_source_quantity": sum(resolved.values()),
        "matched": len(resolved),
        "unmatched": unmatched,
        "unmatched_quantity": unmatched_quantity,
        "table_title": table_title,
        "negative_skipped": negative_skipped,
        "applied": len(applied_items),
        "added_quantity": sum(int(item["quantity"]) for item in applied_items),
        "new_items": new_items,
        "increased": increased,
        "unchanged": unchanged,
        "decreased": decreased,
        "removed": removed,
        "items": applied_items,
    }


def _confirmation_token(
    store_slug: str,
    fulfillment: str,
    marketplace: str,
    source_type: str,
    source_key: str,
    resolved: dict[str, int],
    previous: dict[str, int],
    source_quantity: int,
    unmatched_quantity: int,
) -> str:
    payload = json.dumps(
        {
            "target": [store_slug, fulfillment, marketplace, source_type, source_key],
            "resolved": sorted(resolved.items()),
            "previous": sorted(previous.items()),
            "source_quantity": source_quantity,
            "unmatched_quantity": unmatched_quantity,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _source_key(source_type: str, sheet_url: str | None, table_title: str) -> str:
    if source_type == "sheet" and sheet_url:
        sheet_id, gid = _parse_sheet_id_and_gid(sheet_url)
        return f"{sheet_id}:{gid or '0'}"
    return table_title.strip().casefold()


def import_ff_stock_from_sheet(
    store_slug: str,
    fulfillment: str,
    sheet_url: str,
    marketplace: str = db.DEFAULT_MARKETPLACE,
    *,
    preview: bool = False,
    confirmation_token: str | None = None,
) -> dict:

    try:
        rows = fetch_google_sheet_rows(sheet_url)
        table_title = _fetch_public_sheet_title(sheet_url)
    except _SheetAccessDenied:
        rows, table_title = fetch_google_sheet_rows_via_api(sheet_url)
    entries, negative_skipped = _rows_to_entries(rows)
    return _apply_entries(
        store_slug,
        fulfillment,
        entries,
        marketplace=marketplace,
        source_type="sheet",
        sheet_url=sheet_url,
        table_title=table_title,
        negative_skipped=negative_skipped,
        preview=preview,
        confirmation_token=confirmation_token,
    )


def import_ff_stock_from_xlsx(
    store_slug: str,
    fulfillment: str,
    file_bytes: bytes,
    file_name: str = "",
    marketplace: str = db.DEFAULT_MARKETPLACE,
    *,
    preview: bool = False,
    confirmation_token: str | None = None,
) -> dict:
    rows = _parse_xlsx_rows(file_bytes)
    entries, negative_skipped = _rows_to_entries(rows)
    table_title = (file_name or "").strip() or "(файл без имени)"
    return _apply_entries(
        store_slug,
        fulfillment,
        entries,
        marketplace=marketplace,
        source_type="file",
        sheet_url=None,
        table_title=table_title,
        negative_skipped=negative_skipped,
        preview=preview,
        confirmation_token=confirmation_token,
    )
