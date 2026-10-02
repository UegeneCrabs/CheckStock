"""Find matching week headers and walk left using read-only Google Sheets access."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime

from app.integrations import google_week_update as week
from app.repositories import google_week_update as repository
from app.repositories.google_week_update import WeekSearchState, WeekUpdateSettings

IDENTITY_COLUMNS = ("ARTICLE", "Проект")
MATCHING_KEY = "article_project"


def _normalized(value: object) -> str:
    return " ".join(str(value).split())


def _is_week(value: object) -> bool:
    match = re.fullmatch(r"W([0-9]{1,2}) ([0-9]{4})", _normalized(value))
    if not match:
        return False
    try:
        date.fromisocalendar(int(match[2]), int(match[1]), 1)
    except ValueError:
        return False
    return True


def _position(cell: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", cell)
    column = 0
    for letter in match[1]:
        column = column * 26 + ord(letter) - ord("A") + 1
    return int(match[2]) - 1, column - 1


def _address(row: int, column: int) -> str:
    letters = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row + 1}"


def identity_columns(row: list, row_index: int) -> dict:
    return {
        name: [
            {"cell": _address(row_index, col), "value": str(value)}
            for col, value in enumerate(row)
            if _normalized(value).casefold() == name.casefold()
        ]
        for name in IDENTITY_COLUMNS
    }


def find_headers(rows: list[list], source_cells: tuple[str, ...]) -> list[dict]:
    """Find week ranges and ARTICLE/project headers on each matching row, excluding the source."""
    sources = []
    wanted: dict[str, list[tuple[int, int]]] = {}
    columns_by_row: dict[int, dict[str, list[dict]]] = {}
    for cell in source_cells:
        r, c = _position(cell)
        value = str(rows[r][c]) if r < len(rows) and c < len(rows[r]) else ""
        source = {"cell": cell, "value": value, "matches": []}
        if not _is_week(value):
            source["issue"] = "Исходная ячейка не содержит неделю в формате W39 2026"
        else:
            wanted.setdefault(_normalized(value), [])
        sources.append(source)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if _normalized(value) in wanted:
                wanted[_normalized(value)].append((r, c))
    for source in sources:
        if source.get("issue"):
            continue
        origin = _position(source["cell"])
        for r, c in wanted[_normalized(source["value"])]:
            if (r, c) == origin:
                continue
            if r not in columns_by_row:
                columns_by_row[r] = identity_columns(rows[r], r)
            headers = []
            stop = None
            for col in range(c, -1, -1):
                value = str(rows[r][col])
                entry = {"cell": _address(r, col), "value": value}
                if (r, col) == origin or not _is_week(value):
                    stop = {**entry, "reason": "source" if (r, col) == origin else "format"}
                    break
                headers.append(entry)
            source["matches"].append(
                {
                    "cell": _address(r, c),
                    "range": f"{headers[-1]['cell']}:{headers[0]['cell']}",
                    "headers": headers,
                    "stop": stop,
                    "row": r + 1,
                    "columns": columns_by_row[r],
                }
            )
    return sources


def schedule_settings(settings: WeekUpdateSettings, state: WeekSearchState) -> WeekUpdateSettings:
    return replace(
        settings,
        enabled=state.enabled,
        last_attempt_at=state.last_attempt_at,
        last_success_at=state.last_success_at,
        last_success_slot=state.last_success_slot,
    )


def is_due(settings: WeekUpdateSettings, *, now: datetime) -> bool:
    return week.update_is_due(schedule_settings(settings, repository.get_search_state()), now)


def result_matches_settings(result: dict, settings: WeekUpdateSettings) -> bool:
    return (
        result.get("spreadsheet_url") == settings.spreadsheet_url
        and result.get("sheet_name") == settings.sheet_name
        and result.get("source_cells") == list(settings.cells)
        and result.get("matching_key") == MATCHING_KEY
    )


def read_sheet(settings: WeekUpdateSettings, service=None) -> tuple[dict, list[list]]:
    """Read displayed identifiers and headers, with sheet bounds and merges."""
    service = service or week._google_service(read_only=True)
    doc_id = week.spreadsheet_id(settings.spreadsheet_url)
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
    quoted_sheet = "'" + settings.sheet_name.replace("'", "''") + "'"
    response = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=doc_id, range=quoted_sheet, valueRenderOption="FORMATTED_VALUE")
        .execute(num_retries=2)
    )
    return sheet, response.get("values", [])


def describe_headers(settings: WeekUpdateSettings, sheet: dict, rows: list[list]) -> dict:
    sources = find_headers(rows, settings.cells)
    unique_cells = {
        cell["cell"] for source in sources for match in source["matches"] for cell in match["headers"]
    }
    return {
        "matching_key": MATCHING_KEY,
        "spreadsheet_url": settings.spreadsheet_url,
        "sheet_name": settings.sheet_name,
        "sheet_id": sheet["properties"]["sheetId"],
        "source_cells": list(settings.cells),
        "sources": sources,
        "total_cells": len(unique_cells),
        "total_matches": sum(len(source["matches"]) for source in sources),
    }


def read_headers(settings: WeekUpdateSettings) -> dict:
    """Never writes to Sheets. Only selected headers and boundary cells are retained."""
    return describe_headers(settings, *read_sheet(settings))


def _run(*, manual: bool, now: datetime | None = None) -> dict:
    """Caller holds google_week_update's shared run_tracked lock."""
    settings = repository.get_settings()
    current = week._now(now)
    if not manual and not is_due(settings, now=current):
        return {"skipped": True}
    if not settings.updated_at:
        raise ValueError("Сначала сохраните настройки обновления недели")
    settings = week.validate(settings)
    attempted_at = current.isoformat()
    slot = week.scheduled_at(settings, current).isoformat()
    repository.record_search_attempt(attempted_at)
    try:
        result = read_headers(settings)
    except Exception as error:
        status = getattr(getattr(error, "resp", None), "status", None)
        if status:
            message = f"Google Таблицы: HTTP {status}. Проверьте доступ к таблице и повторите поиск"
        elif isinstance(error, ValueError):
            message = str(error)
        else:
            message = f"Не удалось прочитать таблицу ({type(error).__name__}). Повторите поиск"
        repository.record_search_result(attempted_at, slot=slot, error=message)
        raise ValueError(message) from error
    repository.record_search_result(attempted_at, slot=slot, result=result)
    return result


def run_due(now: datetime | None = None) -> dict:
    return _run(manual=False, now=now)


def run_now(now: datetime | None = None) -> dict:
    return _run(manual=True, now=now)
