from datetime import date
from hashlib import sha256

from app.core.constants import OZON_MARGIN, OzonCalendarField
from app.dto.ozon_advertising import AdvertisingPeriod
from app.dto.ozon_calendar import (
    OzonCalendarCorrection,
    OzonCalendarDay,
    OzonCalendarEvidence,
    OzonCalendarJournal,
    OzonCalendarOverride,
    OzonCalendarResult,
    OzonCalendarSources,
    OzonCalendarValues,
)
from app.dto.ozon_margin import OzonMarginDay, OzonMarginInputs, OzonMarginSnapshot
from app.economics.ozon_margin import calculate_day
from app.ozon.advertising_normalizer import source_days, spend_summary
from app.ozon.advertising_read_index import AdvertisingReadIndex
from app.ozon.margin_normalizer import day_margin


def days(sources: OzonCalendarSources, store: str, day: date) -> tuple[OzonCalendarDay, ...]:
    period = AdvertisingPeriod(start=day, end=day)
    advertising = AdvertisingReadIndex(source_days(period, sources.advertising))
    return tuple(
        OzonCalendarDay(
            product=item.product,
            journal=item.journal,
            calculation=calculate_day(
                store,
                item.product.article,
                day,
                effective(item.journal) if item.journal else None,
                sources.turnover,
                spend_summary(
                    period,
                    advertising.article(item.product.article),
                    article=item.product.article,
                    credentials_present=sources.credentials_present,
                ).days[0],
            ),
        )
        for item in sources.products
    )


def effective(journal: OzonCalendarJournal) -> OzonMarginSnapshot:
    inputs = journal.source.inputs.model_dump()
    buyout = journal.source.buyout_percent
    for change in journal.overrides:
        if change.field == OzonCalendarField.BUYOUT:
            buyout = change.value
        else:
            inputs[change.field] = change.value
    return journal.source.model_copy(
        update={"inputs": OzonMarginInputs.model_validate(inputs), "buyout_percent": buyout}
    )


def values(day: OzonMarginDay) -> OzonCalendarValues:
    return OzonCalendarValues.model_validate(
        {
            **day.inputs.model_dump(),
            "buyout_percent": day.buyout_percent,
            "orders_count": day.orders,
            "advertising_spend": day.advertising_spend,
        }
    )


def result(day: OzonMarginDay) -> OzonCalendarResult:
    missing = tuple(dict.fromkeys((*day.missing, *day.issues)))
    return OzonCalendarResult(
        margin=float(day.margin) if day.margin is not None else None,
        roi=float(day.roi) if day.roi is not None else None,
        day_profit=float(day.profit) if day.profit is not None else None,
        complete=day.margin is not None and not missing,
        daily_complete=day.profit is not None and not missing,
        no_expected_buyouts=day.expected_buyouts == 0,
        missing=missing,
        daily_missing=missing,
    )


def source_values(snapshot: OzonMarginSnapshot) -> OzonCalendarValues:
    return OzonCalendarValues.model_validate(
        {**snapshot.inputs.model_dump(), "buyout_percent": snapshot.buyout_percent}
    )


def token(evidence: OzonCalendarEvidence) -> str:
    return sha256(evidence.model_copy(update={"events": ()}).model_dump_json().encode()).hexdigest()


def changed(journal: OzonCalendarJournal, change: OzonCalendarCorrection) -> OzonCalendarJournal:
    overrides = tuple(item for item in journal.overrides if item.field != change.field)
    if not change.undo:
        assert change.value is not None
        overrides += (OzonCalendarOverride(field=change.field, value=change.value),)
    return journal.model_copy(update={"overrides": overrides, "revision": journal.revision + 1})


def recalculate(evidence: OzonCalendarEvidence, journal: OzonCalendarJournal) -> OzonMarginDay:
    day = evidence.calculation
    return day_margin(
        day.day,
        effective(journal),
        day.orders,
        day.advertising_spend,
        issues=tuple(issue for issue in day.issues if issue not in evidence.journal.source.issues),
    )


def preview_token(evidence: OzonCalendarEvidence, change: OzonCalendarCorrection) -> str:
    return sha256(
        (
            token(evidence)
            + change.model_copy(update={"preview_token": ""}).model_dump_json()
            + str(OZON_MARGIN.version)
        ).encode()
    ).hexdigest()
