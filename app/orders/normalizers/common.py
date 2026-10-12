"""Shared value conversions for marketplace order responses."""

from __future__ import annotations

import json
from datetime import date, datetime, time

from app.core.domain import MOSCOW_TIMEZONE as MOSCOW


def number(value, default: float = 0.0) -> float:
    if isinstance(value, dict):
        value = value.get("value", value.get("amount", default))
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def integer(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def timestamp(value, fallback: datetime | None = None) -> str:

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        raw = str(value or "").strip()
        parsed = None
        if raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                for pattern in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(raw, pattern)
                        break
                    except ValueError:
                        continue
        if parsed is None:
            parsed = fallback or datetime.now(MOSCOW)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=MOSCOW)
    return parsed.astimezone(MOSCOW).isoformat(timespec="seconds")


def compact_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
