"""Import Yandex Market cost prices from the public 1C export sheet."""

import csv
from io import StringIO
import logging
from urllib.request import urlopen

from app import db
from app.core.stores import STORES
from app.economics.sources import purchase_prices as source
from app.repositories import yandex_source_values as repository

logger = logging.getLogger(__name__)
MARKETPLACE = "YANDEX MARKET"
SHEET_TITLE = "Для выгрузки"
SHEET_GID = "1943211031"
PUBLIC_SHEET_EXPORT_URL = (
    "https://docs.google.com/spreadsheets/d/"
    f"{source.SOURCE_SPREADSHEET_ID}/gviz/tq?tqx=out:csv&gid={SHEET_GID}"
)
SOURCE_COLUMNS = {
    "article": "артикул",
    "purchase_price": "себестоимость",
}


def _number(value: object) -> float | None:

    return source._number(str(value) if value is not None else None)


def _find_header(rows: list[list[object]]) -> tuple[int, dict[str, int]]:
    for header_index, row in enumerate(rows[:20]):
        columns = {source._header_key(value): index for index, value in enumerate(row)}
        if set(SOURCE_COLUMNS.values()).issubset(columns):
            return header_index, columns
    raise source.SourceDataError(
        "В листе «Для выгрузки» не найдены колонки «Артикул» и «Себестоимость»"
    )


def _public_export_sheet() -> list[dict]:
    """Read the public two-column export when no service account is configured."""

    try:
        with urlopen(PUBLIC_SHEET_EXPORT_URL, timeout=30) as response:
            rows = list(csv.reader(StringIO(response.read().decode("utf-8-sig"))))
    except Exception as error:
        raise source.SourceDataError(
            "не удалось получить публичную вкладку «Для выгрузки» Google Sheets"
        ) from error
    return [{"sheet_id": int(SHEET_GID), "title": SHEET_TITLE, "rows": rows}]


def fetch_source_sheet() -> list[dict]:
    """Prefer the authenticated API, but keep the public export self-contained."""

    if source.google_service_account.has_credentials():
        return source.fetch_sheet_rows(SHEET_TITLE)
    return _public_export_sheet()


def parse_source_values(sheets: list[dict], existing_values: list[dict]) -> dict:
    if len(sheets) != 1 or source._text(sheets[0].get("title")).casefold() != SHEET_TITLE.casefold():
        raise source.SourceDataError("Для ЯМ нужен ровно один лист «Для выгрузки»")
    sheet = sheets[0]
    rows = list(sheet.get("rows") or [])
    header_index, columns = _find_header(rows)
    prices_by_article: dict[str, tuple[float, int]] = {}
    source_rows = duplicates = 0
    for source_row, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        article_column = columns[SOURCE_COLUMNS["article"]]
        price_column = columns[SOURCE_COLUMNS["purchase_price"]]
        article = source._identifier(row[article_column]) if article_column < len(row) else ""
        price = _number(row[price_column]) if price_column < len(row) else None
        if not article or price is None:
            continue
        source_rows += 1
        previous = prices_by_article.get(article)
        if previous is not None:
            if previous[0] != price:
                raise source.SourceDataError(
                    f"Для артикула {article} указана разная себестоимость "
                    f"в строках {previous[1]} и {source_row}"
                )
            duplicates += 1
            continue
        prices_by_article[article] = (price, source_row)

    parsed_rows: list[dict] = []
    missing_articles: list[str] = []
    matched = 0
    for existing in existing_values:
        article = source._identifier(existing.get("article"))
        price_data = prices_by_article.get(article)
        if price_data is None:
            missing_articles.append(article)
            # Retain the product-to-store binding, but do not carry a price
            # over from the old sheet after the source of truth has changed.
            parsed_rows.append(
                {
                    **existing,
                    "purchase_price": None,
                    "source_sheet_id": int(sheet["sheet_id"]),
                    "source_sheet_title": SHEET_TITLE,
                    # The database records the source row as required data;
                    # zero explicitly means that the article is absent from
                    # the current export.
                    "source_row": 0,
                }
            )
            continue
        price, source_row = price_data
        matched += 1
        parsed_rows.append(
            {
                **existing,
                "purchase_price": price,
                "source_sheet_id": int(sheet["sheet_id"]),
                "source_sheet_title": SHEET_TITLE,
                "source_row": source_row,
            }
        )
    return {
        "rows": parsed_rows,
        "sheet_count": 1,
        "source_rows": source_rows,
        "matched": matched,
        "unmatched": len(missing_articles),
        "duplicates": duplicates,
        "missing_articles": missing_articles,
    }


def sync_all(sheets: list[dict] | None = None) -> dict:
    now = source._now()
    try:
        existing_values = repository.list_values()
        report = parse_source_values(sheets if sheets is not None else fetch_source_sheet(), existing_values)
        saved = repository.replace_values(report.pop("rows"), now)
    except Exception as error:
        for slug in STORES:
            db.record_sync_health(slug, MARKETPLACE, "unit_economics_1c_source", False, str(error), now)
        raise
    for slug in STORES:
        db.record_sync_health(slug, MARKETPLACE, "unit_economics_1c_source", True, None, now)
    logger.info("yandex_source_sync saved=%s source_rows=%s", saved, report["source_rows"])
    return {"ok": True, "saved": saved, "synced_at": now, **report}
