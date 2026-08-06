"""
Загрузка остатков на фулфилмент ("Доступно ФФ для распределения") вручную —
либо файлом .xlsx, либо ссылкой на Google Таблицу с теми же данными.

В обоих случаях ищем колонки BARCODE, ARTICLE и колонку с количеством
(WB, OZON, YANDEX или КОЛИЧЕСТВО — без учёта регистра) и
количество из колонки WB записываем в ff_stock для выбранного фулфилмента,
сопоставляя строку с товаром каталога сначала по баркоду, потом по артикулу.

Для Google Таблицы сначала пробуем самый простой путь — публичный CSV-экспорт
без всякой авторизации (работает, если таблица расшарена как "Доступно всем,
у кого есть ссылка"). Если это не сработало из-за прав доступа — пробуем
прочитать её через Google Sheets API сервисным аккаунтом (см.
google_service_account.py) — так можно читать и приватные таблицы, если они
расшарены конкретно на e-mail сервисного аккаунта (не всем по ссылке).
Если сервисный аккаунт не настроен — возвращаем понятную ошибку с обоими
вариантами решения.
"""

import csv
import html
import io
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

from app import db
from app.formatting import format_dt
from app.ff_import import google_service_account

# Колонка с товаром обязательна, а колонку с количеством называют по-разному:
# в выгрузке WB это «WB», в выгрузке Ozon — «OZON». Принимаем любой из
# вариантов, иначе пришлось бы переименовывать колонку руками перед каждой
# загрузкой — а это ровно то место, где ошибаются.
CODE_COLUMNS = ("barcode", "article")
QUANTITY_COLUMNS = ("wb", "ozon", "yandex", "ym", "количество", "кол-во", "колво", "qty")
REQUIRED_COLUMNS = CODE_COLUMNS
REQUEST_TIMEOUT = 30


class FFImportError(Exception):
    """Понятная пользователю ошибка загрузки остатков на ФФ."""


class _SheetAccessDenied(FFImportError):
    """Внутренний маркер: публичная CSV-ссылка не сработала из-за прав
    доступа — стоит попробовать через сервисный аккаунт Google Sheets API,
    если он настроен."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Человекочитаемое время (МСК) вместо сырого ISO с микросекундами —
# общий для всего приложения форматтер, см. app/formatting.py
_format_dt = format_dt


def _normalize_cell(v) -> str:
    return str(v if v is not None else "").strip()


def _find_header_row(rows: list[list[str]]) -> tuple[int, dict[str, int]]:
    """Ищет среди первых нескольких строк ту, где есть все нужные заголовки
    (без учёта регистра/пробелов), и возвращает (номер строки, {колонка: индекс})."""
    for row_idx, row in enumerate(rows[:10]):
        normalized = {_normalize_cell(cell).casefold(): i for i, cell in enumerate(row)}
        if not all(col in normalized for col in CODE_COLUMNS):
            continue

        quantity_col = next((normalized[name] for name in QUANTITY_COLUMNS
                             if name in normalized), None)
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
    """Возвращает (entries, negative_skipped) — оба списка из (barcode, article,
    quantity). В entries попадают только строки с количеством >= 0; строки с
    отрицательным количеством в колонке WB в остатки не идут, а возвращаются
    отдельно в negative_skipped, чтобы вызывающий код мог о них сообщить."""
    if not rows:
        raise FFImportError("таблица пустая")

    header_idx, idx = _find_header_row(rows)
    entries = []
    negative_skipped = []
    for row in rows[header_idx + 1:]:
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
        raise FFImportError(
            "не похоже на ссылку на Google Таблицу (нет /spreadsheets/d/<id> в адресе)"
        )
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
    """Название документа Google Таблицы (обычно = номер поставки) —
    вытаскиваем из <title> публичной страницы таблицы. Используется только
    после того, как публичный CSV-путь уже подтвердил, что таблица
    действительно открыта по ссылке — иначе тут будет страница входа Google,
    а не реальное название."""
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
    """Публичный путь без авторизации — работает только если таблица
    расшарена как "Доступно всем, у кого есть ссылка". При отказе в доступе
    поднимает _SheetAccessDenied, чтобы вызывающий код мог попробовать через
    сервисный аккаунт вместо мгновенного провала."""
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
    """Читает таблицу через Google Sheets API сервисным аккаунтом — так можно
    прочитать и приватную таблицу, если она расшарена конкретно на e-mail
    сервисного аккаунта (google_service_account.get_service_account_email()),
    а не "всем, у кого есть ссылка". Возвращает (строки, название документа) —
    название нужно для журнала загруженных поставок."""
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
        meta = service.spreadsheets().get(
            spreadsheetId=sheet_id, fields="properties.title,sheets.properties(sheetId,title)"
        ).execute()
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
        # в read_only режиме openpyxl держит открытым файловый дескриптор,
        # пока книгу явно не закрыть
        if workbook is not None:
            workbook.close()

    return [[_normalize_cell(c) for c in row] for row in rows]


def _apply_entries(
    store_slug: str,
    fulfillment: str,
    entries: list[tuple[str, str, int]],
    *,
    source_type: str,
    sheet_url: str | None,
    table_title: str,
    negative_skipped: list[tuple[str, str, int]] | None = None,
) -> dict:
    table_title = (table_title or "").strip() or "(без названия)"
    negative_skipped = negative_skipped or []

    catalog = db.get_catalog_items(store_slug)
    by_barcode = {item["barcode"]: item["article"] for item in catalog}
    known_articles = {item["article"] for item in catalog}
    meta_by_article = {item["article"]: item for item in catalog}

    def _label(barcode: str, article: str) -> str:
        """Артикул для сообщения пользователю — берём из каталога, если
        нашли по баркоду/артикулу, иначе показываем то, что было в таблице."""
        target = by_barcode.get(barcode)
        if target is None and article in known_articles:
            target = article
        return target or article or barcode or "?"

    resolved: dict[str, int] = {}
    unmatched = 0

    for barcode, article, quantity in entries:
        target_article = by_barcode.get(barcode)
        if target_article is None and article in known_articles:
            target_article = article
        if target_article is None:
            unmatched += 1
            continue
        # Если один и тот же товар встретился в поставке дважды — складываем
        # (а не берём последнюю строку), это всё ещё одна и та же поставка.
        resolved[target_article] = resolved.get(target_article, 0) + quantity

    skipped_labels = [
        {"article": _label(barcode, article), "quantity": quantity}
        for barcode, article, quantity in negative_skipped
    ]

    now = _now()
    with db.WRITE_LOCK:
        existing = db.find_existing_delivery(store_slug, sheet_url, table_title)
        if existing is not None:
            raise FFImportError(
                f'поставка "{table_title}" уже была загружена {_format_dt(existing["created_at"])} '
                f'на фулфилмент "{existing["fulfillment"]}" ({existing["matched"]} товаров) — '
                "загрузка отменена, чтобы не прибавить остатки этой поставки повторно"
            )

        for article, quantity in resolved.items():
            # Пока все поставки на ФФ приходят по WB; другие маркетплейсы
            # появятся, когда по ним пойдут отгрузки.
            db.increment_ff_stock(
                store_slug, article, fulfillment, quantity, now, db.DEFAULT_MARKETPLACE
            )

        db.record_delivery(
            store_slug=store_slug,
            fulfillment=fulfillment,
            source_type=source_type,
            sheet_url=sheet_url,
            table_title=table_title,
            total_rows=len(entries),
            matched=len(resolved),
            unmatched=unmatched,
            created_at=now,
        )

    return {
        "total_rows": len(entries),
        "matched": len(resolved),
        "unmatched": unmatched,
        "table_title": table_title,
        "negative_skipped": skipped_labels,
        # применённые строки — из них собирается xlsx при скачивании из журнала
        "items": [
            {
                "article": article,
                "barcode": meta_by_article.get(article, {}).get("barcode", ""),
                "name": meta_by_article.get(article, {}).get("name", ""),
                "quantity": quantity,
            }
            for article, quantity in resolved.items()
        ],
    }


def import_ff_stock_from_sheet(store_slug: str, fulfillment: str, sheet_url: str) -> dict:
    # Сначала — публичный CSV-экспорт без авторизации (быстрее и не требует
    # настройки). Если таблица оказалась закрытой — пробуем через сервисный
    # аккаунт Google Sheets API (см. fetch_google_sheet_rows_via_api).
    try:
        rows = fetch_google_sheet_rows(sheet_url)
        table_title = _fetch_public_sheet_title(sheet_url)
    except _SheetAccessDenied:
        rows, table_title = fetch_google_sheet_rows_via_api(sheet_url)
    entries, negative_skipped = _rows_to_entries(rows)
    return _apply_entries(
        store_slug, fulfillment, entries,
        source_type="sheet", sheet_url=sheet_url, table_title=table_title,
        negative_skipped=negative_skipped,
    )


def add_items(store_slug: str, fulfillment: str, entries: list[dict]) -> list[dict]:
    """Ручная докладка нескольких позиций — когда в поставке товар ушёл с
    минусом и проще дописать пару строк, чем городить второй файл.

    entries — список {"code": артикул ИЛИ баркод, "quantity": сколько}.
    Количество ПРИБАВЛЯЕТСЯ к текущему остатку, как и обычная поставка.

    Сначала проверяем ВСЕ строки и только потом пишем: иначе при ошибке в
    середине списка часть позиций уже была бы записана, и пользователь не
    понял бы, что применилось, а что нет.

    Один и тот же товар дважды запрещён: это всегда случайно продублированная
    строка, а молчаливое суммирование прятало бы ошибку. Дубль ловится и когда
    товар введён по-разному — артикулом в одной строке и баркодом в другой.

    В журнал поставок не пишем: это правка, а не отдельная поставка, иначе
    защита от дублей начала бы ругаться на следующую настоящую поставку.
    """
    if not entries:
        raise FFImportError("добавьте хотя бы одну позицию")

    catalog = db.get_catalog_items(store_slug)
    by_barcode = {item["barcode"]: item for item in catalog}
    by_article = {item["article"]: item for item in catalog}

    resolved: dict[str, dict] = {}
    problems: list[str] = []
    duplicates: list[str] = []

    for index, entry in enumerate(entries, start=1):
        code = str(entry.get("code") or "").strip()
        raw_qty = entry.get("quantity")

        if not code:
            problems.append(f"строка {index}: не указан артикул или баркод")
            continue
        try:
            quantity = int(raw_qty)
        except (TypeError, ValueError):
            problems.append(f"строка {index} ({code}): количество должно быть числом")
            continue
        if quantity <= 0:
            problems.append(f"строка {index} ({code}): количество должно быть больше нуля")
            continue

        item = by_barcode.get(code) or by_article.get(code)
        if item is None:
            problems.append(
                f"строка {index}: товар «{code}» не найден в каталоге. "
                "Сначала заведите его в личном кабинете маркетплейса — "
                "после ближайшей синхронизации он появится в системе"
            )
            continue

        article = item["article"]
        if article in resolved:
            resolved[article]["added"] += quantity
        else:
            resolved[article] = {
            "article": article, "barcode": item["barcode"],
            "name": item["name"], "added": quantity,
        }

    if problems:
        raise FFImportError("; ".join(problems))

    now = _now()
    with db.WRITE_LOCK:
        for article, info in resolved.items():
            db.increment_ff_stock(
                store_slug, article, fulfillment, info["added"], now, db.DEFAULT_MARKETPLACE
            )

    return list(resolved.values())


def import_ff_stock_from_xlsx(store_slug: str, fulfillment: str, file_bytes: bytes, file_name: str = "") -> dict:
    rows = _parse_xlsx_rows(file_bytes)
    entries, negative_skipped = _rows_to_entries(rows)
    table_title = (file_name or "").strip() or "(файл без имени)"
    return _apply_entries(
        store_slug, fulfillment, entries,
        source_type="file", sheet_url=None, table_title=table_title,
        negative_skipped=negative_skipped,
    )
