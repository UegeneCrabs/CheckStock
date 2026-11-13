import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from app.application.google_test_order_report import GoogleTestOrderReport, order_period
from app.application.ports import StockTestScheduleStore
from app.core.constants import (
    GOOGLE_STOCK_TEST,
    GOOGLE_WEEK,
    TIME,
    Marketplace,
    ScheduleKind,
    StockTestTarget,
    WeekIssue,
)
from app.dto.google_stock_report import StockTable, StockTestJobResult, StockTestScheduleState
from app.dto.google_week import GoogleSheet, WeekError
from app.integrations.google_stock_report import stock_report_requests
from app.integrations.google_week_client import GoogleWeekClient


def next_run(state: StockTestScheduleState, now: datetime) -> datetime | None:
    schedule = state.schedule
    if not schedule.enabled or state.activated_at is None:
        return None
    current = now.astimezone(TIME.moscow_timezone)
    hour, minute = map(int, schedule.run_time.split(":"))
    slot = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    period = timedelta(days=1)
    if schedule.schedule_kind == ScheduleKind.WEEKLY:
        period = timedelta(days=GOOGLE_WEEK.days_per_week)
        slot -= timedelta(days=(slot.weekday() - schedule.weekday) % GOOGLE_WEEK.days_per_week)
    if slot > current:
        slot -= period
    if (
        slot > state.activated_at
        and current - slot < timedelta(seconds=GOOGLE_WEEK.schedule_window_seconds)
        and (state.last_scheduled_at is None or state.last_scheduled_at < slot)
    ):
        return slot
    return slot + period


def payload_size(requests: list[dict[str, object]]) -> int:
    return len(json.dumps({"requests": requests}, ensure_ascii=False, separators=(",", ":")).encode())


def target_sheet(sheets: dict[int, GoogleSheet], target: StockTestTarget) -> GoogleSheet:
    sheet = sheets.get(target.sheet_id)
    if sheet is None or sheet.properties.title != target.sheet_name or sheet.properties.sheetType != "GRID":
        raise WeekError(WeekIssue.SHEET_MISSING, detail=target.sheet_name)
    return sheet


def problem_text(error: WeekError, *details: str) -> str:
    problem = error.problem
    return " · ".join(
        value
        for value in (
            problem.code.value,
            *details,
            problem.detail,
            f"HTTP {problem.status}" if problem.status else "",
        )
        if value
    )


class GoogleStockTestExport:
    def __init__(
        self,
        schedule: StockTestScheduleStore,
        read_table: Callable[[Marketplace, datetime], Awaitable[StockTable]],
        google: GoogleWeekClient,
        orders: GoogleTestOrderReport,
    ):
        self.schedules = schedule
        self.read_table = read_table
        self.google = google
        self.orders = orders

    async def run(self, now: datetime) -> StockTestJobResult:
        doc_id = GOOGLE_STOCK_TEST.spreadsheet_id
        try:
            document = await self.google.metadata(doc_id)
        except WeekError as error:
            return StockTestJobResult(error=problem_text(error))
        sheets = {sheet.properties.sheetId: sheet for sheet in document.sheets}
        path = f"{doc_id}:batchUpdate"
        errors: list[str] = []
        updated: list[str] = []
        try:
            batches: dict[str, list[dict[str, object]]] = {}
            for target in GOOGLE_STOCK_TEST.targets:
                sheet = target_sheet(sheets, target)
                table = await self.read_table(target.marketplace, now)
                if not table.rows:
                    raise WeekError(WeekIssue.SOURCE_MISSING, detail=target.sheet_name)
                batches[target.sheet_name] = stock_report_requests(sheet, table, now)
            requests = [request for batch in batches.values() for request in batch]
            if payload_size(requests) <= GOOGLE_WEEK.max_payload_bytes:
                await self.google.request("POST", path, body={"requests": requests})
            else:
                for name, batch in batches.items():
                    await self.google.request("POST", path, body={"requests": batch})
                    updated.append(name)
        except WeekError as error:
            errors.append(problem_text(error, ", ".join(updated)))

        period = order_period(now)

        async def prepare(target: StockTestTarget) -> list[dict[str, object]] | str:
            try:
                sheet = target_sheet(sheets, target)
                table = await self.orders.load(target.marketplace, period)
                if not table.rows:
                    raise WeekError(WeekIssue.SOURCE_MISSING)
                return stock_report_requests(sheet, table, now, order_period=period)
            except WeekError as error:
                return problem_text(error, target.sheet_name)

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(prepare(target)) for target in GOOGLE_STOCK_TEST.order_targets]
        for target, task in zip(GOOGLE_STOCK_TEST.order_targets, tasks, strict=True):
            result = task.result()
            if isinstance(result, str):
                errors.append(result)
                continue
            try:
                await self.google.request("POST", path, body={"requests": result})
            except WeekError as error:
                errors.append(problem_text(error, target.sheet_name))
        return StockTestJobResult(error="\n".join(errors) or None)
