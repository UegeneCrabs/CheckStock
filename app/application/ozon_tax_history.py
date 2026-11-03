from collections.abc import Callable
from datetime import datetime

from app.application.ports import OzonTaxHistoryStore
from app.core.constants import (
    OZON_MARGIN,
    OZON_PRICE_CHANGES,
    OzonCalendarEventKind,
    OzonCalendarField,
    OzonPriceState,
)
from app.dto.ozon_cabinet import OzonCabinetSave, OzonCabinetUpdate
from app.dto.ozon_calculator import OzonCalculatorValues
from app.dto.ozon_calendar import OzonCalendarEvent, OzonCalendarJournal
from app.dto.ozon_tax_history import (
    OzonTaxHistoryChange,
    OzonTaxHistoryCommand,
    OzonTaxHistoryPlan,
    OzonTaxHistoryReport,
)
from app.economics.ozon_calculator import recalculate
from app.economics.ozon_calendar import effective, source_values


class OzonTaxHistoryService:
    def __init__(self, repository: OzonTaxHistoryStore, clock: Callable[[], datetime]):
        self.repository = repository
        self.clock = clock

    async def plan(self, command: OzonTaxHistoryCommand) -> OzonTaxHistoryPlan:
        data = await self.repository.load(command)
        now = self.clock()
        changes = []
        unresolved = 0
        already = sum(row.repaired for row in data.rows)
        for row in data.rows:
            snapshot = row.snapshot
            if snapshot.version != OZON_MARGIN.version:
                raise ValueError("Unsupported margin snapshot version")
            journal = (
                row.journal.model_copy(update={"source": snapshot})
                if row.journal
                else OzonCalendarJournal(original=snapshot, source=snapshot)
            )
            if row.repaired:
                unresolved += effective(journal).inputs.usn_amount is None
                continue
            candidates = {
                item.amount
                for item in row.prices
                if item.amount is not None
                and item.state == OzonPriceState.AVAILABLE
                and item.currency in (None, OZON_PRICE_CHANGES.currency)
            }
            buyer = (
                next(iter(candidates))
                if len(candidates) == 1
                and all(
                    item.amount is not None
                    and item.state == OzonPriceState.AVAILABLE
                    and item.currency in (None, OZON_PRICE_CHANGES.currency)
                    for item in row.prices
                )
                else None
            )
            taxes = recalculate(
                OzonCalculatorValues(
                    buyer_price=buyer,
                    vat_percent=command.vat_percent,
                    usn_percent=command.usn_percent,
                )
            )
            updated = snapshot.model_copy(
                update={
                    "inputs": snapshot.inputs.model_copy(
                        update={
                            "vat_amount": taxes.vat_amount,
                            "usn_amount": taxes.usn_amount,
                        }
                    )
                }
            )
            corrected = journal.model_copy(
                update={
                    "source": updated,
                    "revision": journal.revision + 1,
                    "overrides": tuple(
                        item
                        for item in journal.overrides
                        if item.field not in (OzonCalendarField.VAT, OzonCalendarField.USN)
                    ),
                }
            )
            unresolved += taxes.usn_amount is None
            changes.append(
                OzonTaxHistoryChange(
                    before=row,
                    snapshot=updated,
                    journal=corrected,
                    event=OzonCalendarEvent(
                        at=now,
                        actor=command.operation,
                        kind=OzonCalendarEventKind.CORRECTION,
                        before=source_values(effective(journal)),
                        after=source_values(effective(corrected)),
                        reason=command.reason,
                    ),
                )
            )
        cabinet_change = None
        expenses = data.cabinet.values.expenses
        if (
            data.rows
            and not already
            and (expenses.vat_percent != command.vat_percent or expenses.usn_percent != command.usn_percent)
        ):
            cabinet_change = OzonCabinetSave(
                store_slug=command.store,
                user_name=command.operation,
                saved_at=now,
                change=OzonCabinetUpdate(
                    revision=data.cabinet.revision,
                    expenses=expenses.model_copy(
                        update={
                            "vat_percent": float(command.vat_percent),
                            "usn_percent": float(command.usn_percent),
                        }
                    ),
                ),
            )
        days = {row.snapshot.day for row in data.rows}
        return OzonTaxHistoryPlan(
            command=command,
            cabinet=data.cabinet,
            cabinet_change=cabinet_change,
            changes=tuple(changes),
            report=OzonTaxHistoryReport(
                store=command.store,
                snapshots=len(data.rows),
                days=len(days),
                first_day=min(days) if days else None,
                last_day=max(days) if days else None,
                changes=len(changes),
                already_applied=already,
                unresolved_usn=unresolved,
                cabinet_changed=cabinet_change is not None,
            ),
        )

    async def apply(self, plan: OzonTaxHistoryPlan) -> OzonTaxHistoryReport:
        await self.repository.save(plan)
        return plan.report
