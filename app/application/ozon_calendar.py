from collections.abc import Callable
from datetime import datetime

from app.application.ports import OzonCalendarStore
from app.core.constants import TIME, OzonCalendarEventKind
from app.dto.ozon_calendar import (
    OzonCalendarCell,
    OzonCalendarConflict,
    OzonCalendarCorrection,
    OzonCalendarEvent,
    OzonCalendarEvidence,
    OzonCalendarKey,
    OzonCalendarListing,
    OzonCalendarMissing,
    OzonCalendarObservation,
    OzonCalendarPreview,
    OzonCalendarQuery,
    OzonCalendarRow,
    OzonCalendarSave,
)
from app.economics import ozon_calendar


class OzonCalendarService:
    def __init__(self, repository: OzonCalendarStore, clock: Callable[[], datetime]):
        self.repository = repository
        self.clock = clock

    async def listing(self, query: OzonCalendarQuery) -> OzonCalendarListing:
        sources = await self.repository.days(query.store, query.day)
        days = ozon_calendar.days(sources, query.store, query.day)
        search = query.q.casefold().strip()
        selected = tuple(
            sorted(
                (
                    row
                    for row in days
                    if not search
                    or search
                    in " ".join(
                        (row.product.name, row.product.article, row.product.barcode, *row.product.barcodes)
                    ).casefold()
                ),
                key=lambda row: (row.product.name.casefold(), row.product.article),
            )
        )
        rows = tuple(
            OzonCalendarRow(
                product=row.product,
                snapshot=row.journal is not None,
                values=ozon_calendar.values(row.calculation),
                result=ozon_calendar.result(row.calculation),
                corrected=tuple(item.field for item in row.journal.overrides) if row.journal else (),
            )
            for row in selected
        )
        known = tuple(row.calculation.profit for row in selected if row.calculation.profit is not None)
        missing = tuple(
            OzonCalendarMissing(
                article=row.product.article,
                parameters=row.result.daily_missing,
                unavailable=row.result.day_profit is None,
            )
            for row in rows
            if not row.result.daily_complete
        )
        offset = (query.page - 1) * query.page_size
        return OzonCalendarListing(
            rows=rows[offset : offset + query.page_size],
            total=len(rows),
            complete=sum(row.result.complete for row in rows),
            day_profit=float(sum(known)) if known else None,
            profit_complete=bool(rows) and not missing,
            profit_missing=missing,
            first_day=sources.first_day,
            day=query.day,
            preliminary=query.day == self.clock().astimezone(TIME.moscow_timezone).date(),
        )

    async def evidence(self, key: OzonCalendarKey, *, events: bool = False) -> OzonCalendarEvidence:
        sources = await self.repository.days(key.store, key.day, key.article)
        days = ozon_calendar.days(sources, key.store, key.day)
        if not days or days[0].journal is None:
            raise LookupError()
        return OzonCalendarEvidence(
            key=key,
            journal=days[0].journal,
            calculation=days[0].calculation,
            events=await self.repository.events(key) if events else (),
        )

    async def cell(self, key: OzonCalendarKey) -> OzonCalendarCell:
        evidence = await self.evidence(key, events=True)
        journal = evidence.journal
        return OzonCalendarCell(
            token=ozon_calendar.token(evidence),
            values=ozon_calendar.values(evidence.calculation),
            result=ozon_calendar.result(evidence.calculation),
            original=OzonCalendarObservation(
                values=ozon_calendar.source_values(journal.original),
                captured_at=journal.original.observed_at,
                snapshot=journal.original,
            ),
            source=OzonCalendarObservation(
                values=ozon_calendar.source_values(journal.source),
                captured_at=journal.source.observed_at,
                snapshot=journal.source,
            ),
            overrides={item.field: item.value for item in journal.overrides},
            events=evidence.events,
            version=journal.source.version,
        )

    async def preview(self, change: OzonCalendarCorrection) -> OzonCalendarPreview:
        key = OzonCalendarKey(store=change.store, day=change.day, article=change.article)
        evidence = await self.evidence(key)
        if ozon_calendar.token(evidence) != change.token:
            raise OzonCalendarConflict()
        journal = ozon_calendar.changed(evidence.journal, change)
        after = ozon_calendar.recalculate(evidence, journal)
        return OzonCalendarPreview(
            before=getattr(ozon_calendar.values(evidence.calculation), change.field),
            after=getattr(ozon_calendar.values(after), change.field),
            before_result=ozon_calendar.result(evidence.calculation),
            after_result=ozon_calendar.result(after),
            preview_token=ozon_calendar.preview_token(evidence, change),
        )

    async def correct(self, change: OzonCalendarCorrection, actor: str) -> OzonCalendarCell:
        key = OzonCalendarKey(store=change.store, day=change.day, article=change.article)
        evidence = await self.evidence(key)
        if (
            ozon_calendar.token(evidence) != change.token
            or ozon_calendar.preview_token(evidence, change) != change.preview_token
        ):
            raise OzonCalendarConflict()
        journal = ozon_calendar.changed(evidence.journal, change)
        after = ozon_calendar.recalculate(evidence, journal)
        await self.repository.save(
            OzonCalendarSave(
                key=key,
                expected=evidence.journal,
                journal=journal,
                event=OzonCalendarEvent(
                    at=self.clock(),
                    actor=actor,
                    kind=OzonCalendarEventKind.UNDO if change.undo else OzonCalendarEventKind.CORRECTION,
                    field=change.field,
                    before=getattr(ozon_calendar.values(evidence.calculation), change.field),
                    after=getattr(ozon_calendar.values(after), change.field),
                    reason=change.reason,
                ),
            )
        )
        return await self.cell(key)
