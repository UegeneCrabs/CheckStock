"""Named contracts for saved WB SPP observations."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class SppHistoryKind(StrEnum):
    HOURLY = "spp-hourly"
    DAILY = "spp-daily"


@dataclass(frozen=True)
class HourlySppRow:
    article: str
    observed_at: str
    customer_price_with_spp: float
    spp_percent: float | None
    customer_price_with_wallet: float | None


@dataclass(frozen=True)
class DailySppRow:
    article: str
    day: str
    observed_at: str
    average_customer_price_with_spp: float
    average_spp_percent: float | None
    average_customer_price_with_wallet: float | None


@dataclass(frozen=True)
class SppHistoryQuery:
    kind: SppHistoryKind
    store: str
    date_from: date
    date_to: date
    article: str | None = None
