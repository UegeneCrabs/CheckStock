"""Choose a display barcode while retaining all identifiers for stock matching."""

from collections.abc import Iterable


def ordered_barcodes(values: Iterable[object]) -> list[str]:
    codes = dict.fromkeys(
        str(code).strip()
        for code in values
        if code is not None and not isinstance(code, bool) and str(code).strip()
    )
    # Preserve source order within each priority group, as in the WB catalog.
    return sorted(
        codes,
        key=lambda code: (
            code.startswith("0"),
            not (len(code) == 13 and code.isascii() and code.isdigit()),
        ),
    )


def preferred_barcode(values: Iterable[object]) -> str:
    return next((code for code in ordered_barcodes(values) if not code.startswith("0")), "")
