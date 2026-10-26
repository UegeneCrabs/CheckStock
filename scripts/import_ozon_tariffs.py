import argparse
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import openpyxl

from app.config import settings
from app.core.constants import OZON_COMMISSIONS, OZON_TARIFFS
from app.dto.ozon_tariffs import OzonTariff, OzonTariffCatalog
from app.ozon.tariff_normalizer import normalized_name


def import_catalog(
    workbook: Path, source_url: str, effective_from: date, verified_on: date
) -> OzonTariffCatalog:
    source = urlparse(source_url)
    if source.scheme != "https" or source.hostname != OZON_TARIFFS.source_host:
        raise ValueError("Expected an official Ozon workbook URL")
    book = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    try:
        if len(book.worksheets) != 1:
            raise ValueError("Review the workbook layout before importing")
        sheet = book.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        schemes = next(rows)
        headers = next(rows)
        if schemes[3] != "FBO" or tuple(normalized_name(str(value)) for value in headers[:6]) != (
            "основная категория",
            "категория",
            "тип товара",
            "до 100 руб.",
            "свыше 100 до 300 руб.",
            "свыше 300 руб.",
        ):
            raise ValueError("Unexpected tariff columns")
        items: list[OzonTariff] = []
        for row_number, row in enumerate(rows, start=OZON_TARIFFS.workbook_header_rows + 1):
            if not any(value is not None for value in row):
                continue
            names = tuple(str(value).strip() if value is not None else "" for value in row[:3])
            identity = json.dumps([normalized_name(value) for value in names], ensure_ascii=False)
            rates = tuple(
                (Decimal(str(value)) * OZON_COMMISSIONS.max_percent).quantize(
                    Decimal(OZON_TARIFFS.rate_quantum)
                )
                for value in row[3:6]
            )
            items.append(
                OzonTariff(
                    id=hashlib.sha256(identity.encode()).hexdigest(),
                    group=names[0],
                    category=names[1],
                    type_name=names[2],
                    fbo_up_to_100=rates[0],
                    fbo_up_to_300=rates[1],
                    fbo_over_300=rates[2],
                    source_row=row_number,
                )
            )
        return OzonTariffCatalog(
            effective_from=effective_from,
            verified_on=verified_on,
            source_url=source_url,
            source_page=OZON_TARIFFS.source_page,
            source_sha256=hashlib.sha256(workbook.read_bytes()).hexdigest(),
            worksheet=sheet.title,
            items=tuple(items),
        )
    finally:
        book.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--effective-from", required=True, type=date.fromisoformat)
    parser.add_argument("--verified-on", required=True, type=date.fromisoformat)
    parser.add_argument("--output", type=Path, default=settings.ozon_tariffs_path)
    args = parser.parse_args()
    catalog = import_catalog(args.workbook, args.source_url, args.effective_from, args.verified_on)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    staged = args.output.with_suffix(".tmp")
    staged.write_text(catalog.model_dump_json(indent=2) + "\n", encoding="utf-8")
    staged.replace(args.output)
    print(f"Imported {len(catalog.items)} tariffs, effective {catalog.effective_from}")


if __name__ == "__main__":
    main()
