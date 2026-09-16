"""Price discounts are learned from observations, never from estimated prices."""

import math
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal


def positive(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def discount_percent(base, price):
    base, price = positive(base), positive(price)
    if base is None or price is None or price > base:
        return None
    return float((1 - Decimal(str(price)) / Decimal(str(base))) * 100)


def discounted_price(base, percent):
    base = positive(base)
    if base is None or percent is None or isinstance(percent, bool):
        return None
    try:
        percent = Decimal(str(percent))
        if not percent.is_finite() or not 0 <= percent < 100:
            return None
        return float(
            (Decimal(str(base)) * (1 - percent / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        )
    except (ValueError, ArithmeticError):
        return None


def paired_at(seller_at, buyer_at):
    """A seller quote must precede the storefront observation and be recent."""
    try:
        delta = datetime.fromisoformat(buyer_at) - datetime.fromisoformat(seller_at)
        return timedelta(0) <= delta <= timedelta(hours=2)
    except (TypeError, ValueError):
        return False
