"""Normalization of optional numeric WB source values."""

import math


def number(value: object) -> float | None:
    try:
        value = float(str(value).replace(",", ".").replace(" ", ""))
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def norm(value: object) -> str:
    return " ".join(str(value or "").casefold().split())
