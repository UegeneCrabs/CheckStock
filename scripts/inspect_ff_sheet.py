import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ff_import import google_service_account

DEFAULT_SHEET = "1rJdvA6ASic31W456eRyprqPCvNS6iBiEr-vOpqVb_KY"


def sheet_id_from(value: str) -> str:

    value = (value or "").strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", value)
    return match.group(1) if match else value


SHEET_ID = sheet_id_from(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SHEET)

PREVIEW_ROWS = 6
PREVIEW_COLS = 12


def main() -> None:
    if not google_service_account.has_credentials():
        print(f"Нет ключа сервисного аккаунта: {google_service_account.CREDENTIALS_PATH}")
        return

    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        print("Нет пакета google-api-python-client — установи: pip install -r requirements.txt")
        return

    creds = google_service_account.get_credentials()
    service = build("sheets", "v4", credentials=creds, cache_discovery=False)

    try:
        meta = (
            service.spreadsheets()
            .get(
                spreadsheetId=SHEET_ID,
                fields="properties.title,sheets.properties(sheetId,title,gridProperties)",
            )
            .execute()
        )
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        if status == 403:
            print(
                "Нет доступа к таблице. Расшарь её на сервисный аккаунт "
                f"{google_service_account.get_service_account_email()} с правами «Читатель»."
            )
        else:
            print(f"Ошибка Google Sheets API: {e}")
        return

    print(f"=== Документ: {meta.get('properties', {}).get('title')} ===")
    sheets = meta.get("sheets", [])
    print(f"Листов: {len(sheets)}\n")

    for sheet in sheets:
        props = sheet.get("properties", {})
        title = props.get("title")
        grid = props.get("gridProperties", {})
        print(
            f"--- Лист: {title!r}  (gid={props.get('sheetId')}, "
            f"{grid.get('rowCount')}x{grid.get('columnCount')}) ---"
        )

        try:
            values = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=SHEET_ID,
                    range=f"'{title}'!A1:Z{PREVIEW_ROWS}",
                )
                .execute()
                .get("values", [])
            )
        except HttpError as e:
            print(f"  не удалось прочитать: {e}")
            print()
            continue

        for row in values:
            print("   ", [c[:28] for c in row[:PREVIEW_COLS]])
        print()


if __name__ == "__main__":
    main()
