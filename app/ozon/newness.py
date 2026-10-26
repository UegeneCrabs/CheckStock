from datetime import date

from app.core.constants import OZON_NEWNESS
from app.dto.ozon_newness import OzonNewness


def classify(today: date, first_order_day: date | None, first_stock_day: date | None) -> OzonNewness:
    order_day = first_order_day if first_order_day is not None and first_order_day <= today else None
    stock_day = first_stock_day if first_stock_day is not None and first_stock_day <= today else None
    observed_since = min((day for day in (order_day, stock_day) if day is not None), default=None)
    days = (today - observed_since).days if observed_since is not None else None
    return OzonNewness(
        first_order_day=order_day,
        first_stock_day=stock_day,
        observed_since=observed_since,
        observed_days=days,
        is_new=days <= OZON_NEWNESS.max_days if days is not None else None,
    )
