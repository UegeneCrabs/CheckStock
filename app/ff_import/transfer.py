"""
Перемещение остатков между фулфилментами и маркетплейсами.

Все три сценария — это одна операция: перенос из ячейки (ФФ + маркетплейс)
в другую ячейку. Отдельной логики под каждый случай не нужно:

    ФФ1/OZON -> ФФ2/OZON   перевоз между складами, маркетплейс тот же
    ФФ2/WB   -> ФФ2/OZON   переброс между маркетплейсами внутри одного склада
    ФФ1/OZON -> ФФ2/WB     меняется и склад, и маркетплейс

Правило одно: откуда забрали — вычли, куда привезли — прибавили.

Проверка идёт до записи и по всему списку сразу: если хоть одной позиции
не хватает на источнике, не проводится ничего. Иначе половина списка уже
уехала бы, а половина нет, и разобраться, что применилось, было бы нельзя.
"""

from datetime import datetime, timezone

from app import db
from app.ff_import.importer import (
    FFImportError,
    _parse_quantity,
    _parse_xlsx_rows,
    fetch_google_sheet_rows,
    fetch_google_sheet_rows_via_api,
    _SheetAccessDenied,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_route(
    from_fulfillment: str,
    from_marketplace: str,
    to_fulfillment: str,
    to_marketplace: str,
) -> None:
    """Проверяет сам маршрут, ещё до разбора товаров."""
    if not from_fulfillment or not to_fulfillment:
        raise FFImportError("выберите фулфилменты «откуда» и «куда»")
    if not from_marketplace or not to_marketplace:
        raise FFImportError("выберите маркетплейсы «откуда» и «куда»")
    if from_marketplace not in db.MARKETPLACES or to_marketplace not in db.MARKETPLACES:
        raise FFImportError("неизвестный маркетплейс")
    if from_fulfillment == to_fulfillment and from_marketplace == to_marketplace:
        raise FFImportError("источник и получатель совпадают — перемещать некуда")


def resolve_entries(store_slug: str, raw_entries: list[dict],
                    marketplace: str = "WB",
                    allow_negative: bool = False) -> list[tuple[str, int, str, str]]:
    """Приводит введённые строки к (article, quantity, name).

    code — артикул или баркод. Один и тот же товар дважды в одном перемещении
    запрещён: почти всегда это случайно продублированная строка, а молчаливое
    суммирование прятало бы ошибку. Дубль ловится и когда товар введён разными
    способами — артикулом в одной строке и баркодом в другой.

    Все проблемы собираются и показываются разом.
    """
    if not raw_entries:
        raise FFImportError("добавьте хотя бы одну позицию")

    catalog = db.get_catalog_items(store_slug, marketplace)
    by_barcode = {item["barcode"]: item for item in catalog}
    by_article = {item["article"]: item for item in catalog}

    resolved: dict[str, dict] = {}
    problems: list[str] = []
    duplicates: list[str] = []

    for index, entry in enumerate(raw_entries, start=1):
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
        # Минус разрешён только там, где он осмыслен — в возврате из мусорки.
        # В остальных операциях это опечатка, и пропускать её нельзя.
        if quantity == 0 or (quantity < 0 and not allow_negative):
            problems.append(
                f"строка {index} ({code}): количество должно быть больше нуля"
                if not allow_negative else
                f"строка {index} ({code}): количество не может быть нулём"
            )
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
            duplicates.append(f"{article} ({item['name']})")
            continue
        resolved[article] = {"quantity": quantity, "name": item["name"], "barcode": item["barcode"]}

    if duplicates:
        problems.append(
            "товар указан несколько раз: "
            + "; ".join(sorted(set(duplicates)))
            + " — оставьте одну строку с итоговым количеством"
        )

    if problems:
        raise FFImportError("; ".join(problems))

    return [(a, info["quantity"], info["name"], info["barcode"]) for a, info in resolved.items()]


def check_availability(
    store_slug: str,
    entries: list[tuple[str, int, str, str]],
    from_fulfillment: str,
    from_marketplace: str,
) -> None:
    """Хватает ли остатка на источнике по каждой позиции.
    Перечисляет сразу все нехватки, чтобы не исправлять по одной."""
    shortages = []
    for article, quantity, name, _barcode in entries:
        available = db.get_ff_stock_one(store_slug, article, from_fulfillment, from_marketplace)
        if quantity > available:
            shortages.append(f"{article} ({name}): просят {quantity}, есть {available}")

    if shortages:
        raise FFImportError(
            "не хватает остатка на источнике — "
            + "; ".join(shortages)
            + ". Перемещение отменено целиком."
        )


def transfer(
    store_slug: str,
    raw_entries: list[dict],
    from_fulfillment: str,
    from_marketplace: str,
    to_fulfillment: str,
    to_marketplace: str,
    user_id: int | None,
    user_name: str,
) -> list[dict]:
    """Полный цикл: проверка маршрута -> разбор позиций -> проверка остатков
    -> запись одной транзакцией. Возвращает применённые позиции."""
    validate_route(from_fulfillment, from_marketplace, to_fulfillment, to_marketplace)
    # товары ищем в каталоге маркетплейса-источника: артикулы и баркоды
    # у площадок свои, и каталог Ozon с каталогом WB не пересекается
    entries = resolve_entries(store_slug, raw_entries, from_marketplace)

    with db.WRITE_LOCK:
        check_availability(store_slug, entries, from_fulfillment, from_marketplace)
        db.apply_ff_transfer(
            store_slug,
            [(article, quantity) for article, quantity, _n, _b in entries],
            from_fulfillment, from_marketplace,
            to_fulfillment, to_marketplace,
            user_id, user_name, _now(),
        )

    return [{"article": a, "name": n, "barcode": b, "quantity": q} for a, q, n, b in entries]


# Как может называться колонка с количеством в файле перемещения.
# «WB» оставлен для совместимости с шаблоном поставок, но для перемещения он
# сбивает с толку (WB — ещё и маркетплейс), поэтому понятные названия впереди.
QUANTITY_HEADERS = ("количество", "кол-во", "колво", "qty", "quantity", "wb")
CODE_HEADERS = ("barcode", "баркод", "article", "артикул")


def _parse_transfer_rows(rows: list[list[str]]) -> list[dict]:
    """Разбирает таблицу перемещения: нужен код товара (баркод или артикул)
    и количество. Маршрут (откуда/куда) в файле не указывается — он берётся
    из формы, поэтому один файл нельзя развезти по разным направлениям.
    """
    if not rows:
        raise FFImportError("таблица пустая")

    header_idx = None
    code_cols: dict[str, int] = {}
    qty_col = None

    for row_idx, row in enumerate(rows[:10]):
        found_codes = {}
        found_qty = None
        for i, cell in enumerate(row):
            name = str(cell or "").strip().casefold()
            if name in CODE_HEADERS:
                found_codes[name] = i
            elif name in QUANTITY_HEADERS and found_qty is None:
                found_qty = i
        if found_codes and found_qty is not None:
            header_idx, code_cols, qty_col = row_idx, found_codes, found_qty
            break

    if header_idx is None:
        raise FFImportError(
            "не нашёл в таблице шапку: нужна колонка с кодом товара "
            "(BARCODE или ARTICLE) и колонка с количеством "
            "(КОЛИЧЕСТВО, КОЛ-ВО, QTY или WB)"
        )

    # баркод точнее артикула, поэтому берём его первым, если он есть
    order = [code_cols[k] for k in ("barcode", "баркод") if k in code_cols]
    order += [code_cols[k] for k in ("article", "артикул") if k in code_cols]

    entries = []
    for row in rows[header_idx + 1:]:
        if not row or all(not str(c or "").strip() for c in row):
            continue

        code = ""
        for idx in order:
            if idx < len(row):
                value = str(row[idx] or "").strip()
                if value:
                    code = value
                    break
        if not code:
            continue

        raw_qty = str(row[qty_col] or "").strip() if qty_col < len(row) else ""
        quantity = _parse_quantity(raw_qty)
        if quantity <= 0:
            # ноль/пусто = перемещать нечего. Заодно отсекает подписи и
            # комментарии под таблицей, которые иначе приняли бы за товар
            continue
        entries.append({"code": code, "quantity": quantity})

    if not entries:
        raise FFImportError("в таблице нет ни одной позиции с количеством больше нуля")

    return entries


def entries_from_xlsx(file_bytes: bytes) -> list[dict]:
    """Позиции для перемещения из .xlsx."""
    return _parse_transfer_rows(_parse_xlsx_rows(file_bytes))


def entries_from_sheet(sheet_url: str) -> list[dict]:
    """Позиции для перемещения из Google Таблицы."""
    try:
        rows = fetch_google_sheet_rows(sheet_url)
    except _SheetAccessDenied:
        rows, _title = fetch_google_sheet_rows_via_api(sheet_url)
    return _parse_transfer_rows(rows)
