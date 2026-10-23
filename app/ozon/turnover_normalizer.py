from datetime import date, timedelta
from decimal import Decimal

from app.core.constants import OZON_ORDERS
from app.dto.ozon_economics import OzonCoverage, OzonTurnoverData, OzonTurnoverValue


def period_turnover(
    data: OzonTurnoverData, store: str, article: str, start: date, end: date
) -> OzonTurnoverValue:
    expected = {start + timedelta(days=index) for index in range((end - start).days + 1)}
    rows = tuple(row for row in data.products.get((store, article), ()) if row.day in expected)
    confirmed = data.confirmed_days.get(store, frozenset()) & expected
    available = {row.day for row in rows} | confirmed
    partial = available - confirmed
    missing = expected - available
    stale = store in data.stale_stores
    return OzonTurnoverValue(
        amount=sum((row.amount for row in rows), Decimal(0)).quantize(Decimal(OZON_ORDERS.money_quantum))
        if available
        else None,
        coverage=OzonCoverage(
            dates=tuple(day.isoformat() for day in sorted(available)),
            days=len(available),
            expected_days=len(expected),
            complete=not missing and not partial and not stale,
            missing_dates=tuple(day.isoformat() for day in sorted(missing)),
            partial_dates=tuple(day.isoformat() for day in sorted(partial)),
            stale=stale,
        ),
    )
