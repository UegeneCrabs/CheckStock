import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from app.config import settings as app_settings
from app.core.constants import (
    GOOGLE_WEEK,
    JOBS,
    TIME,
    GoogleExportKind,
    Marketplace,
    ScheduleKind,
    WeekAction,
    WeekIssue,
)
from app.core.stores import PROJECT_STORE_ALIASES
from app.dto.google_week import (
    CellWrite,
    FreezePlan,
    FreezeSheetResult,
    GoogleSheet,
    GridCell,
    HeaderCell,
    ProductImage,
    ProductImageKey,
    SheetOption,
    WeekActionState,
    WeekCatalog,
    WeekError,
    WeekJobResult,
    WeekProfile,
    WeekReport,
    WeekSettings,
    WeekView,
    document_id,
)
from app.infrastructure import legacy_google_export
from app.infrastructure.async_resources import AsyncResources
from app.infrastructure.google_week_repository import GoogleWeekRepository
from app.integrations import google_week_plans as plans
from app.integrations.google_week_client import GoogleWeekClient
from app.jobs import async_locks
from app.jobs.locks import SyncJobBusyError

logger = logging.getLogger(__name__)


def log_incomplete(report: WeekReport, action: WeekAction | GoogleExportKind) -> None:
    problems = report.issues + report.missing_data + report.zero_filled
    samples = [item.model_dump(mode="json") for item in problems[: GOOGLE_WEEK.diagnostic_sample_size]]
    missing_days = sorted({day.isoformat() for item in problems for day in item.missing_days})
    logger.warning(
        "google_week_incomplete marketplace=%s action=%s issues=%s missing=%s zero_filled=%s missing_days=%s samples=%s",
        report.marketplace,
        action,
        len(report.issues),
        len(report.missing_data),
        len(report.zero_filled),
        missing_days,
        samples,
    )


def job_name(marketplace: Marketplace) -> str:
    return next(job.name for job in JOBS.google_weeks if marketplace in job.marketplaces)


def slot_at(settings: WeekSettings, now: datetime) -> datetime:
    now = now.astimezone(TIME.moscow_timezone)
    hour, minute = map(int, settings.run_time.split(":"))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days = 1 if settings.schedule_kind == ScheduleKind.DAILY else GOOGLE_WEEK.days_per_week
    if settings.schedule_kind == ScheduleKind.WEEKLY:
        slot -= timedelta(days=(slot.weekday() - settings.weekday) % GOOGLE_WEEK.days_per_week)
    return slot - timedelta(days=days) if slot > now else slot


def action_state(profile: WeekProfile, action: WeekAction | GoogleExportKind) -> WeekActionState:
    return profile.export_states[action] if isinstance(action, GoogleExportKind) else profile.states[action]


def action_enabled(profile: WeekProfile, action: WeekAction | GoogleExportKind) -> bool:
    if isinstance(action, GoogleExportKind):
        return profile.settings.schedule_managed and action in profile.settings.export_actions
    return action in profile.settings.enabled_actions


def scheduled_actions(profile: WeekProfile) -> tuple[WeekAction | GoogleExportKind, ...]:
    exports = tuple(GoogleExportKind)
    return (
        WeekAction.NUMBER,
        WeekAction.SALES,
        WeekAction.STOCK,
        WeekAction.SEARCH,
        *exports,
        WeekAction.FREEZE,
    )


def pending(profile: WeekProfile, action: WeekAction | GoogleExportKind, now: datetime) -> bool:
    state = action_state(profile, action)
    activation = state.activated_at or profile.saved_at
    if not action_enabled(profile, action) or not activation:
        return False
    slot = slot_at(profile.settings, now)
    return slot > activation and (state.last_success_slot is None or state.last_success_slot < slot)


def due(profile: WeekProfile, action: WeekAction | GoogleExportKind, now: datetime) -> bool:
    attempt = action_state(profile, action).last_attempt_at
    return pending(profile, action, now) and (
        attempt is None or now - attempt >= timedelta(seconds=GOOGLE_WEEK.retry_seconds)
    )


def next_run(profile: WeekProfile, action: WeekAction | GoogleExportKind, now: datetime) -> datetime | None:
    if not action_enabled(profile, action) or not profile.saved_at:
        return None
    if pending(profile, action, now):
        attempt = action_state(profile, action).last_attempt_at
        return max(now, attempt + timedelta(seconds=GOOGLE_WEEK.retry_seconds)) if attempt else now
    days = 1 if profile.settings.schedule_kind == ScheduleKind.DAILY else GOOGLE_WEEK.days_per_week
    return slot_at(profile.settings, now) + timedelta(days=days)


def freeze_ready(profile: WeekProfile, now: datetime) -> bool:
    if pending(profile, WeekAction.NUMBER, now):
        return False
    for action in (WeekAction.SALES, WeekAction.STOCK, *GoogleExportKind):
        if not action_enabled(profile, action):
            continue
        state = action_state(profile, action)
        if (
            state.last_error
            or not state.result
            or not state.result.complete
            or not state.last_success_slot
            or state.last_success_slot < slot_at(profile.settings, now)
        ):
            return False
    return True


def catalog_due(profile: WeekProfile, now: datetime) -> bool:
    catalog = profile.catalog
    if not profile.saved_at:
        return False
    if (
        catalog.updated_at
        and catalog.updated_at.astimezone(TIME.moscow_timezone).date()
        >= now.astimezone(TIME.moscow_timezone).date()
    ):
        return False
    return catalog.last_attempt_at is None or now - catalog.last_attempt_at >= timedelta(
        seconds=GOOGLE_WEEK.retry_seconds
    )


class GoogleWeeks:
    def __init__(self, resources: AsyncResources):
        self.resources = resources
        self.repository = GoogleWeekRepository(resources.database)
        self.google = GoogleWeekClient(resources.http)

    async def product_images(
        self, marketplace: Marketplace, items: tuple[ProductImageKey, ...]
    ) -> list[ProductImage]:
        if not items:
            return []
        products = await self.repository.products(marketplace)
        catalog = {(product.store_slug, product.article): product for product in products}
        aliases: dict[tuple[str, str], set[str]] = {}
        for product in products:
            for alias in (product.article, *product.aliases):
                aliases.setdefault((product.store_slug, alias.strip()), set()).add(product.article)
        result = []
        for item in items:
            store = PROJECT_STORE_ALIASES.get(plans.normalized(item.project).casefold(), "")
            matched = aliases.get((store, item.article.strip()), set())
            matched_product = catalog.get((store, next(iter(matched)))) if len(matched) == 1 else None
            result.append(
                ProductImage(
                    article=item.article,
                    project=item.project,
                    image_url=matched_product.image_url if matched_product else "",
                )
            )
        return result

    async def view(self, marketplace: Marketplace, *, preview: WeekReport | None = None) -> WeekView:
        profile = await self.repository.get(marketplace)
        now = datetime.now(TIME.moscow_timezone)
        return WeekView(
            profile=profile,
            value=plans.current_week(now),
            next_runs={action: next_run(profile, action, now) for action in WeekAction},
            export_next_runs={kind: next_run(profile, kind, now) for kind in GoogleExportKind},
            freeze_waiting=pending(profile, WeekAction.FREEZE, now) and not freeze_ready(profile, now),
            catalog_due=catalog_due(profile, now),
            background_enabled=app_settings.background_sync_enabled,
            busy=await async_locks.is_running(job_name(marketplace), self.resources.database),
            preview=preview,
        )

    async def save(
        self, marketplace: Marketplace, settings: WeekSettings, *, now: datetime | None = None
    ) -> None:
        now = now or datetime.now(TIME.moscow_timezone)
        document_id(settings.spreadsheet_url)
        target = settings.export_target
        for kind, title in (
            (GoogleExportKind.STOCKS, target.stock_sheet_name),
            (GoogleExportKind.FBS_ORDERS, target.orders_sheet_name),
            (GoogleExportKind.FBO_TRANSIT, target.fbo_sheet_name),
        ):
            if kind in settings.export_actions and not title:
                raise WeekError(WeekIssue.INVALID_SETTINGS)
        if target.stock_sheet_name and target.stock_sheet_name.casefold() in {
            settings.sheet_name.casefold(),
            (settings.sales_sheet_name or settings.sheet_name).casefold(),
            settings.stock_sheet_name.casefold(),
        }:
            raise WeekError(WeekIssue.TARGET)
        await self.repository.validate_export(marketplace, settings)
        profile = await self.repository.get(marketplace)
        before = profile.settings
        if settings.spreadsheet_url != before.spreadsheet_url:
            settings = settings.model_copy(
                update={
                    "freeze_sheet_ids": (),
                    "selection_initialized": False,
                    "enabled_actions": settings.enabled_actions - {WeekAction.FREEZE},
                }
            )
            profile.catalog = WeekCatalog()
        else:
            known = {sheet.sheet_id for sheet in profile.catalog.sheets} | set(before.freeze_sheet_ids)
            if not set(settings.freeze_sheet_ids) <= known:
                raise WeekError(WeekIssue.SELECTION)
        if WeekAction.FREEZE in settings.enabled_actions and not settings.freeze_sheet_ids:
            raise WeekError(WeekIssue.SELECTION)
        schedule_changed = (
            settings.schedule_kind,
            settings.weekday,
            settings.run_time,
            settings.schedule_managed,
        ) != (
            before.schedule_kind,
            before.weekday,
            before.run_time,
            before.schedule_managed,
        )
        source_changed = (settings.spreadsheet_url, settings.sheet_name, settings.cells) != (
            before.spreadsheet_url,
            before.sheet_name,
            before.cells,
        )
        for action in WeekAction:
            state = profile.states[action]
            target_changed = (
                source_changed
                or (
                    action in (WeekAction.SALES, WeekAction.SEARCH)
                    and (settings.sales_sheet_name or settings.sheet_name)
                    != (before.sales_sheet_name or before.sheet_name)
                )
                or (action == WeekAction.STOCK and settings.stock_sheet_name != before.stock_sheet_name)
                or (action == WeekAction.FREEZE and settings.freeze_sheet_ids != before.freeze_sheet_ids)
            )
            if target_changed:
                state.result = None
                state.last_error = None
                state.last_success_slot = None
                state.last_attempt_at = None
            if (
                not profile.saved_at
                or schedule_changed
                or target_changed
                or action in settings.enabled_actions - before.enabled_actions
            ):
                state.activated_at = now
            elif state.activated_at is None:
                state.activated_at = profile.saved_at
        for kind, state in profile.export_states.items():
            if (
                not profile.saved_at
                or schedule_changed
                or not before.schedule_managed
                or settings.spreadsheet_url != before.spreadsheet_url
                or kind in settings.export_actions - before.export_actions
                or settings.export_target != before.export_target
            ):
                state.activated_at = now
                state.last_attempt_at = None
                state.last_success_slot = None
            if settings.spreadsheet_url != before.spreadsheet_url:
                state.result = None
                state.last_error = None
        profile.settings, profile.saved_at = settings, now
        await self.repository.save(profile, settings_changed=True)

    async def refresh_catalog(self, profile: WeekProfile, now: datetime) -> None:
        if not profile.saved_at:
            raise WeekError(WeekIssue.NOT_SAVED)
        profile.catalog.last_attempt_at = now
        await self.repository.save(profile)
        try:
            document = await self.google.metadata(document_id(profile.settings.spreadsheet_url))
            profile.catalog.sheets = [
                SheetOption(sheet_id=sheet.properties.sheetId, title=sheet.properties.title)
                for sheet in document.sheets
                if sheet.properties.sheetType == "GRID"
            ]
            profile.catalog.updated_at, profile.catalog.last_error = now, None
            if not profile.settings.selection_initialized:
                profile.settings.freeze_sheet_ids = tuple(
                    sorted(
                        sheet.sheet_id
                        for sheet in profile.catalog.sheets
                        if sheet.title in GOOGLE_WEEK.default_freeze_sheets
                    )
                )
                profile.settings.selection_initialized = True
        except WeekError as error:
            profile.catalog.last_error = error.problem
            raise
        finally:
            await self.repository.save(profile)

    async def run(
        self,
        profile: WeekProfile,
        action: WeekAction | GoogleExportKind,
        now: datetime,
        *,
        preview: bool = False,
    ) -> WeekReport:
        if not profile.saved_at:
            raise WeekError(WeekIssue.NOT_SAVED)
        state = action_state(profile, action)
        if not preview:
            state.last_attempt_at = now
            state.last_error = None
            await self.repository.save(profile)
        try:
            async with async_locks.hold(
                "google-week-document:" + document_id(profile.settings.spreadsheet_url),
                self.resources.database,
            ):
                if isinstance(action, GoogleExportKind):
                    if profile.marketplace == Marketplace.OZON:
                        from app.container import ozon_google_export_service

                        async with async_locks.hold(JOBS.stock_sheet_export.name, self.resources.database):
                            report = await ozon_google_export_service(self.resources).run(
                                profile, action, now
                            )
                    else:
                        report = await legacy_google_export.run(
                            action, profile.settings.spreadsheet_url, now, profile.marketplace
                        )
                elif action == WeekAction.FREEZE:
                    report = await self._freeze(profile, now, preview=preview)
                else:
                    report = await self._export(profile, action, now)
        except (Exception, asyncio.CancelledError) as error:
            failure = (
                error
                if isinstance(error, WeekError)
                else WeekError(
                    WeekIssue.INTERRUPTED
                    if isinstance(error, asyncio.CancelledError)
                    else WeekIssue.BUSY
                    if isinstance(error, SyncJobBusyError)
                    else WeekIssue.INTERNAL,
                    detail=type(error).__name__,
                )
            )
            if not preview:
                state.last_error = failure.problem
                state.last_success_slot = None
                await self.repository.save(profile)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise failure from error
        if not preview:
            state.result, state.last_error = report, None
            state.last_success_slot = slot_at(profile.settings, now) if report.complete else None
            if report.complete:
                state.last_success_at = now
            await self.repository.save(profile)
        return report

    async def _sheet(self, doc_id: str, title: str) -> GoogleSheet:
        document = await self.google.read(doc_id, [title])
        sheet = next((sheet for sheet in document.sheets if sheet.properties.title == title), None)
        if sheet is None:
            raise WeekError(WeekIssue.SHEET_MISSING, detail=title)
        return sheet

    async def _export(self, profile: WeekProfile, action: WeekAction, now: datetime) -> WeekReport:
        settings = profile.settings
        doc_id = document_id(settings.spreadsheet_url)
        source = await self._sheet(doc_id, settings.sheet_name)
        if action == WeekAction.NUMBER:
            value = plans.current_week(now)
            writes = [CellWrite(cell=cell, value=value) for cell in settings.cells]
            plans.check_targets(profile, source, writes, number=True)
            await self.google.write(doc_id, settings.sheet_name, writes)
            return WeekReport(
                marketplace=profile.marketplace, value=value, processed_at=now, written_cells=len(writes)
            )
        origins = [
            HeaderCell(
                cell=cell, value=plans.display(plans.cells(source).get(plans.position(cell), GridCell()))
            )
            for cell in settings.cells
        ]
        stock = action == WeekAction.STOCK
        target_title = (
            settings.stock_sheet_name if stock else settings.sales_sheet_name or settings.sheet_name
        )
        sheet = await self._sheet(doc_id, target_title) if target_title != settings.sheet_name else source
        report = plans.headers(profile, sheet, origins, stock=stock)
        report.processed_at = now
        if action == WeekAction.SEARCH:
            report.complete = all(not item.issue and item.matches for item in report.sources)
            return report
        header = plans.layout(report)
        report.periods = plans.periods(header)
        if stock:
            for period in report.periods:
                period.snapshot_day = period.date_to
        products = plans.match_products(
            sheet, header, await self.repository.products(profile.marketplace), report
        )
        facts = await self.repository.facts(
            profile.marketplace,
            {product.store_slug for product in products},
            min(period.date_from for period in report.periods),
            max(period.date_to for period in report.periods),
            stock=stock,
        )
        plan = plans.export_plan(profile, report, products, facts, now=now, stock=stock)
        plans.check_targets(profile, sheet, plan.writes)
        report.checked_cells = len(plan.writes)
        grid = plans.cells(sheet)
        changed = [
            write
            for write in plan.writes
            if grid.get(plans.position(write.cell), GridCell()).userEnteredValue.numberValue != write.value
        ]
        report.unchanged_cells = len(plan.writes) - len(changed)
        report.products = plans.product_results(products, plan.writes, changed)
        if not changed:
            return report
        fresh = await self._sheet(doc_id, sheet.properties.title)
        fresh_grid = plans.cells(fresh)
        watched = {entry.cell for entry in plan.writes} | {entry.cell for entry in header.headers}
        if target_title == settings.sheet_name:
            watched.update(settings.cells)
        for source_result in report.sources:
            for match in source_result.matches:
                if match.stop:
                    watched.add(match.stop.cell)
        for col in range(sheet.properties.gridProperties.columnCount):
            watched.add(plans.address(header.row - 1, col))
        for row in range(
            header.row,
            max(max((r for r, _ in grid), default=0), max((r for r, _ in fresh_grid), default=0)) + 1,
        ):
            for name in GOOGLE_WEEK.identity_headers:
                watched.add(plans.address(row, plans.position(header.columns[name][0].cell)[1]))
        if (
            sheet.properties != fresh.properties
            or sheet.merges != fresh.merges
            or sheet.protectedRanges != fresh.protectedRanges
            or any(
                grid.get(plans.position(cell), GridCell()) != fresh_grid.get(plans.position(cell), GridCell())
                for cell in watched
            )
        ):
            raise WeekError(WeekIssue.CHANGED)
        if target_title != settings.sheet_name:
            new_source = await self._sheet(doc_id, settings.sheet_name)
            if (
                new_source.properties != source.properties
                or plans.source_values(profile, new_source) != origins
            ):
                raise WeekError(WeekIssue.CHANGED)
        plans.check_targets(profile, fresh, changed)
        report.backup = self._backup(
            profile,
            action,
            now,
            {
                "sheet_id": sheet.properties.sheetId,
                "sheet_name": sheet.properties.title,
                "cells": [
                    {
                        **write.model_dump(),
                        "previous": grid.get(
                            plans.position(write.cell), GridCell()
                        ).userEnteredValue.model_dump(exclude_none=True),
                    }
                    for write in changed
                ],
            },
        )
        await self.google.write(doc_id, sheet.properties.title, changed)
        report.written_cells = len(changed)
        report.zeroed_cells = sum(write.value == 0 for write in changed)
        return report

    async def _freeze(self, profile: WeekProfile, now: datetime, *, preview: bool) -> WeekReport:
        if not profile.settings.selection_initialized or not profile.settings.freeze_sheet_ids:
            raise WeekError(WeekIssue.SELECTION)
        doc_id = document_id(profile.settings.spreadsheet_url)
        metadata = await self.google.metadata(doc_id)
        sheets = {sheet.properties.sheetId: sheet for sheet in metadata.sheets}
        wanted = plans.current_week(now)
        prepared: list[FreezePlan] = []
        for sid in profile.settings.freeze_sheet_ids:
            if sid not in sheets:
                prepared.append(
                    FreezePlan(
                        report=FreezeSheetResult(sheet_id=sid, sheet_name="", issue=WeekIssue.SHEET_MISSING)
                    )
                )
                continue
            document = await self.google.read(doc_id, [sheets[sid].properties.title])
            sheet = next((item for item in document.sheets if item.properties.sheetId == sid), None)
            prepared.append(
                plans.freeze_plan(profile, sheet, document, wanted)
                if sheet is not None
                else FreezePlan(
                    report=FreezeSheetResult(sheet_id=sid, sheet_name="", issue=WeekIssue.SHEET_MISSING)
                )
            )
        report = WeekReport(
            marketplace=profile.marketplace,
            spreadsheet_url=profile.settings.spreadsheet_url,
            spreadsheet_id=doc_id,
            sheet_ids=list(profile.settings.freeze_sheet_ids),
            source_sheet_name=profile.settings.sheet_name,
            source_cells=list(profile.settings.cells),
            processed_at=now,
            week=wanted,
            dry_run=preview,
            sheets=[plan.report for plan in prepared],
        )
        if not preview and any(plan.ranges for plan in prepared):
            for plan in prepared:
                if not plan.ranges:
                    continue
                fresh = await self.google.read(doc_id, [plan.report.sheet_name])
                sheet = next(
                    (item for item in fresh.sheets if item.properties.sheetId == plan.report.sheet_id), None
                )
                checked = plans.freeze_plan(profile, sheet, fresh, wanted) if sheet else None
                if checked is None or checked.report.issue or checked.signature != plan.signature:
                    plan.report.issue = WeekIssue.CHANGED
                    plan.report.formula_count = 0
                    plan.ranges, plan.formulas = [], []
                else:
                    plan.formulas = checked.formulas
            ranges = [region for plan in prepared for region in plan.ranges]
            if ranges:
                report.backup = self._backup(
                    profile,
                    WeekAction.FREEZE,
                    now,
                    {
                        "week": wanted,
                        "sheets": [
                            {
                                "sheet_id": plan.report.sheet_id,
                                "sheet_name": plan.report.sheet_name,
                                "ranges": plan.report.ranges,
                                "cells": [cell.model_dump(exclude_none=True) for cell in plan.formulas],
                            }
                            for plan in prepared
                            if plan.ranges
                        ],
                    },
                )
                await self.google.freeze(
                    doc_id, [write for plan in prepared for write in plans.freeze_values(plan)]
                )
                for plan in prepared:
                    if plan.ranges:
                        plan.report.written_cells = plan.report.formula_count
        report.complete = all(not plan.report.issue for plan in prepared)
        report.total_formulas = sum(plan.report.formula_count for plan in prepared)
        report.written_cells = sum(plan.report.written_cells for plan in prepared)
        return report

    def _backup(self, profile: WeekProfile, action: WeekAction, now: datetime, payload: object) -> str:
        folder = (
            self.resources.database.path.parent
            / "backups"
            / GOOGLE_WEEK.backup_folder
            / profile.marketplace.name.lower()
            / action.value
        )
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex}.json"
        with path.open("x", encoding="utf-8") as file:
            json.dump(
                {
                    "marketplace": profile.marketplace.value,
                    "spreadsheet_url": profile.settings.spreadsheet_url,
                    "created_at": now.isoformat(),
                    "payload": payload,
                },
                file,
                ensure_ascii=False,
            )
            file.flush()
            os.fsync(file.fileno())
        return str(
            Path(GOOGLE_WEEK.backup_folder) / profile.marketplace.name.lower() / action.value / path.name
        )

    async def scheduled(self, marketplace: Marketplace, now: datetime | None = None) -> WeekJobResult:
        now = now or datetime.now(TIME.moscow_timezone)
        profile = await self.repository.get(marketplace)
        errors = []
        if catalog_due(profile, now):
            try:
                await self.refresh_catalog(profile, now)
            except WeekError as error:
                errors.append(error.problem.code.value)
        for action in scheduled_actions(profile):
            if action != WeekAction.NUMBER and pending(profile, WeekAction.NUMBER, now):
                break
            if action == WeekAction.FREEZE and not freeze_ready(profile, now):
                continue
            if due(profile, action, now):
                try:
                    report = await self.run(profile, action, now)
                    if not report.complete:
                        log_incomplete(report, action)
                        errors.append(f"{action.value}:{WeekIssue.MISSING_DATA.value}")
                except WeekError as error:
                    errors.append(f"{action.value}:{error.problem.code.value}")
        return WeekJobResult(error="; ".join(errors) or None)

    async def is_due(self, marketplace: Marketplace) -> bool:
        profile = await self.repository.get(marketplace)
        now = datetime.now(TIME.moscow_timezone)
        if catalog_due(profile, now):
            return True
        if pending(profile, WeekAction.NUMBER, now):
            return due(profile, WeekAction.NUMBER, now)
        return any(
            due(profile, action, now) and (action != WeekAction.FREEZE or freeze_ready(profile, now))
            for action in scheduled_actions(profile)
        )
