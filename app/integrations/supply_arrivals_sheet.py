"""Read the arrivals register; Google Sheets remains the source of truth."""

import math
import re
from datetime import date, timedelta
from urllib.parse import urlsplit

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession

from app.config import settings
from app.core.stores import STORES
from app.dto.supply_arrivals import SupplyArrival
from app.ff_import.google_service_account import (
    CredentialsUnavailableError,
    get_credentials,
    get_service_account_email,
)

TEXT_COLUMNS = {
    "project": "Проект",
    "order": "Номер заказа",
    "group": "Группа",
    "file": "Ссылка на файл заказа",
    "category": "Категория",
    "warehouse": "Склад ФФ",
    "status": "Статус поставки",
    "shipping": "Способ",
}
NUMBER_COLUMNS = {"volume": "Обьем", "weight": "Вес", "boxes": "Коробки"}
ARRIVAL_COLUMN = "План даты прихода в МСК"
READ_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


class SupplySheetError(RuntimeError):
    pass


def header_key(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").replace("объем", "обьем").split())


STORE_ALIASES = {
    **{header_key(slug): slug for slug in STORES},
    **{header_key(store.name): slug for slug, store in STORES.items()},
    "хочушар": "rimili",
    "bth": "trusthome",
    "гоголь": "gogol",
    "ракета": "gogol",
}


def cell_value(cell: dict) -> object:
    effective = cell.get("effectiveValue", {})
    if "errorValue" in effective:
        raise ValueError("ошибка формулы")
    return effective.get("numberValue", effective.get("stringValue", cell.get("formattedValue", "")))


def cell_text(cell: dict) -> str:
    return str(cell_value(cell)).strip()


def safe_link(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.hostname and not (parts.username or parts.password):
            return value
    except ValueError:
        pass
    return ""


def parse_number(value: object) -> float | None:
    if str(value).strip() in {"", "—", "-"}:
        return None
    number = float(re.sub(r"\s", "", str(value)).replace(",", "."))
    if not math.isfinite(number) or number < 0:
        raise ValueError("ожидалось неотрицательное число")
    return number


def parse_date(value: object) -> date | None:
    if str(value).strip() in {"", "—", "-"}:
        return None
    if isinstance(value, (int, float)) or re.fullmatch(r"\d{5}(?:\.\d+)?", str(value)):
        result = date(1899, 12, 30) + timedelta(days=int(float(value)))
        if not 2000 <= result.year <= 2100:
            raise ValueError("дата вне диапазона 2000–2100")
        return result
    text = str(value).strip()
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        day, month, year = map(int, text.split("."))
        return date(year, month, day)
    return date.fromisoformat(text)


def parse_sheet(sheet: dict) -> list[SupplyArrival]:
    rows = sheet.get("data", [{}])[0].get("rowData", [])
    required = {
        *TEXT_COLUMNS.values(),
        ARRIVAL_COLUMN,
        *(
            f"{label} ({source})"
            for label in NUMBER_COLUMNS.values()
            for source in ("поставщик", "перевозчик")
        ),
    }
    for header_index, row in enumerate(rows[:20]):
        keys = [header_key(str(cell.get("formattedValue", ""))) for cell in row.get("values", [])]
        if all(header_key(label) in keys for label in required):
            if any(keys.count(header_key(label)) != 1 for label in required):
                raise SupplySheetError("В реестре повторяются обязательные заголовки колонок")
            columns = {key: index for index, key in enumerate(keys)}
            first_data_row = header_index + 1
            break
    else:
        raise SupplySheetError(
            "В первых 20 строках не найдены заголовки реестра поставок. Проверьте структуру листа."
        )

    result = []
    for index, row in enumerate(rows[first_data_row:], start=first_data_row + 1):
        cells = row.get("values", [])

        def cell(label, cells=cells):
            position = columns[header_key(label)]
            return cells[position] if position < len(cells) else {}

        try:
            order = cell_text(cell(TEXT_COLUMNS["order"]))
        except ValueError as error:
            raise SupplySheetError(f"Строка {index}: ошибка в номере заказа") from error
        if not order:
            continue
        warnings = []
        fields = {"row": index, "order": order}
        for field, label in TEXT_COLUMNS.items():
            try:
                fields[field] = cell_text(cell(label))
            except ValueError:
                fields[field] = ""
                warnings.append(f"{label}: ошибка формулы")
        fields["store_slug"] = STORE_ALIASES.get(header_key(fields["project"]), "")
        if not fields["store_slug"]:
            warnings.append("Проект не сопоставлен с кабинетом")
        link_cell = cell(TEXT_COLUMNS["file"])
        fields["file"] = safe_link(link_cell.get("hyperlink") or fields["file"])
        try:
            fields["arrival"] = parse_date(cell_value(cell(ARRIVAL_COLUMN)))
        except (ValueError, OverflowError):
            fields["arrival"] = None
            warnings.append("План прихода: некорректная дата")
        for field, label in NUMBER_COLUMNS.items():
            # A literal zero is actual data. A broken carrier formula must not be
            # silently replaced by an estimate from the supplier.
            try:
                value = parse_number(cell_value(cell(f"{label} (перевозчик)")))
                if value is None:
                    value = parse_number(cell_value(cell(f"{label} (поставщик)")))
                fields[field] = value
            except (ValueError, OverflowError):
                fields[field] = None
                warnings.append(f"{label}: некорректное число")
        result.append(SupplyArrival(**fields, warnings=tuple(warnings)))
    return result


def fetch_arrivals() -> tuple[str, list[SupplyArrival]]:
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{settings.supply_arrivals_spreadsheet_id}"
    try:
        credentials = get_credentials().with_scopes([READ_SCOPE])
        with AuthorizedSession(credentials) as session:

            def get(params):
                response = session.get(url, params=params, timeout=45)
                if response.status_code == 403:
                    raise SupplySheetError(
                        f"Нет доступа к Google Таблице. Добавьте читателя {get_service_account_email()}."
                    )
                if response.status_code == 404:
                    raise SupplySheetError("Google Таблица не найдена или недоступна сервисному аккаунту")
                if not response.ok:
                    raise SupplySheetError(
                        f"Google Таблицы временно недоступны (HTTP {response.status_code})"
                    )
                return response.json()

            metadata = get({"fields": "sheets.properties(sheetId,title,gridProperties)"})
            properties = next(
                (
                    s["properties"]
                    for s in metadata.get("sheets", [])
                    if s["properties"]["sheetId"] == settings.supply_arrivals_sheet_gid
                ),
                None,
            )
            if properties is None:
                raise SupplySheetError("Лист поставок с указанным gid не найден")
            title = properties["title"]
            row_count = properties["gridProperties"]["rowCount"]
            column_count = properties["gridProperties"]["columnCount"]
            if row_count > 20_000 or column_count > 256:
                raise SupplySheetError(
                    "Лист превышает лимит 20 000 строк / 256 колонок. Уменьшите пустую область листа."
                )
            data = get(
                {
                    "ranges": f"'{title.replace(chr(39), chr(39) * 2)}'!1:{row_count}",
                    "fields": "sheets(properties(sheetId),data(rowData(values(effectiveValue,formattedValue,hyperlink))))",
                }
            )
            sheet = next(s for s in data["sheets"] if s["properties"]["sheetId"] == properties["sheetId"])
            return title, parse_sheet(sheet)
    except CredentialsUnavailableError as error:
        raise SupplySheetError("На сервере не настроен ключ сервисного аккаунта Google") from error
    except (requests.RequestException, GoogleAuthError) as error:
        raise SupplySheetError(
            "Не удалось подключиться к Google Таблицам. Повторите обновление позже."
        ) from error
