from app.dto.stock import SignedStockEntries
from app.ff_import.importer import FFImportError
from app.ff_import.transfer import (
    _parse_transfer_rows,
    entries_from_sheet,
    entries_from_xlsx,
)

__all__ = [
    "FFImportError",
    "entries_from_sheet",
    "entries_from_xlsx",
    "entries_from_rows",
]


def entries_from_rows(rows: list[list[str]]) -> SignedStockEntries:
    return _parse_transfer_rows(rows)
