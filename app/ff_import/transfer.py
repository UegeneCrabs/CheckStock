from app.dto.stock import SignedStockEntries, SignedStockEntry
from app.ff_import.importer import (
    FFImportError,
    _parse_quantity,
    _parse_xlsx_rows,
    _SheetAccessDenied,
    fetch_google_sheet_rows,
    fetch_google_sheet_rows_via_api,
)

QUANTITY_HEADERS = ("количество", "кол-во", "колво", "qty", "quantity", "wb")
CODE_HEADERS = ("barcode", "баркод", "article", "артикул")


def _parse_transfer_rows(rows: list[list[str]]) -> SignedStockEntries:
    if not rows:
        raise FFImportError("таблица пустая")

    header_index = None
    code_columns: dict[str, int] = {}
    quantity_column = None
    for row_index, row in enumerate(rows[:10]):
        found_codes: dict[str, int] = {}
        found_quantity = None
        for index, cell in enumerate(row):
            name = str(cell or "").strip().casefold()
            if name in CODE_HEADERS:
                found_codes[name] = index
            elif name in QUANTITY_HEADERS and found_quantity is None:
                found_quantity = index
        if found_codes and found_quantity is not None:
            header_index = row_index
            code_columns = found_codes
            quantity_column = found_quantity
            break

    if header_index is None or quantity_column is None:
        raise FFImportError("не найдена шапка с кодом товара и количеством")

    order = [code_columns[key] for key in ("barcode", "баркод") if key in code_columns]
    order.extend(code_columns[key] for key in ("article", "артикул") if key in code_columns)
    entries: list[SignedStockEntry] = []
    for row in rows[header_index + 1 :]:
        if not row or all(not str(cell or "").strip() for cell in row):
            continue
        code = ""
        for index in order:
            if index < len(row):
                code = str(row[index] or "").strip()
                if code:
                    break
        if not code:
            continue
        raw_quantity = str(row[quantity_column] or "").strip() if quantity_column < len(row) else ""
        quantity = _parse_quantity(raw_quantity)
        if quantity > 0:
            entries.append(SignedStockEntry(code=code, quantity=quantity))

    if not entries:
        raise FFImportError("в таблице нет позиций с количеством больше нуля")
    return SignedStockEntries(tuple(entries))


def entries_from_xlsx(file_bytes: bytes) -> SignedStockEntries:
    return _parse_transfer_rows(_parse_xlsx_rows(file_bytes))


def entries_from_sheet(sheet_url: str) -> SignedStockEntries:
    try:
        rows = fetch_google_sheet_rows(sheet_url)
    except _SheetAccessDenied:
        rows, _title = fetch_google_sheet_rows_via_api(sheet_url)
    return _parse_transfer_rows(rows)
