"""Published WB volume bands; reverse delivery excludes warehouse coefficient and IL."""

from app.economics.wb.standard_numbers import number

RETURN_BASE_RUB = 46
RETURN_EXTRA_LITER_RUB = 14


def volume_tariff(volume: float | None) -> float | None:
    volume = number(volume)
    if volume is None or volume <= 0:
        return None
    for limit, rate in ((0.2, 23), (0.4, 26), (0.6, 29), (0.8, 30), (1.0, 32)):
        if volume <= limit:
            return rate
    return RETURN_BASE_RUB + RETURN_EXTRA_LITER_RUB * (volume - 1)
