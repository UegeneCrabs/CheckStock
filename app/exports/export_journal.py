import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from itertools import zip_longest
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet._write_only import WriteOnlyWorksheet

from app.core.constants import TIME
from app.core.export_journal import clean_detail, daily_log_paths
from app.dto.integrations import JobRun

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_LOG_HEADER = re.compile(
    r"(?P<stamp>\d{4}-\d{2}-\d{2}T[\d:.+-]+) \| "
    r"(?P<level>[A-Z]+) \| (?P<module>[\w.]+) \| "
    r'(?:(?P<tags>\["(?:[^"\\]|\\.)*"(?:,\s*"(?:[^"\\]|\\.)*")*\]) \| )?'
    r"(?P<message>.*)"
)
_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_CELL_TEXT_LENGTH = 16_000
_CELL_LINE_FEEDS = 200
_MAX_SHEET_ROWS = 1_048_576
_DATETIME_FORMAT = "dd.mm.yyyy hh:mm:ss.000"
_HEADER_FILL = PatternFill("solid", fgColor="233854")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_BODY_ALIGNMENT = Alignment(vertical="top")
_DETAIL_ALIGNMENT = Alignment(vertical="top", wrap_text=True)


@dataclass(slots=True, kw_only=True)
class LogEntry:
    stamp: datetime | None = None
    level: str = ""
    module: str = ""
    tags: str = ""
    message: str = ""
    continuation: str = ""


def _moscow_time(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        return stamp
    return stamp.astimezone(TIME.moscow_timezone).replace(tzinfo=None)


def _entries(lines: Iterable[str]) -> Iterator[LogEntry]:
    entry = None
    continuation: list[str] = []
    for line in lines:
        match = _LOG_HEADER.fullmatch(line.removesuffix("\n"))
        try:
            stamp = _moscow_time(match["stamp"]) if match is not None else None
        except ValueError:
            stamp = None
        if match is None or stamp is None:
            continuation.append(line)
            continue
        if entry is not None or continuation:
            previous = entry or LogEntry()
            previous.continuation = "".join(continuation).removesuffix("\n")
            yield previous
        entry = LogEntry(
            stamp=stamp,
            level=match["level"],
            module=match["module"],
            tags=match["tags"] or "",
            message=match["message"],
        )
        continuation.clear()
    if entry is not None or continuation:
        previous = entry or LogEntry()
        previous.continuation = "".join(continuation).removesuffix("\n")
        yield previous


def _text_parts(value: str) -> Iterator[str]:
    value = _clean_text(value)
    if not value:
        yield ""
        return
    start = 0
    while start < len(value):
        end = min(start + _CELL_TEXT_LENGTH, len(value))
        if value.count("\n", start, end) > _CELL_LINE_FEEDS:
            lines = value[start:end].split("\n", _CELL_LINE_FEEDS)
            end = start + sum(len(line) + 1 for line in lines[:-1])
        yield value[start:end]
        start = end


def _clean_text(value: str) -> str:
    return _INVALID_XML.sub("\ufffd", clean_detail(value))


type CellValue = str | int | datetime | None


def _log_rows(directory: Path, day: date) -> Iterator[tuple[CellValue, ...]]:
    for path in daily_log_paths(directory, day):
        try:
            stream = path.open(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        with stream:
            for entry in _entries(stream):
                for part, (message, trace) in enumerate(
                    zip_longest(_text_parts(entry.message), _text_parts(entry.continuation)), start=1
                ):
                    yield entry.stamp, entry.level, entry.module, entry.tags, path.name, part, message, trace


def _run_rows(day: date, failures: Iterable[JobRun]) -> Iterator[tuple[CellValue, ...]]:
    for run in failures:
        stamp = _moscow_time(run.finished_at or run.started_at)
        if stamp.date() != day:
            continue
        details = "\n".join(
            " · ".join(filter(None, (item.target, item.code, item.message))) for item in run.error_details
        )
        for part, (error, trace) in enumerate(
            zip_longest(_text_parts(run.error or "—"), _text_parts(details)), start=1
        ):
            yield stamp, run.name, run.id, run.trigger, run.duration_ms, run.error_code, part, error, trace


def _add_sheet(book: Workbook, title: str, columns: tuple[tuple[str, int], ...]) -> WriteOnlyWorksheet:
    sheet = book.create_sheet(title)
    sheet.freeze_panes = "B2"
    cells = []
    for index, (header, width) in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
        cell = WriteOnlyCell(sheet, value=header)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cells.append(cell)
    sheet.append(cells)
    return sheet


def _write_rows(
    book: Workbook, title: str, columns: tuple[tuple[str, int], ...], rows: Iterable[tuple[CellValue, ...]]
) -> None:
    sheet = _add_sheet(book, title, columns)
    last_column = get_column_letter(len(columns))
    row_number = sheet_number = 1
    for row in rows:
        if row_number == _MAX_SHEET_ROWS:
            sheet.auto_filter.ref = f"A1:{last_column}{row_number}"
            sheet_number += 1
            sheet = _add_sheet(book, f"{title} {sheet_number}", columns)
            row_number = 1
        cells = []
        for index, value in enumerate(row):
            is_detail = index >= len(columns) - 2
            if isinstance(value, str) and not is_detail:
                value = _clean_text(value)
            cell = WriteOnlyCell(sheet, value=value)
            if isinstance(value, str):
                cell.data_type = "s"
            elif isinstance(value, datetime):
                cell.number_format = _DATETIME_FORMAT
            cell.alignment = _DETAIL_ALIGNMENT if is_detail else _BODY_ALIGNMENT
            cells.append(cell)
        sheet.append(cells)
        row_number += 1
    sheet.auto_filter.ref = f"A1:{last_column}{row_number}"


def build_xlsx(directory: Path, day: date, failures: tuple[JobRun, ...]) -> bytes:
    book = Workbook(write_only=True)
    _write_rows(
        book,
        "Записи журнала",
        (
            ("Время (МСК)", 26),
            ("Уровень", 13),
            ("Модуль", 35),
            ("Теги", 28),
            ("Файл журнала", 25),
            ("Часть", 8),
            ("Сообщение", 86),
            ("Трассировка и продолжение", 70),
        ),
        _log_rows(directory, day),
    )
    _write_rows(
        book,
        "Ошибки запусков",
        (
            ("Время (МСК)", 26),
            ("Задача", 36),
            ("ID запуска", 37),
            ("Запуск", 16),
            ("Длительность, мс", 20),
            ("Код ошибки", 22),
            ("Часть", 8),
            ("Ошибка", 86),
            ("Подробности", 70),
        ),
        _run_rows(day, failures),
    )
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()
