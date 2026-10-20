from dataclasses import dataclass
from datetime import timedelta, timezone
from enum import StrEnum
from http import HTTPStatus as HTTPStatus
from typing import Final, Literal


class LocalService(StrEnum):
    WEB = "web"
    SCHEDULER = "scheduler"


@dataclass(frozen=True, slots=True, kw_only=True)
class DevelopmentConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    web_app: str = "app.main:app"
    worker_module: str = "app.jobs.worker"
    poll_seconds: float = 0.1
    watch_timeout_ms: int = 100
    reload_debounce_ms: int = 300
    shutdown_seconds: float = 60
    force_wait_seconds: float = 5
    start_token: bytes = b"1"


DEVELOPMENT: Final = DevelopmentConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class WindowsProcessConfig:
    extended_limit_information: int = 9
    kill_on_job_close: int = 0x2000
    process_set_quota_and_terminate: int = 0x0101


WINDOWS_PROCESS: Final = WindowsProcessConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class WbAnalyzerConstants:
    first_hour: int = 8
    hourly_d: tuple[float, ...] = (
        0.18,
        0.23,
        0.30,
        0.36,
        0.42,
        0.48,
        0.54,
        0.60,
        0.66,
        0.71,
        0.76,
        0.81,
        0.87,
        0.92,
        0.96,
        1.00,
    )
    fallback_d: float = 1.0
    max_d: float = 1_000_000


WB_ANALYZER: Final = WbAnalyzerConstants()


class SupplyStockAction(StrEnum):
    MARK = "supply_arrival_stock_mark"
    UNMARK = "supply_arrival_stock_unmark"


class Marketplace(StrEnum):
    WB = "WB"
    OZON = "OZON"
    YANDEX_MARKET = "YANDEX MARKET"


class OrderScheme(StrEnum):
    FBO = "fbo"
    FBS = "fbs"


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"


class JobTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class JobScope(StrEnum):
    GLOBAL = "global"
    STORES = "stores"
    STORE_MARKETPLACES = "store_marketplaces"


class DatabaseDialect(StrEnum):
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


@dataclass(frozen=True, slots=True, kw_only=True)
class SyncJobMetadata:
    name: str
    title: str
    description: str
    schedule: str | None
    scope: JobScope
    marketplaces: tuple[Marketplace, ...]
    manual_run: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class TimeConstants:
    seconds_per_minute: int = 60
    seconds_per_hour: int = 3_600
    seconds_per_day: int = 86_400
    milliseconds_per_second: int = 1000
    moscow_timezone: timezone = timezone(timedelta(hours=3), name="MSK")


TIME: Final = TimeConstants()


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonOrdersConfig:
    lookback_days: int = 21
    window_days: int = 30
    run_hour_msk: int = 3
    retry_delays_seconds: tuple[int, ...] = (60, 300, 900)
    schemes: tuple[OrderScheme, ...] = (OrderScheme.FBO, OrderScheme.FBS)
    max_error_length: int = 1500
    health_scope: str = "orders"
    coverage_source: str = "orders"
    money_quantum: str = "0.01"
    interval_seconds: int = 3600
    retry_days: int = 7
    lock_prefix: str = "ozon-orders:"
    pending_batch_size: int = 100


OZON_ORDERS: Final = OzonOrdersConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class HttpConfig:
    retry_backoff_multiplier: int = 2
    retry_jitter_max_seconds: float = 1.5
    max_keepalive_connections: int = 20


HTTP: Final = HttpConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class DatabaseConfig:
    postgres_driver: str = "postgresql"
    postgres_async_driver: str = "postgresql+asyncpg"
    sqlite_async_driver: str = "sqlite+aiosqlite"


DATABASE: Final = DatabaseConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class LockConfig:
    namespace: str = "checkstock-sync:"
    directory: str = ".sync-locks"
    file_suffix: str = ".lock"
    postgres_key_bytes: int = 8
    postgres_byte_order: Literal["little", "big"] = "big"
    file_length_bytes: int = 1
    initialization_name: str = "application_initialization"


LOCKS: Final = LockConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class SchedulerConfig:
    poll_seconds: int = 60
    error_retry_seconds: int = 60
    min_delay_seconds: int = 1
    heartbeat_interval_seconds: int = 10
    heartbeat_max_age_seconds: int = 60
    heartbeat_clock_skew_seconds: int = 5
    heartbeat_filename: str = "scheduler-heartbeat.json"
    job_run_retention_days: int = 30
    initialization_retry_seconds: int = 1
    max_error_length: int = 12_000
    wb_funnel_weekly_metrics_interval_seconds: int = 4 * TIME.seconds_per_hour


SCHEDULER: Final = SchedulerConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class JobCatalog:
    google_weeks: tuple[SyncJobMetadata, ...] = tuple(
        SyncJobMetadata(
            name=name,
            title=title,
            description="Обновляет неделю, недельные заказы, историю остатков и фиксирует формулы выбранной площадки.",
            schedule=None,
            scope=JobScope.GLOBAL,
            marketplaces=(marketplace,),
            manual_run=False,
        )
        for marketplace, name, title in (
            (Marketplace.WB, "google_week_update", "Автообновление недели WB"),
            (Marketplace.OZON, "google_week_update_ozon", "Автообновление недели Ozon"),
            (Marketplace.YANDEX_MARKET, "google_week_update_yandex", "Автообновление недели Яндекс Маркета"),
        )
    )


JOBS: Final = JobCatalog()


class WeekAction(StrEnum):
    NUMBER = "number"
    SALES = "sales"
    STOCK = "stock"
    SEARCH = "search"
    FREEZE = "freeze"


class WeekIssue(StrEnum):
    INVALID_SETTINGS = "invalid_settings"
    NOT_SAVED = "not_saved"
    BUSY = "busy"
    CREDENTIALS = "credentials"
    GOOGLE = "google"
    SOURCE = "source"
    SHEET_MISSING = "sheet_missing"
    WEEK_MISSING = "week_missing"
    HEADERS = "headers"
    AMBIGUOUS = "ambiguous"
    PRODUCT = "product"
    PROJECT = "project"
    DUPLICATE = "duplicate"
    MISSING_DATA = "missing_data"
    FUTURE = "future"
    TARGET = "target"
    MERGED = "merged"
    PROTECTED = "protected"
    ARRAY = "array"
    FORMULA = "formula"
    CHANGED = "changed"
    TOO_LARGE = "too_large"
    SELECTION = "selection"
    INTERRUPTED = "interrupted"
    INTERNAL = "internal"
    LEGACY = "legacy"


@dataclass(frozen=True, slots=True, kw_only=True)
class GoogleWeekConstants:
    poll_seconds: int = 60
    retry_seconds: int = 300
    startup_seconds: int = 10
    timeout_seconds: int = 30
    token_seconds: int = 3600
    max_payload_bytes: int = 1_800_000
    max_cells: int = 20
    max_sheet_name: int = 100
    max_url_length: int = 2048
    freeze_anchor_column: int = 8
    days_per_week: int = 7
    funnel_raw_version: int = 3
    funnel_complete_version: int = 4
    ozon_cancelled_status: str = "cancelled"
    api_url: str = "https://sheets.googleapis.com/v4/spreadsheets"
    token_url: str = "https://oauth2.googleapis.com/token"
    scope: str = "https://www.googleapis.com/auth/spreadsheets"
    grant_type: str = "urn:ietf:params:oauth:grant-type:jwt-bearer"
    default_cells: tuple[str, ...] = ("F4",)
    default_weekday: int = 0
    default_time: str = "00:05"
    default_sheet: str = "2. Факт продаж"
    default_stock_sheets: tuple[str, ...] = (
        "Сток на складах ВБ",
        "Сток на складах Ozon",
        "Сток на складах ЯМ",
    )
    default_freeze_sheets: tuple[str, ...] = (
        "5. Базовый потенциал",
        "3. Реальный Сток общий - ШТ",
        "3.1. Адаптированный Сток общий - ШТ",
        "6. Исходный прогноз продаж - ШТ",
        "6.1 Адаптированный прогноз продаж - ШТ",
        "6г. Прогноз продаж - Объем товара",
        "3б. Сток общий - ЗЦ",
        "7а. В пути из Китая - ЗЦ",
        "7б. В пути из Китая - Объем",
    )
    identity_headers: tuple[str, str] = ("ARTICLE", "Проект")
    freeze_headers: frozenset[str] = frozenset(("ARTICLE", "BARCODE"))
    matching_key: str = "article_project"
    backup_folder: str = "google-week-marketplaces"
    grid_fields: str = (
        "namedRanges(namedRangeId,range),sheets(properties(sheetId,title,sheetType,gridProperties),merges,"
        "protectedRanges(range,namedRangeId,warningOnly,requestingUserCanEdit,unprotectedRanges),"
        "data(startRow,startColumn,rowData(values(userEnteredValue,effectiveValue,formattedValue,"
        "pivotTable,dataSourceTable,dataSourceFormula))))"
    )


GOOGLE_WEEK: Final = GoogleWeekConstants()
