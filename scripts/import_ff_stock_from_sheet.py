import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db
from app.ff_import import google_service_account
from app.ff_import.importer import _normalize_cell, _parse_quantity
from app.stores import STORES

DEFAULT_SHEET = "1rJdvA6ASic31W456eRyprqPCvNS6iBiEr-vOpqVb_KY"


def sheet_id_from(value: str) -> str:

    value = (value or "").strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", value)
    return match.group(1) if match else value


ARTICLE_HEADERS = ("артикул",)
BARCODE_HEADERS = ("баркод",)


def _norm(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())


def _build_service():
    if not google_service_account.has_credentials():
        raise SystemExit(f"Нет ключа сервисного аккаунта: {google_service_account.CREDENTIALS_PATH}")
    try:
        from googleapiclient.discovery import build
    except ImportError as error:
        raise SystemExit("Нет пакета google-api-python-client — pip install -r requirements.txt") from error
    return build("sheets", "v4", credentials=google_service_account.get_credentials(), cache_discovery=False)


def _store_slug_by_sheet_title(title: str) -> str | None:

    target = _norm(title)
    for slug, store in STORES.items():
        if _norm(store["name"]) == target or _norm(slug) == target:
            return slug
    return None


def match_fulfillment(column: str, known_ffs: dict[str, str]) -> str | None:

    name = _norm(column)
    if not name:
        return None

    if name in known_ffs:
        return known_ffs[name]

    matches = [full for key, full in known_ffs.items() if name in key or key in name]
    if len(matches) == 1:
        return matches[0]
    return None


def _parse_sheet(rows: list[list[str]], known_ffs: dict[str, str]) -> tuple[dict, list[str], list[str]]:

    header_idx = None
    for idx, row in enumerate(rows[:10]):
        cells = [_norm(c) for c in row]
        if any(h in cells for h in ARTICLE_HEADERS) and any(h in cells for h in BARCODE_HEADERS):
            header_idx = idx
            break
    if header_idx is None:
        raise ValueError("не нашёл строку заголовков (Артикул / Баркод)")

    header = rows[header_idx]
    col_article = col_barcode = None
    ff_columns: dict[int, str] = {}
    unknown: list[str] = []

    SKIP = ("наименование товара", "наименование", "название", "")

    for i, raw in enumerate(header):
        name = _norm(raw)
        if name in ARTICLE_HEADERS:
            col_article = i
        elif name in BARCODE_HEADERS:
            col_barcode = i
        elif name in SKIP:
            continue
        else:
            matched = match_fulfillment(raw, known_ffs)
            if matched:
                ff_columns[i] = matched
            else:
                unknown.append(raw)

    if col_article is None or col_barcode is None:
        raise ValueError("в заголовках нет колонки Артикул или Баркод")
    if not ff_columns:
        raise ValueError("в заголовках не нашлось ни одной колонки с известным фулфилментом")

    data: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows[header_idx + 1 :]:
        if not row or all(not _normalize_cell(c) for c in row):
            continue
        article = _normalize_cell(row[col_article]) if col_article < len(row) else ""
        barcode = _normalize_cell(row[col_barcode]) if col_barcode < len(row) else ""
        if not article and not barcode:
            continue

        per_ff: dict[str, int] = {}
        for col_idx, ff_name in ff_columns.items():
            raw = _normalize_cell(row[col_idx]) if col_idx < len(row) else ""
            qty = _parse_quantity(raw)
            per_ff[ff_name] = max(qty, 0)
        data[(barcode, article)] = per_ff

    return data, list(ff_columns.values()), unknown


def main() -> None:
    parser = argparse.ArgumentParser(description="Загрузка снимка остатков ФФ из Google Таблицы")
    parser.add_argument("--sheet", default=DEFAULT_SHEET, help="ссылка на таблицу или её идентификатор")
    parser.add_argument(
        "--mp", default=db.DEFAULT_MARKETPLACE, help=f"маркетплейс: {', '.join(db.MARKETPLACES)}"
    )
    parser.add_argument("--apply", action="store_true", help="записать в БД, а не только показать")
    args = parser.parse_args()

    marketplace = args.mp.strip().upper()
    if marketplace not in db.MARKETPLACES:
        raise SystemExit(f"Неизвестный маркетплейс {marketplace!r}. Доступны: {', '.join(db.MARKETPLACES)}")

    sheet_id = sheet_id_from(args.sheet)
    apply_changes = args.apply

    db.init_db()
    db.seed_defaults()

    print(f"Таблица    : {sheet_id}")
    print(f"Маркетплейс: {marketplace}")
    print(f"Режим      : {'ЗАПИСЬ В БД' if apply_changes else 'только просмотр'}")

    known_ffs = {_norm(name): name for name in db.get_fulfillments()}
    print(f"Известных фулфилментов в БД: {len(known_ffs)}")

    service = _build_service()
    from googleapiclient.errors import HttpError

    try:
        meta = (
            service.spreadsheets()
            .get(spreadsheetId=sheet_id, fields="sheets.properties(sheetId,title)")
            .execute()
        )
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        if status == 403:
            raise SystemExit(
                "Нет доступа к таблице. Расшарь её на "
                f"{google_service_account.get_service_account_email()} с правами «Читатель»."
            ) from e
        raise SystemExit(f"Ошибка Google Sheets API: {e}") from e

    now = datetime.now(UTC).isoformat()
    grand_written = 0
    grand_skipped = 0

    for sheet in meta.get("sheets", []):
        title = sheet.get("properties", {}).get("title", "")
        slug = _store_slug_by_sheet_title(title)
        if slug is None:
            print(f"\n--- Лист {title!r}: не похож на магазин, пропускаю")
            continue

        try:
            values = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=sheet_id, range=f"'{title}'!A1:ZZ20000")
                .execute()
                .get("values", [])
            )
        except HttpError as e:
            print(f"\n--- Лист {title!r}: не удалось прочитать: {e}")
            continue

        try:
            data, ff_names, unknown_columns = _parse_sheet(values, known_ffs)
        except ValueError as e:
            print(f"\n--- Лист {title!r} ({slug}): {e}")
            continue

        catalog = db.get_catalog_items(slug, marketplace)
        by_barcode = {item["barcode"]: item["article"] for item in catalog}
        known_articles = {item["article"] for item in catalog}

        written = 0
        skipped_rows = []
        total_qty = 0

        for (barcode, article), per_ff in data.items():
            target = by_barcode.get(barcode)
            if target is None and article in known_articles:
                target = article
            if target is None:
                skipped_rows.append(article or barcode)
                continue

            for ff_name, qty in per_ff.items():
                if apply_changes:
                    db.upsert_ff_stock(slug, target, ff_name, qty, now, marketplace)
                written += 1
                total_qty += qty

        grand_written += written
        grand_skipped += len(skipped_rows)

        print(f"\n--- Лист {title!r} -> магазин {slug} ---")
        print(f"    товаров в листе : {len(data)}")
        print(f"    колонок ФФ      : {len(ff_names)} ({', '.join(ff_names)})")
        print(f"    записей ff_stock: {written} (суммарный остаток {total_qty})")
        if unknown_columns:
            print(
                f"    НЕ РАСПОЗНАНЫ колонки: {', '.join(unknown_columns)}"
                " — такого фулфилмента нет в базе, остатки по нему пропущены"
            )
        if skipped_rows:
            preview = ", ".join(skipped_rows[:5])
            more = f" и ещё {len(skipped_rows) - 5}" if len(skipped_rows) > 5 else ""
            print(f"    нет в каталоге  : {len(skipped_rows)} ({preview}{more})")

    print("\n==================================================")
    print(f"Всего записей ff_stock : {grand_written}")
    print(f"Всего не в каталоге    : {grand_skipped}")
    if not apply_changes:
        print("\nЭто был просмотр. Чтобы записать, добавьте --apply")
    if apply_changes:
        print("Изменения ЗАПИСАНЫ в базу.")
    else:
        print("Это был предпросмотр. Чтобы записать: python scripts/import_ff_stock_from_sheet.py --apply")


if __name__ == "__main__":
    main()
