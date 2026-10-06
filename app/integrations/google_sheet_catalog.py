"""Daily cached Google sheet titles, with stable IDs for the freeze selection."""

from __future__ import annotations

from datetime import datetime

from app.integrations import google_week_update as week
from app.repositories import google_week_freeze as repository
from app.repositories import google_week_update as week_repository
from app.repositories.google_week_update import WeekUpdateSettings

JOB_NAME = "google_sheet_catalog"
DEFAULT_SHEET_NAMES = (
    "5. Базовый потенциал",
    "3. Реальный Сток общий - ШТ",
    "3.1. Адаптированный Сток общий - ШТ",
    "6. Исходный прогноз продаж - ШТ",
    "6.1 Адаптированный прогноз продаж - ШТ",
    "6г. Прогноз продаж - Объем товара",
    "3б. Сток общий - ЗЦ",
    "7а. В пути из Китая - ЗЦ",
    "7б. В пути из Китая - Объем",
)


def _due(catalog: dict, now: datetime) -> bool:
    updated = week._timestamp(catalog["updated_at"])
    if updated and updated.date() >= now.date():
        return False
    attempt = week._timestamp(catalog["last_attempt_at"])
    return attempt is None or now - attempt >= week.RETRY_DELAY


def is_due(now: datetime | None = None) -> bool:
    settings = week_repository.get_settings()
    if not settings.updated_at:
        return False
    return _due(repository.get_catalog(week.spreadsheet_id(settings.spreadsheet_url)), week._now(now))


def get_catalog(settings: WeekUpdateSettings, *, refresh: bool = False, now: datetime | None = None) -> dict:
    """Cached reads never contact Google. The caller holds the shared job lock for refresh."""
    doc_id = week.spreadsheet_id(settings.spreadsheet_url)
    current = week._now(now)
    if refresh:
        stamp = current.isoformat()
        repository.record_catalog_attempt(doc_id, stamp)
        try:
            response = (
                week._google_service(read_only=True)
                .spreadsheets()
                .get(
                    spreadsheetId=doc_id,
                    fields="sheets(properties(sheetId,title,sheetType,index))",
                )
                .execute(num_retries=2)
            )
            sheets = [
                {"sheet_id": item["properties"]["sheetId"], "title": item["properties"]["title"]}
                for item in response.get("sheets", [])
                if item["properties"].get("sheetType", "GRID") == "GRID"
            ]
            repository.record_catalog_result(doc_id, stamp, sheets=sheets)
            repository.initialize_selection(
                doc_id, tuple(item["sheet_id"] for item in sheets if item["title"] in DEFAULT_SHEET_NAMES)
            )
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            if status:
                message = f"Google Таблицы: HTTP {status}. Не удалось обновить список листов"
            elif isinstance(error, ValueError):
                message = str(error)
            else:
                message = f"Не удалось обновить список листов ({type(error).__name__})"
            repository.record_catalog_result(doc_id, stamp, error=message)
            raise ValueError(message) from error
    catalog = repository.get_catalog(doc_id)
    state = repository.get_state()
    same_document = state.spreadsheet_id == doc_id
    return {
        **catalog,
        "selected_sheet_ids": list(state.sheet_ids) if same_document else [],
        "selection_initialized": same_document and state.selection_initialized,
        "refresh_due": _due(catalog, current),
    }


def refresh_if_due(now: datetime | None = None) -> dict:
    with week.locks.hold(week.JOB_NAME):
        if not is_due(now):
            return {"skipped": True}
        return get_catalog(week_repository.get_settings(), refresh=True, now=now)
