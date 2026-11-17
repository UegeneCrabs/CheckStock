from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class SppDirection(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"
    UNCHANGED = "unchanged"
    NO_DATA = "no_data"


class SppDirectionFilter(StrEnum):
    ALL = "all"
    INCREASE = "increase"
    DECREASE = "decrease"


@dataclass(frozen=True, slots=True)
class SppFilters:
    store: str = ""
    brand: str = ""
    manager: str = ""
    subject: str = ""
    article: str = ""
    search: str = ""
    direction: SppDirectionFilter = SppDirectionFilter.ALL


class SppSortField(StrEnum):
    ARTICLE = "article"
    NAME = "name"
    TURNOVER = "turnover"
    CURRENT_SPP = "current_spp"
    PREVIOUS_SPP = "previous_spp"
    STATUS = "status"


class SppSortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class SppHistoryPeriod(StrEnum):
    DAYS_7 = "7d"
    DAYS_30 = "30d"
    MONTHS_6 = "6m"
    YEAR_1 = "1y"


@dataclass(frozen=True, slots=True)
class SppProduct:
    store_slug: str
    article: str
    name: str
    brand: str | None
    subject: str | None
    manager: str | None
    turnover: float | None
    current_spp: float | None
    previous_spp: float | None
    status: str
    direction: SppDirection


@dataclass(frozen=True, slots=True)
class SppSummary:
    problem: int
    healthy: int
    no_data: int
    comparable: int
    health_percent: int | None
    increase: int
    decrease: int
    unchanged: int


@dataclass(frozen=True, slots=True)
class SppPeriod:
    date_from: str
    date_to: str
    previous_from: str
    previous_to: str
    excluded_days: tuple[str, ...] = ()
    previous_excluded_days: tuple[str, ...] = ()
    hourly_today: bool = False


@dataclass(frozen=True, slots=True)
class SppOptions:
    stores: list[str]
    brands: list[str]
    managers: list[str]
    subjects: list[str]
    articles: list[str]


@dataclass(frozen=True, slots=True)
class SppReportResult:
    rows: tuple[SppProduct, ...]
    total: int
    page: int
    page_size: int
    summary: SppSummary
    period: SppPeriod
    options: SppOptions
    sort_by: SppSortField
    sort_order: SppSortOrder


@dataclass(frozen=True, slots=True)
class SppDailyPoint:
    date: str
    average_spp: float | None
    observations: int
    average_price: float | None = None
    turnover_rub: float | None = None
    open: bool = False


@dataclass(frozen=True, slots=True)
class SppHourlyPoint:
    hour: str
    observed_at: str | None
    spp_percent: float | None
    customer_price_with_spp: float | None
    orders_turnover_rub: float | None


@dataclass(frozen=True, slots=True)
class SppHourlyHistory:
    day: str
    hours: tuple[SppHourlyPoint, ...]
    average_spp: float | None
    average_price: float | None


@dataclass(frozen=True, slots=True)
class _DailyPrice:
    store_slug: str
    article: str
    nm_id: str
    day: date
    spp_percent: float | None


@dataclass(frozen=True, slots=True)
class _HourlySnapshot:
    store_slug: str
    article: str
    observed_at: str
    spp_percent: float | None
    customer_price_with_spp: float
    orders_turnover_rub: float | None


@dataclass(frozen=True, slots=True)
class SppCatalogItem:
    store_slug: str
    article: str
    name: str
    brand: str | None = None
    subject: str | None = None
    manager: str | None = None


@dataclass(frozen=True, slots=True)
class SppTurnover:
    store_slug: str
    article: str
    day: date
    net_orders_amount: float | None
