from __future__ import annotations

import ftplib
import json
import logging
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

from app.config import settings
from app.domain import MOSCOW_TIMEZONE
from app.ff_import import google_service_account

logger = logging.getLogger(__name__)

SOURCE_SPREADSHEET_ID = "1q0WL6OB3Edh2O1ogqx7CK3MAij3O6xjD6gE0i3q3qEY"
SOURCE_SHEETS = {
    "wb": (
        "RIMILI WB",
        "SOKOLOFF и TRUSTHOME WB",
        "TOYKA WB",
        "TRIS WB",
        "ROCKKIDDO WB",
        "GOGOL WB",
    ),
    "ozon": (
        "RIMILI OZON",
        "SOKOLOFF и TRUSTHOME OZON",
        "TRIS OZON",
        "ROCKKIDDO OZON",
        "GOGOL OZON",
    ),
}
JOB_PLATFORMS = {
    "ftp_wb_export": "wb",
    "ftp_ozon_export": "ozon",
}
EXPECTED_HEADERS = {
    "wb": ("АртикулВБ", "Себес, руб", "Проч.затр, руб", "Артикул поставщика внешний"),
    "ozon": ("Артикул ОЗОН", "Себес, руб", "Проч.затр, руб"),
}
_SPACE_CHARS = re.compile(r"[\s\xa0\u202f]+")
_EXPORT_LOCKS = {platform: threading.Lock() for platform in SOURCE_SHEETS}


class FTPExportError(RuntimeError):
    pass


class FTPExportBusyError(FTPExportError):
    pass


@dataclass(frozen=True, slots=True)
class FTPTarget:
    host: str
    username: str
    password: str
    filename: str


def platform_for_job(job_name: str) -> str | None:
    return JOB_PLATFORMS.get(job_name)


def is_running(platform: str) -> bool:
    lock = _EXPORT_LOCKS.get(platform)
    return bool(lock and lock.locked())


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _clean_number(value: Any) -> Any:
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value
    cleaned = _SPACE_CHARS.sub("", value.strip()).replace(",", ".")
    if not cleaned:
        return value
    try:
        number = float(cleaned)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _valid_value(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text) and not text.startswith("#")


def _identifier(value: object) -> str:
    text = _normalized_text(value).lstrip("'")
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def _find_headers(rows: Sequence[Sequence[object]], expected: Sequence[str]) -> tuple[int, dict[str, int]]:
    for row_index, row in enumerate(rows):
        normalized = [_normalized_text(value) for value in row]
        if all(header in normalized for header in expected):
            header_map = {header: normalized.index(header) for header in expected}
            for optional in ("Тег", "Нет на ОЗОН"):
                if optional in normalized:
                    header_map[optional] = normalized.index(optional)
            return row_index, header_map
    raise FTPExportError(f"Не найдена строка с заголовками: {', '.join(expected)}")


def _quoted_sheet_name(sheet_name: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'"


def fetch_source_rows(platform: str) -> dict[str, list[list[object]]]:
    sheet_names = SOURCE_SHEETS.get(platform)
    if sheet_names is None:
        raise FTPExportError("Поддерживаются только платформы WB и Ozon")
    if not google_service_account.has_credentials():
        raise FTPExportError(
            "Не настроен сервисный аккаунт Google Sheets: "
            f"нет файла {google_service_account.CREDENTIALS_PATH}"
        )
    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as error:
        raise FTPExportError("Не установлены google-api-python-client и google-auth") from error
    try:
        credentials = google_service_account.get_credentials()
    except google_service_account.CredentialsUnavailableError as error:
        raise FTPExportError(str(error)) from error

    service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
    try:
        response = (
            service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=SOURCE_SPREADSHEET_ID,
                ranges=[_quoted_sheet_name(name) for name in sheet_names],
                majorDimension="ROWS",
                valueRenderOption="FORMATTED_VALUE",
            )
            .execute()
        )
    except HttpError as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        if status == 403:
            message = "Нет доступа к исходной Google-таблице"
        elif status == 404:
            message = "Исходная Google-таблица или один из листов не найден"
        else:
            message = f"Google Sheets API вернул ошибку: {error}"
        raise FTPExportError(message) from error

    value_ranges = response.get("valueRanges", [])
    if len(value_ranges) != len(sheet_names):
        raise FTPExportError("Google Sheets API вернул не все запрошенные листы")
    return {
        sheet_name: list(value_range.get("values", []))
        for sheet_name, value_range in zip(sheet_names, value_ranges, strict=True)
    }


def collect_platform_data(
    platform: str,
    source_rows: Mapping[str, Sequence[Sequence[object]]] | None = None,
) -> dict[str, object]:
    sheet_names = SOURCE_SHEETS.get(platform)
    expected_headers = EXPECTED_HEADERS.get(platform)
    if sheet_names is None or expected_headers is None:
        raise FTPExportError("Поддерживаются только платформы WB и Ozon")
    loaded_rows = source_rows if source_rows is not None else fetch_source_rows(platform)

    items: list[dict[str, object]] = []
    missing_costs: dict[str, list[str]] = {}
    missing_other_costs: dict[str, list[str]] = {}
    total_rows = 0
    excluded_rows = 0

    for sheet_name in sheet_names:
        rows = loaded_rows.get(sheet_name)
        if rows is None:
            raise FTPExportError(f'Не получены данные листа "{sheet_name}"')
        try:
            header_index, header_map = _find_headers(rows, expected_headers)
        except FTPExportError as error:
            raise FTPExportError(f'Ошибка листа "{sheet_name}": {error}') from error

        article_header = "Артикул ОЗОН" if platform == "ozon" else "АртикулВБ"
        for row in rows[header_index + 1 :]:
            article_column = header_map[article_header]
            if article_column >= len(row):
                continue
            article = _identifier(row[article_column])
            if not article or article.casefold() == "нет на озон":
                excluded_rows += 1
                continue
            total_rows += 1

            cost_column = header_map["Себес, руб"]
            other_column = header_map["Проч.затр, руб"]
            cost = _clean_number(row[cost_column] if cost_column < len(row) else None)
            other_cost = _clean_number(row[other_column] if other_column < len(row) else None)
            has_cost = _valid_value(cost)
            has_other_cost = _valid_value(other_cost)
            if not has_cost:
                missing_costs.setdefault(sheet_name, []).append(article)
            if not has_other_cost:
                missing_other_costs.setdefault(sheet_name, []).append(article)
            if not has_cost or not has_other_cost:
                continue

            if platform == "ozon":
                item: dict[str, object] = {
                    "product_id": article,
                    "cost_price": cost,
                    "other_costs": other_cost,
                }
            else:
                item = {
                    "nmId": article,
                    "cost_price": cost,
                    "other_costs": other_cost,
                }
                supplier_column = header_map["Артикул поставщика внешний"]
                supplier = row[supplier_column] if supplier_column < len(row) else None
                if _valid_value(supplier):
                    item["supplier_article_number"] = supplier

            tag_column = header_map.get("Тег")
            tag = row[tag_column] if tag_column is not None and tag_column < len(row) else None
            if _valid_value(tag):
                item["tag"] = tag
            items.append(item)

    return {
        "items": items,
        "total_rows": total_rows,
        "missing_costs": missing_costs,
        "missing_other_costs": missing_other_costs,
        "excluded_rows": excluded_rows,
    }


def _ftp_target(platform: str) -> FTPTarget:
    if platform == "wb":
        return FTPTarget(
            host=settings.ftp_host_wb,
            username=settings.ftp_user_wb,
            password=settings.ftp_password_wb,
            filename="data.json",
        )
    if platform == "ozon":
        return FTPTarget(
            host=settings.ftp_host_ozon,
            username=settings.ftp_user_ozon,
            password=settings.ftp_password_ozon,
            filename="data_ozon.json",
        )
    raise FTPExportError("Поддерживаются только платформы WB и Ozon")


def upload_to_ftp(platform: str, content: Mapping[str, object]) -> str:
    target = _ftp_target(platform)
    if not all((target.host, target.username, target.password)):
        raise FTPExportError(f"Не указаны FTP-реквизиты для платформы {platform.upper()}")
    content_bytes = json.dumps(content, ensure_ascii=False).encode("utf-8")

    def store(passive: bool, *, use_tls: bool = False, protect_data: bool = True) -> None:
        ftp_class = ftplib.FTP_TLS if use_tls else ftplib.FTP
        ftp = ftp_class(target.host, timeout=settings.ftp_timeout_seconds)
        try:
            ftp.login(target.username, target.password)
            if use_tls:
                ftp.prot_p() if protect_data else ftp.prot_c()
            ftp.set_pasv(passive)
            ftp.storbinary(f"STOR {target.filename}", BytesIO(content_bytes))
            ftp.quit()
        finally:
            try:
                ftp.close()
            except ftplib.all_errors:
                pass

    def store_with_transfer_mode(*, use_tls: bool, protect_data: bool = True) -> None:
        if settings.ftp_mode == "passive":
            store(True, use_tls=use_tls, protect_data=protect_data)
        elif settings.ftp_mode == "active":
            store(False, use_tls=use_tls, protect_data=protect_data)
        else:
            try:
                store(True, use_tls=use_tls, protect_data=protect_data)
            except ftplib.error_temp as error:
                if not str(error).startswith("425"):
                    raise
                store(False, use_tls=use_tls, protect_data=protect_data)

    def store_with_tls_protection() -> None:
        if settings.ftp_prot == "private":
            store_with_transfer_mode(use_tls=True, protect_data=True)
        elif settings.ftp_prot == "clear":
            store_with_transfer_mode(use_tls=True, protect_data=False)
        else:
            try:
                store_with_transfer_mode(use_tls=True, protect_data=True)
            except ftplib.all_errors as error:
                if "UNEXPECTED_EOF_WHILE_READING" not in str(error).upper():
                    raise
                store_with_transfer_mode(use_tls=True, protect_data=False)

    try:
        if settings.ftp_tls == "on":
            store_with_tls_protection()
        elif settings.ftp_tls == "off":
            store_with_transfer_mode(use_tls=False)
        else:
            try:
                store_with_transfer_mode(use_tls=False)
            except ftplib.error_perm as error:
                if "USE AUTH FIRST" not in str(error).upper():
                    raise
                store_with_tls_protection()
    except ftplib.all_errors as error:
        raise FTPExportError(f"FTP {platform.upper()}: {error}") from error
    return target.filename


def run_platform(platform: str) -> dict[str, object]:
    lock = _EXPORT_LOCKS.get(platform)
    if lock is None:
        raise FTPExportError("Поддерживаются только платформы WB и Ozon")
    if not lock.acquire(blocking=False):
        raise FTPExportBusyError(f"Выгрузка FTP {platform.upper()} уже выполняется")
    try:
        report = collect_platform_data(platform)
        items = list(report["items"])
        if not items:
            raise FTPExportError(f"Для FTP {platform.upper()} не найдено ни одного полного товара")
        generated_at = datetime.now(MOSCOW_TIMEZONE).isoformat(timespec="seconds")
        filename = upload_to_ftp(platform, {"date": generated_at, "items": items})
        missing_cost_count = sum(len(values) for values in report["missing_costs"].values())
        missing_other_count = sum(len(values) for values in report["missing_other_costs"].values())
        result = {
            "ok": True,
            "platform": platform,
            "filename": filename,
            "items": len(items),
            "source_rows": report["total_rows"],
            "missing_costs": missing_cost_count,
            "missing_other_costs": missing_other_count,
            "generated_at": generated_at,
        }
        logger.info(
            "ftp_export_completed platform=%s items=%s source_rows=%s missing_costs=%s "
            "missing_other_costs=%s filename=%s",
            platform,
            result["items"],
            result["source_rows"],
            missing_cost_count,
            missing_other_count,
            filename,
        )
        return result
    finally:
        lock.release()
