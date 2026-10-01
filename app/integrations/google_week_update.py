"""Write the last completed ISO week to explicitly configured Google Sheet cells."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from app.core.domain import MOSCOW_TIMEZONE
from app.ff_import import google_service_account
from app.jobs import locks
from app.repositories import google_week_update as repository
from app.repositories.google_week_update import WeekUpdateSettings

JOB_NAME = "google_week_update"
RETRY_DELAY = timedelta(minutes=5)


def _now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(MOSCOW_TIMEZONE)).astimezone(MOSCOW_TIMEZONE)


def last_completed_week(now: datetime | None = None) -> str:
    today = _now(now).date()
    previous_sunday = today - timedelta(days=today.weekday() + 1)
    year, week, _ = previous_sunday.isocalendar()
    return f"W{week:02d} {year}"


def spreadsheet_id(url: str) -> str:
    parsed = urlsplit(url)
    match = re.fullmatch(r"/spreadsheets/d/([a-zA-Z0-9_-]+)(?:/edit)?/?", parsed.path)
    if parsed.scheme != "https" or parsed.netloc != "docs.google.com" or not match:
        raise ValueError("Укажите ссылку вида https://docs.google.com/spreadsheets/d/…/edit")
    return match[1]


def parse_cells(value: str) -> tuple[str, ...]:
    cells = tuple(dict.fromkeys(re.split(r"[\s,;]+", value.strip().upper())))
    if not 1 <= len(cells) <= 20 or any(
        not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", cell) for cell in cells
    ):
        raise ValueError("Укажите от 1 до 20 отдельных ячеек через запятую, например F4, AI10")
    return cells


def validate(settings: WeekUpdateSettings) -> WeekUpdateSettings:
    doc_id = spreadsheet_id(settings.spreadsheet_url.strip())
    sheet = settings.sheet_name.strip()
    if not sheet or len(sheet) > 100 or any(ord(char) < 32 for char in sheet):
        raise ValueError("Укажите название листа длиной от 1 до 100 символов")
    if settings.weekday not in range(7):
        raise ValueError("Выберите день недели")
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", settings.run_time):
        raise ValueError("Укажите время в формате ЧЧ:ММ")
    return replace(
        settings,
        spreadsheet_url=f"https://docs.google.com/spreadsheets/d/{doc_id}/edit",
        sheet_name=sheet,
        cells=parse_cells(",".join(settings.cells)),
    )


def save_settings(
    settings: WeekUpdateSettings,
    now: datetime | None = None,
    *,
    search_enabled: bool | None = None,
    sales_enabled: bool | None = None,
) -> None:
    normalized = validate(settings)
    with locks.hold(JOB_NAME):
        repository.save_settings(
            replace(normalized, updated_at=_now(now).isoformat()),
            search_enabled=search_enabled,
            sales_enabled=sales_enabled,
        )


def scheduled_at(settings: WeekUpdateSettings, now: datetime) -> datetime:
    current = _now(now)
    hour, minute = map(int, settings.run_time.split(":"))
    slot = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    slot -= timedelta(days=(slot.weekday() - settings.weekday) % 7)
    if slot > current:
        slot -= timedelta(days=7)
    return slot


def _timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(MOSCOW_TIMEZONE) if value else None


def pending_run(settings: WeekUpdateSettings, now: datetime) -> bool:
    if not settings.enabled or not settings.updated_at:
        return False
    slot = scheduled_at(settings, now)
    if slot <= _timestamp(settings.updated_at):
        return False
    success = _timestamp(settings.last_success_slot)
    return success is None or success < slot


def update_is_due(settings: WeekUpdateSettings, now: datetime) -> bool:
    if not pending_run(settings, now):
        return False
    attempt = _timestamp(settings.last_attempt_at)
    return attempt is None or _now(now) - attempt >= RETRY_DELAY


def is_due(settings: WeekUpdateSettings | None = None, now: datetime | None = None) -> bool:
    from app.integrations import google_week_sales, google_week_search

    settings = settings or repository.get_settings()
    current = _now(now)
    if pending_run(settings, current):
        # A failed write must finish before a scheduled search reads the source week.
        return update_is_due(settings, current)
    return google_week_sales.is_due(settings, now=current) or google_week_search.is_due(settings, now=current)


def next_run_at(settings: WeekUpdateSettings, now: datetime | None = None) -> datetime | None:
    if not settings.enabled or not settings.updated_at:
        return None
    current = _now(now)
    slot = scheduled_at(settings, current)
    success = _timestamp(settings.last_success_slot)
    if slot > _timestamp(settings.updated_at) and (success is None or success < slot):
        attempt = _timestamp(settings.last_attempt_at)
        return max(current, attempt + RETRY_DELAY) if attempt else current
    return slot + timedelta(days=7)


def _google_service(*, read_only: bool = False):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    if not google_service_account.has_credentials():
        raise ValueError("Не настроен сервисный аккаунт Google Таблиц")
    credentials = google_service_account.get_credentials()
    if read_only:
        credentials = credentials.with_scopes(["https://www.googleapis.com/auth/spreadsheets.readonly"])
    return build(
        "sheets",
        "v4",
        cache_discovery=False,
        http=AuthorizedHttp(credentials, http=httplib2.Http(timeout=30)),
    )


def _write(settings: WeekUpdateSettings, value: str) -> None:
    service = _google_service()
    doc_id = spreadsheet_id(settings.spreadsheet_url)
    metadata = (
        service.spreadsheets()
        .get(spreadsheetId=doc_id, fields="sheets(properties(sheetId,title,gridProperties),merges)")
        .execute(num_retries=2)
    )
    sheet = next(
        (item for item in metadata.get("sheets", []) if item["properties"]["title"] == settings.sheet_name),
        None,
    )
    if sheet is None:
        raise ValueError(f"Лист «{settings.sheet_name}» не найден. Проверьте название в настройках")
    grid = sheet["properties"]["gridProperties"]
    for cell in settings.cells:
        letters, digits = re.fullmatch(r"([A-Z]+)([0-9]+)", cell).groups()
        column = 0
        for letter in letters:
            column = column * 26 + ord(letter) - ord("A") + 1
        row = int(digits)
        if row > grid["rowCount"] or column > grid["columnCount"]:
            raise ValueError(f"Ячейка {cell} находится за границами листа")
        for merge in sheet.get("merges", []):
            if (
                merge.get("startRowIndex", 0) <= row - 1 < merge["endRowIndex"]
                and merge.get("startColumnIndex", 0) <= column - 1 < merge["endColumnIndex"]
            ):
                if (row - 1, column - 1) != (merge.get("startRowIndex", 0), merge.get("startColumnIndex", 0)):
                    raise ValueError(f"{cell} входит в объединение. Укажите его верхнюю левую ячейку")
    quoted_sheet = "'" + settings.sheet_name.replace("'", "''") + "'"
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=doc_id,
        body={
            "valueInputOption": "RAW",
            "data": [{"range": f"{quoted_sheet}!{cell}", "values": [[value]]} for cell in settings.cells],
        },
    ).execute(num_retries=2)


def _run(*, manual: bool, now: datetime | None = None) -> dict:
    """Caller holds JOB_NAME through run_tracked, shared with settings saves."""
    settings = repository.get_settings()
    current = _now(now)
    if not manual and not update_is_due(settings, current):
        return {"skipped": True}
    if not settings.updated_at:
        raise ValueError("Сначала сохраните настройки обновления недели")
    settings = validate(settings)
    value = last_completed_week(current)
    slot = scheduled_at(settings, current).isoformat()
    attempted_at = current.isoformat()
    repository.record_attempt(attempted_at)
    try:
        _write(settings, value)
    except Exception as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        if status:
            message = (
                f"Google Таблицы: HTTP {status}. Проверьте доступ сервисного аккаунта и повторите запуск"
            )
        elif isinstance(error, ValueError):
            message = str(error)
        else:
            message = f"Не удалось обновить неделю ({type(error).__name__}). Повторите запуск"
        repository.record_result(attempted_at, slot=slot, value=value, error=message)
        raise ValueError(message) from error
    repository.record_result(attempted_at, slot=slot, value=value)
    return {"value": value, "cells": list(settings.cells), "updated_at": attempted_at}


def run_due(now: datetime | None = None) -> dict:
    from app.integrations import google_week_sales, google_week_search

    current = _now(now)
    settings = repository.get_settings()
    if pending_run(settings, current) and not update_is_due(settings, current):
        return {"skipped": True}
    report = _run(manual=False, now=current)
    sales = google_week_sales.run_due(current)
    if not sales.get("skipped"):
        report = {**({} if report.get("skipped") else report), "sales": sales}
    search = google_week_search.run_due(current)
    if search.get("skipped"):
        return report
    return {**({} if report.get("skipped") else report), "search": search}


def run_now(now: datetime | None = None) -> dict:
    return _run(manual=True, now=now)
