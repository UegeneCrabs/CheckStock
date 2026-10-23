from dataclasses import dataclass
from datetime import timedelta, timezone
from enum import IntEnum, StrEnum
from http import HTTPMethod as HTTPMethod
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


class OrderSyncStage(StrEnum):
    CREDENTIALS = "credentials"
    LOAD = "load"
    SAVE = "save"


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


class YandexSyncSource(StrEnum):
    ORDERS = "orders"
    REPUTATION = "reputation"
    ADVERTISING = "advertising"
    BUYOUT = "buyout"


class DatabaseDialect(StrEnum):
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class OzonErrorCode(IntEnum):
    UNKNOWN = 2
    DEADLINE_EXCEEDED = 4
    RESOURCE_EXHAUSTED = 8
    INTERNAL = 13
    UNAVAILABLE = 14


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
class OzonApiConfig:
    base_url: str = "https://api-seller.ozon.ru"
    fbo_postings_path: str = "/v2/posting/fbo/list"
    fbo_postings_v3_path: str = "/v3/posting/fbo/list"
    fbo_postings_v3_page_size: int = 100
    fbs_postings_v3_path: str = "/v3/posting/fbs/list"
    fbs_postings_v4_path: str = "/v4/posting/fbs/list"
    page_size: int = 1000
    fbs_postings_v4_page_size: int = 50
    max_postings: int = 500_000
    validation_error_details_limit: int = 5
    legacy_response_error_length: int = 300
    error_body_length: int = 200
    tokens_cache_size: int = 8
    subscription_error_marker: str = "not available with existing subscription"
    subscription_error_message: str = "метод недоступен на текущей подписке Ozon (403)"


OZON_API: Final = OzonApiConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonStockAnalyticsConfig:
    path: str = "/v1/analytics/stocks"
    sku_batch_size: int = 100
    request_attempts: int = 8
    minimum_interval_seconds: float = 1.5
    retry_base_seconds: float = 5.0
    retry_max_seconds: float = 60.0


OZON_STOCK_ANALYTICS: Final = OzonStockAnalyticsConfig()


class OzonPriceField(StrEnum):
    BLACK = "black_price"
    GREEN = "green_price"
    BASE = "base_price"
    SELLER = "seller_price"


class OzonPriceState(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    CONFLICT = "conflict"
    ERROR = "error"


class OzonPriceSource(StrEnum):
    SELLER = "/v5/product/info/prices"
    PRODUCTS = "/v2/actions/products"
    CANDIDATES = "/v2/actions/candidates"


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonPricesConfig:
    actions_path: str = "/v1/actions"
    page_size: int = 1000
    action_page_size: int = 100
    max_pages: int = 20_000
    interval_seconds: int = 3600
    stale_seconds: int = 7200
    close_hour_msk: int = 0
    retry_delays_seconds: tuple[int, ...] = (60, 300, 900)
    lock_prefix: str = "ozon-prices:"
    seller_fields: tuple[OzonPriceField, ...] = (
        OzonPriceField.BASE,
        OzonPriceField.SELLER,
    )
    website_fields: tuple[OzonPriceField, ...] = (OzonPriceField.BLACK, OzonPriceField.GREEN)
    scheme: str = "FBO"
    visibility: str = "ALL"


OZON_PRICES: Final = OzonPricesConfig()


class OzonCommissionState(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    ERROR = "error"


class OzonCommissionInput(StrEnum):
    PERCENT = "percent"
    AMOUNT = "amount"


class OzonPriceCalculatorField(StrEnum):
    BASE = "base_price"
    SELLER = "seller_price"
    BLACK = "black_price"
    GREEN = "green_price"
    SPP = "spp_percent"
    WALLET = "wallet_percent"
    COMMISSION_PERCENT = "commission_percent"
    COMMISSION_AMOUNT = "commission_amount"


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonPriceCalculatorConfig:
    max_edits: int = 64
    number_max_digits: int = 26
    number_decimal_places: int = 8
    price_fields: tuple[OzonPriceCalculatorField, ...] = (
        OzonPriceCalculatorField.BASE,
        OzonPriceCalculatorField.SELLER,
        OzonPriceCalculatorField.BLACK,
        OzonPriceCalculatorField.GREEN,
    )


OZON_PRICE_CALCULATOR: Final = OzonPriceCalculatorConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonCommissionsConfig:
    min_percent: int = 0
    max_percent: int = 100
    amount_max_digits: int = 18
    amount_decimal_places: int = 6
    money_quantum: str = "0.01"
    percent_quantum: str = "0.00000001"


OZON_COMMISSIONS: Final = OzonCommissionsConfig()


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderNormalizationConfig:
    default_currency: str = "RUB"
    amount_decimal_places: int = 2
    delivered_statuses: frozenset[str] = frozenset(("delivered", "completed"))
    cancellation_status_marker: str = "cancel"
    date_formats: tuple[str, ...] = ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y")
    issue_date_format: str = "%d.%m"
    legacy_default_quantity: int = 1
    legacy_order_key_prefix: str = "ozon-row-"


ORDER_NORMALIZATION: Final = OrderNormalizationConfig()


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


class AdvertisingKind(StrEnum):
    CPC = "cpc"
    CPO_SELECTED = "cpo_selected"
    CPO_ALL = "cpo_all"
    CPO_COMBO = "cpo_combo"
    BANNER = "banner"
    VIDEO = "video"
    BRAND_SHELF = "brand_shelf"
    BLOGGER = "blogger"
    VK = "vk"
    OTHER = "other"


class AdvertisingSource(StrEnum):
    CPC = "cpc"
    CAMPAIGNS = "campaigns"
    SELECTED = "selected"
    ALL_PRODUCTS = "all_products"


class AdvertisingState(StrEnum):
    READY = "ready"
    MISSING = "missing"
    ERROR = "error"
    UNSUPPORTED = "unsupported"
    PARTIAL = "partial"
    NO_CREDENTIALS = "no_credentials"


class AdvertisingAllocation(StrEnum):
    DIRECT = "direct"
    EQUAL = "equal"
    UNALLOCATED = "unallocated"


class AdvertisingError(StrEnum):
    INVALID_AMOUNT = "invalid_advertising_amount"
    INVALID_COUNTER = "invalid_advertising_counter"
    INVALID_DATE = "invalid_advertising_date"
    INVALID_PERIOD = "invalid_advertising_period"
    NETWORK = "performance_network_error"
    HTTP = "performance_http_error"
    DAILY_LIMIT = "performance_daily_limit"
    ACTIVE_LIMIT = "performance_active_limit"
    RATE_LIMIT = "performance_rate_limit"
    ATTEMPTS_EXHAUSTED = "performance_attempts_exhausted"
    PAGINATION_REPEATED = "campaign_pagination_repeated"
    PAGINATION_LIMIT = "campaign_pagination_limit"
    REPORT_FAILED = "performance_report_failed"
    REPORT_TIMEOUT = "performance_report_timeout"
    CAMPAIGN_DATE_OUTSIDE_PERIOD = "campaign_date_outside_period"
    INVENTORY_INCOMPLETE = "campaign_inventory_incomplete"
    REPORT_INCOMPLETE = "campaign_report_incomplete"
    REPORT_MISSING_EXPENSES = "campaign_report_missing_expenses"
    UNKNOWN_CPO_SOURCE = "unknown_cpo_order_source"
    DATE_OUTSIDE_PERIOD = "advertising_date_outside_period"
    ALLOCATION_PRECISION = "advertising_allocation_precision"
    INVALID_RESPONSE = "invalid_performance_response"
    SYNC_FAILED = "advertising_sync_failed"
    STORES_FAILED = "advertising_stores_failed"


@dataclass(frozen=True, slots=True, kw_only=True)
class OzonAdvertisingConfig:
    base_url: str = "https://api-performance.ozon.ru"
    token_path: str = "/api/client/token"
    campaigns_path: str = "/api/client/campaign"
    daily_path: str = "/api/client/statistics/daily/json"
    products_path: str = "/api/client/statistics/products/sku"
    statistics_path: str = "/api/client/statistics/json"
    selected_path: str = "/api/client/statistic/orders/generate/json"
    all_products_path: str = "/api/client/statistics/all_sku_promo/orders/generate/json"
    status_path: str = "/api/client/statistics/{uuid}"
    download_path: str = "/api/client/statistics/report"
    grant_type: str = "client_credentials"
    date_group: str = "DATE"
    ready_report: str = "OK"
    failed_report: str = "ERROR"
    pending_reports: frozenset[str] = frozenset(("NOT_STARTED", "IN_PROGRESS"))
    cpc_payment: str = "CPC"
    sku_type: str = "SKU"
    selected_type: str = "SEARCH_PROMO"
    all_type: str = "ALL_SKU_PROMO"
    banner_type: str = "BANNER"
    video_type: str = "VIDEO_BANNER"
    brand_type: str = "BRAND_SHELF"
    blogger_type: str = "REF_BLOGGER"
    vk_type: str = "REF_VK"
    combo_source: str = "Кампания за клики"
    selected_source: str = "Оплата за заказ"
    date_format: str = "%d.%m.%Y"
    money_quantum: str = "0.01"
    period_days: int = 7
    percent_factor: int = 100
    page_size: int = 100
    max_pages: int = 1000
    campaigns_per_report: int = 10
    request_spacing_seconds: float = 1.2
    poll_seconds: float = 3
    max_poll_seconds: float = 30
    report_timeout_seconds: int = 900
    attempts: int = 4
    backoff_seconds: float = 3
    token_margin_seconds: int = 30
    run_hour_msk: int = 4
    run_minute_msk: int = 30
    retry_delays_seconds: tuple[int, ...] = (60, 300, 900)
    quota_pause_seconds: int = 86_400
    daily_limit_markers: tuple[str, ...] = ("дневной лимит", "суточный лимит", "daily limit")
    active_limit_markers: tuple[str, ...] = ("лимит активных запросов", "active requests limit")
    lock_prefix: str = "ozon-performance-breakdown:"
    interval_seconds: int = 3600
    intraday_sources: tuple[AdvertisingSource, ...] = (
        AdvertisingSource.CPC,
        AdvertisingSource.SELECTED,
        AdvertisingSource.ALL_PRODUCTS,
    )
    click_kinds: tuple[AdvertisingKind, ...] = (AdvertisingKind.CPC,)
    order_kinds: tuple[AdvertisingKind, ...] = (
        AdvertisingKind.CPO_SELECTED,
        AdvertisingKind.CPO_ALL,
        AdvertisingKind.CPO_COMBO,
    )
    required_spend_kinds: tuple[AdvertisingKind, ...] = click_kinds + order_kinds


OZON_ADVERTISING: Final = OzonAdvertisingConfig()


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
class ScheduleTexts:
    every_day: str = "Каждые сутки"
    every_hour: str = "Каждый час"
    every_minute: str = "Каждую минуту"
    days_template: str = "Каждые {days} сут."
    hours_template: str = "Каждые {hours} ч."
    minutes_template: str = "Каждые {minutes} мин."
    seconds_template: str = "Каждые {seconds} сек."
    daily_template: str = "Ежедневно в {hour:02d}:00"
    daily_msk_template: str = "Ежедневно в {hour:02d}:00 МСК"
    ftp_template: str = (
        "Ежедневно с {start_hour:02d}:{start_minute:02d} до {deadline_hour:02d}:00 МСК; "
        "повтор каждые {retry_minutes} мин. при ошибке"
    )
    required_error: str = "Не указано расписание задачи {name}."


SCHEDULE_TEXTS: Final = ScheduleTexts()


@dataclass(frozen=True, slots=True, kw_only=True)
class JobCatalog:
    wb_standard_costs: SyncJobMetadata = SyncJobMetadata(
        name="wb_standard_costs_sync",
        title="WB: сравнение ИУ и без ИУ",
        description="Загружает реализацию, хранение, тарифы, комиссии и карточки за последние 7 завершённых дней. При ошибке сохраняет предыдущий отчёт.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.WB,),
    )
    wb_hourly_prices: SyncJobMetadata = SyncJobMetadata(
        name="wb_hourly_prices_sync",
        title="Почасовые цены витрины WB",
        description="Сохраняет фактические цены с СПП из карточек WB по всем товарам магазинов.",
        schedule=None,
        scope=JobScope.STORES,
        marketplaces=(),
    )
    wb_daily_prices: SyncJobMetadata = SyncJobMetadata(
        name="wb_daily_prices_close_00_msk",
        title="Дневные цены витрины WB",
        description="Сворачивает завершённые дни почасовой истории цен и удаляет перенесённые часы.",
        schedule="Ежедневно в 00:05 МСК",
        scope=JobScope.GLOBAL,
        marketplaces=(),
    )
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
    wb_customer_transit: SyncJobMetadata = SyncJobMetadata(
        name="wb_customer_transit_sync",
        title="Возвраты WB в пути",
        description="Сохраняет остатки в пути от покупателей для эфемерид, отдельно от складских остатков.",
        schedule="Каждые 4 часа",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.WB,),
        manual_run=True,
    )
    yandex_finance: SyncJobMetadata = SyncJobMetadata(
        name="yandex_finance_sync",
        title="Финансовые отчёты Яндекс Маркета",
        description="Обновляет отдельный финансовый реестр за 90 завершённых дней. Требует настроенных финансовых подключений.",
        schedule="Ежедневно в 05:00 МСК",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    catalog: SyncJobMetadata = SyncJobMetadata(
        name="catalog_sync",
        title="Каталоги товаров",
        description="Обновляет карточки и привязки товаров WB, Ozon и Яндекс Маркета.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(
            Marketplace.WB,
            Marketplace.OZON,
            Marketplace.YANDEX_MARKET,
        ),
        manual_run=True,
    )
    stock: SyncJobMetadata = SyncJobMetadata(
        name="stock_sync",
        title="Остатки маркетплейсов",
        description="Загружает актуальные остатки по всем магазинам и маркетплейсам.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(
            Marketplace.WB,
            Marketplace.OZON,
            Marketplace.YANDEX_MARKET,
        ),
        manual_run=True,
    )
    inbound_supplies: SyncJobMetadata = SyncJobMetadata(
        name="inbound_supplies_sync",
        title="Поставки на склады маркетплейсов",
        description="Загружает поставки FBW, FBO и FBY, их товары, статусы и результаты приёмки по каждому магазину.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(
            Marketplace.WB,
            Marketplace.OZON,
            Marketplace.YANDEX_MARKET,
        ),
        manual_run=True,
    )
    supply_arrivals: SyncJobMetadata = SyncJobMetadata(
        name="supply_arrivals_sync",
        title="Табло поставок из Google Таблицы",
        description="Загружает реестр грузов, план прихода, группы, объём, вес и короба.",
        schedule=None,
        scope=JobScope.GLOBAL,
        marketplaces=(),
        manual_run=True,
    )
    wb_advertising: SyncJobMetadata = SyncJobMetadata(
        name="wb_advertising_sync",
        title="Реклама WB",
        description="Обновляет кампании, расходы, показы и клики Wildberries.",
        schedule=None,
        scope=JobScope.STORES,
        marketplaces=(),
        manual_run=True,
    )
    wb_funnel_orders: SyncJobMetadata = SyncJobMetadata(
        name="wb_funnel_orders_sync",
        title="Воронка продаж WB",
        description="Загружает заказы, сумму заказов и конверсии из воронки продаж.",
        schedule=None,
        scope=JobScope.STORES,
        marketplaces=(),
        manual_run=True,
    )
    yandex_orders: SyncJobMetadata = SyncJobMetadata(
        name="yandex_orders_sync",
        title="Заказы Яндекс Маркета",
        description="Обновляет заказы ЯМ за сегодня и 6 предыдущих дней, сохраняя историю для расчётов.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    yandex_economics: SyncJobMetadata = SyncJobMetadata(
        name="yandex_economics_sync",
        title="Тарифы и экономика Яндекс Маркета",
        description="Обновляет тарифы API, сохраняет параметры дня и рассчитывает историю прибыли YM.",
        schedule="Каждый час",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    yandex_reputation: SyncJobMetadata = SyncJobMetadata(
        name="yandex_reputation_sync",
        title="Отзывы и рейтинг Яндекс Маркета",
        description="Обновляет рейтинг товаров и количество отзывов ЯМ.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    ozon_reputation: SyncJobMetadata = SyncJobMetadata(
        name="ozon_reputation_sync",
        title="Отзывы и рейтинг Ozon",
        description="Загружает оценки и отзывы товаров Ozon при наличии доступа к API отзывов.",
        schedule="Каждые сутки",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.OZON,),
        manual_run=True,
    )
    ozon_orders: SyncJobMetadata = SyncJobMetadata(
        name="ozon_unit_economics_turnover_sync",
        title="ТО заказов Ozon",
        description="Каждый час загружает новые заказы и обновляет незавершённые отправления FBO/FBS; считает ТО и выкуп по дате заказа.",
        schedule="Каждый час; при ошибке до 3 повторов через 1, 5 и 15 мин.",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.OZON,),
        manual_run=True,
    )
    ozon_prices: SyncJobMetadata = SyncJobMetadata(
        name="ozon_prices_sync",
        title="Цены Ozon",
        description="Каждый час загружает четыре цены Ozon; комиссию за продажу FBO сохраняет один раз за день по каждому товару.",
        schedule="Цены — каждый час; комиссия FBO — раз в день",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.OZON,),
    )
    ozon_prices_close: SyncJobMetadata = SyncJobMetadata(
        name="ozon_prices_daily_close",
        title="Дневная история цен Ozon",
        description="Фиксирует последние полученные цены и комиссии FBO за прошедшие дни по московскому времени.",
        schedule="Ежедневно в 00:00 МСК",
        scope=JobScope.GLOBAL,
        marketplaces=(Marketplace.OZON,),
    )
    ozon_advertising: SyncJobMetadata = SyncJobMetadata(
        name="ozon_unit_economics_advertising_sync",
        title="Реклама Ozon",
        description="Загружает расходы, показы и клики по товарам за 7 завершённых дней из Performance API.",
        schedule="Ежедневно в 04:00 МСК",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.OZON,),
        manual_run=True,
    )
    ozon_advertising_breakdown: SyncJobMetadata = SyncJobMetadata(
        name="ozon_advertising_breakdown_sync",
        title="Реклама Ozon по дням",
        description="Каждый час обновляет сегодняшние CPC/CPO и догружает ошибки последних семи дней. Завершённые дни сохраняет однократно.",
        schedule="Каждый час; вчерашний день закрывается после 04:30 МСК",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.OZON,),
        manual_run=True,
    )
    yandex_advertising: SyncJobMetadata = SyncJobMetadata(
        name="yandex_advertising_sync",
        title="Реклама Яндекс Маркета",
        description="Обновляет затраты, показы и клики ЯМ по дням: сегодня и 6 предыдущих дней. История сохраняется.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    yandex_buyout: SyncJobMetadata = SyncJobMetadata(
        name="yandex_buyout_sync",
        title="Процент выкупа Яндекс Маркета",
        description="Доставки, отмены и возвраты из заказанных за период в настройках кабинета. Сегодня не включается.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    yandex_orders_previous_day_close: SyncJobMetadata = SyncJobMetadata(
        name="yandex_orders_previous_day_close_00_msk",
        title="Закрытие заказов Яндекс Маркета",
        description="Повторно загружает вчерашний день после его завершения.",
        schedule="Ежедневно в 00:00 МСК",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    yandex_product_novelty: SyncJobMetadata = SyncJobMetadata(
        name="yandex_product_novelty_sync",
        title="Новизна товаров Яндекс Маркета",
        description="Проверяет заказы за неделю перед последними 21 днями. Обычные товары повторно не проверяет.",
        schedule=None,
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    yandex_storefront_prices: SyncJobMetadata = SyncJobMetadata(
        name="yandex_storefront_prices_sync",
        title="Цены витрины Яндекс Маркета",
        description="Отдельный браузерный сборщик: цены покупателя без Пэй для актуального ассортимента в БД.",
        schedule="Ежедневно 08:00–19:00 каждый час и 01:00 · МСК",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    yandex_category_commissions: SyncJobMetadata = SyncJobMetadata(
        name="yandex_category_commissions_sync",
        title="Категории и комиссии Яндекс Маркета",
        description="Проверяет полную ветку категории каждого товара и комиссию размещения по магазинам FBY/FBS.",
        schedule="По понедельникам в 04:00 · МСК; пропущенная проверка — после запуска приложения",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(Marketplace.YANDEX_MARKET,),
        manual_run=True,
    )
    wb_funnel_previous_day_close: SyncJobMetadata = SyncJobMetadata(
        name="wb_funnel_previous_day_close_00_msk",
        title="Закрытие воронки WB",
        description="Повторно загружает вчерашний день после его завершения.",
        schedule="Ежедневно в 00:00 МСК",
        scope=JobScope.STORES,
        marketplaces=(),
        manual_run=True,
    )
    wb_funnel_weekly_metrics: SyncJobMetadata = SyncJobMetadata(
        name="wb_funnel_weekly_metrics_sync",
        title="Процент выкупа WB",
        description="Обновляет процент выкупа по товарам за заданное в кабинете число завершённых дней.",
        schedule=None,
        scope=JobScope.STORES,
        marketplaces=(),
        manual_run=True,
    )
    unit_economics_1c_prices: SyncJobMetadata = SyncJobMetadata(
        name="unit_economics_1c_sync",
        title="Цены для юнит-экономики 1С",
        description="Обновляет цены WB, используемые в расчётах юнит-экономики.",
        schedule=None,
        scope=JobScope.STORES,
        marketplaces=(),
        manual_run=True,
    )
    unit_economics_1c_wallet: SyncJobMetadata = SyncJobMetadata(
        name="unit_economics_1c_wallet_sync",
        title="Цена WB Кошелька",
        description="Обновляет публичную цену товара с WB Кошельком.",
        schedule=None,
        scope=JobScope.STORES,
        marketplaces=(),
        manual_run=True,
    )
    unit_economics_1c_daily_margin: SyncJobMetadata = SyncJobMetadata(
        name="unit_economics_1c_daily_margin_snapshot_00_msk",
        title="Дневная маржа WB",
        description="Сохраняет наблюдения текущего дня, включая неполные параметры; история не восстанавливается текущими настройками.",
        schedule="Каждые 30 минут",
        scope=JobScope.STORES,
        marketplaces=(),
        manual_run=True,
    )
    unit_economics_1c_source: SyncJobMetadata = SyncJobMetadata(
        name="unit_economics_1c_source_sync",
        title="Данные 1С",
        description="Загружает закупку, фулфилмент и прочие данные 1С из листов WB и YM.",
        schedule=None,
        scope=JobScope.GLOBAL,
        marketplaces=(),
        manual_run=True,
    )
    ozon_unit_economics_1c_source: SyncJobMetadata = SyncJobMetadata(
        name="ozon_unit_economics_1c_source_sync",
        title="Данные 1С Ozon",
        description="Загружает себестоимость, затраты на ФФ, тег и комиссию компании из листов OZON.",
        schedule=None,
        scope=JobScope.GLOBAL,
        marketplaces=(),
        manual_run=True,
    )
    unit_economics_1c_reference: SyncJobMetadata = SyncJobMetadata(
        name="unit_economics_1c_reference_sync",
        title="Справочники юнит-экономики",
        description="Обновляет категории, комиссии и справочные данные WB.",
        schedule="Каждые сутки",
        scope=JobScope.GLOBAL,
        marketplaces=(),
        manual_run=True,
    )
    marketplace_stock_history: SyncJobMetadata = SyncJobMetadata(
        name="marketplace_stock_sync_and_history_23_msk",
        title="История остатков маркетплейсов",
        description="Сохраняет дневной снимок остатков на маркетплейсах.",
        schedule="Ежедневно в 23:59 МСК",
        scope=JobScope.STORE_MARKETPLACES,
        marketplaces=(
            Marketplace.WB,
            Marketplace.OZON,
            Marketplace.YANDEX_MARKET,
        ),
        manual_run=True,
    )
    fulfillment_stock_history: SyncJobMetadata = SyncJobMetadata(
        name="fulfillment_stock_history_00_msk",
        title="История остатков ФФ",
        description="Сохраняет дневной снимок остатков на фулфилментах.",
        schedule="Ежедневно в 00:00 МСК",
        scope=JobScope.GLOBAL,
        marketplaces=(),
        manual_run=True,
    )
    stock_sheet_export: SyncJobMetadata = SyncJobMetadata(
        name="stock_sheet_export",
        title="Google Таблицы",
        description="Выгружает остатки и FBS-заказы всех семи проектов в отдельные файлы по площадкам.",
        schedule="Проверка каждую минуту; общее расписание на вкладке Google",
        scope=JobScope.GLOBAL,
        marketplaces=(),
        manual_run=True,
    )
    ftp_wb_export: SyncJobMetadata = SyncJobMetadata(
        name="ftp_wb_export",
        title="FTP — себестоимость WB",
        description="Собирает себестоимость из пяти WB-листов и отправляет файл data.json на FTP.",
        schedule=None,
        scope=JobScope.GLOBAL,
        marketplaces=(),
        manual_run=True,
    )
    ftp_ozon_export: SyncJobMetadata = SyncJobMetadata(
        name="ftp_ozon_export",
        title="FTP — себестоимость Ozon",
        description="Собирает себестоимость из пяти Ozon-листов и отправляет data_ozon.json на FTP.",
        schedule=None,
        scope=JobScope.GLOBAL,
        marketplaces=(),
        manual_run=True,
    )
    wb_token_check: SyncJobMetadata = SyncJobMetadata(
        name="wb_token_check",
        title="Срок действия ключей WB",
        description="Проверяет дату окончания API-ключей Wildberries.",
        schedule=None,
        scope=JobScope.STORES,
        marketplaces=(),
        manual_run=True,
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


class CredentialMarketplace(StrEnum):
    WB = "wb"
    OZON = "ozon"
    YANDEX = "yandex"


class WebErrorCode(StrEnum):
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    INVALID_CREDENTIAL = "invalid_credential"
    CREDENTIAL_STORAGE = "credential_storage"
    CLIENT_ID_REQUIRED = "client_id_required"
    INVALID_TARGET = "invalid_target"
    NO_TARGETS = "no_targets"
    JOB_BUSY = "job_busy"
    JOB_FAILED = "job_failed"
    INTERRUPTED = "interrupted"
    COOLDOWN = "cooldown"
    INVALID_PERIOD = "invalid_period"
    INVALID_EXPORT_SETTINGS = "invalid_export_settings"
    INVALID_ARTICLE = "invalid_article"
    INVALID_PRICE_CALCULATION = "invalid_price_calculation"


class UiAlertCode(StrEnum):
    SYNC_ERROR = "sync_error"
    ACCESS_DENIED = "access_denied"
    CREDENTIALS_MISSING = "credentials_missing"
    TOKEN_EXPIRING = "token_expiring"


class UiAlertScope(StrEnum):
    CREDENTIALS = "credentials"
    ADVERTISING = "advertising"


class StockScheme(StrEnum):
    FBS = "fbs"
    RFBS = "rfbs"
    FBO = "fbo"


class StockOperationFilter(StrEnum):
    DELIVERY = "delivery"
    TRANSFER = "transfer"
    FBS_TRANSFER = "fbs_transfer"
    SHIPMENT = "shipment"
    MANUAL_ADD = "manual_add"


@dataclass(frozen=True, slots=True, kw_only=True)
class StockReadConstants:
    wb_schemes: tuple[StockScheme, ...] = (StockScheme.FBS, StockScheme.FBO)
    ozon_schemes: tuple[StockScheme, ...] = (StockScheme.FBS, StockScheme.RFBS, StockScheme.FBO)
    yandex_schemes: tuple[StockScheme, ...] = (StockScheme.FBO, StockScheme.FBS)
    transfer_kinds: tuple[str, ...] = (
        "transfer",
        "transfer_dispatch",
        "transfer_receive",
        "transfer_receive_revert",
        "transfer_cancel",
    )
    shipment_kinds: tuple[str, ...] = (
        "shipment",
        "fbo_dispatch",
        "fbo_receive",
        "fbo_receive_revert",
        "fbo_cancel",
    )


STOCK_READ: Final = StockReadConstants()


class OzonDataIssueCode(StrEnum):
    STOCK_MISSING = "stock_missing"
    STOCK_PARTIAL = "stock_partial"
    SOURCE_ERROR = "source_error"
    REPUTATION_MISSING = "reputation_missing"
    SOURCE_MISSING = "source_missing"
    SOURCE_FIELD_MISSING = "source_field_missing"
    INBOUND_MISSING = "inbound_missing"
    TURNOVER_UNAVAILABLE = "turnover_unavailable"
    ORDERS_UNAVAILABLE = "orders_unavailable"
    ADVERTISING_UNAVAILABLE = "advertising_unavailable"


class InboundAvailability(StrEnum):
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    UNAVAILABLE = "unavailable"


class GoogleExportKind(StrEnum):
    STOCKS = "stocks"
    FBS_ORDERS = "fbs_orders"
    FBO_TRANSIT = "fbo_transit"


class ScheduleKind(StrEnum):
    INTERVAL = "interval"
    DAILY = "daily"
    WEEKLY = "weekly"
    WINDOW = "window"
    EXTERNAL = "external"


@dataclass(frozen=True, slots=True, kw_only=True)
class WebConstants:
    api_key_max_length: int = 16_384
    client_id_max_length: int = 1_024
    client_id_suffix_length: int = 4
    target_max_length: int = 100
    history_retention_days: int = 30
    history_default_limit: int = 50
    history_max_limit: int = 200
    timezone: str = "Europe/Moscow"
    interrupted_status: str = "interrupted"
    week_days: int = 7
    report_max_days: int = 366
    inbound_stale_multiplier: int = 2
    inbound_stale_min_seconds: int = 3_600


WEB: Final = WebConstants()


@dataclass(frozen=True, slots=True, kw_only=True)
class JobScheduleRule:
    name: str
    kind: ScheduleKind = ScheduleKind.INTERVAL
    seconds: int | None = None
    interval_setting: str | None = None
    hours: tuple[int, ...] = ()
    hour_setting: str | None = None
    minute: int = 0
    weekdays: tuple[int, ...] = ()
    yandex_source: YandexSyncSource | None = None


JOB_SCHEDULE_RULES: Final = (
    JobScheduleRule(name=JOBS.wb_standard_costs.name),
    *(JobScheduleRule(name=job.name, kind=ScheduleKind.EXTERNAL) for job in JOBS.google_weeks),
    JobScheduleRule(name=JOBS.ozon_advertising.name, kind=ScheduleKind.DAILY, hours=(4,)),
    JobScheduleRule(name=JOBS.wb_hourly_prices.name, interval_setting="wb_hourly_prices_interval_seconds"),
    JobScheduleRule(name=JOBS.wb_daily_prices.name, kind=ScheduleKind.DAILY, hours=(0,), minute=5),
    JobScheduleRule(name=JOBS.wb_customer_transit.name, seconds=14_400),
    JobScheduleRule(name=JOBS.yandex_finance.name, kind=ScheduleKind.DAILY, hours=(5,)),
    JobScheduleRule(name=JOBS.catalog.name, kind=ScheduleKind.DAILY, hour_setting="catalog_sync_hour"),
    JobScheduleRule(name=JOBS.stock.name, interval_setting="auto_sync_interval_seconds"),
    JobScheduleRule(name=JOBS.inbound_supplies.name, interval_setting="inbound_sync_interval_seconds"),
    JobScheduleRule(name=JOBS.supply_arrivals.name, interval_setting="supply_arrivals_sync_interval_seconds"),
    JobScheduleRule(name=JOBS.wb_advertising.name, interval_setting="wb_advertising_sync_interval_seconds"),
    JobScheduleRule(
        name=JOBS.wb_funnel_orders.name, interval_setting="wb_funnel_orders_sync_interval_seconds"
    ),
    JobScheduleRule(name=JOBS.yandex_orders.name, yandex_source=YandexSyncSource.ORDERS),
    JobScheduleRule(name=JOBS.yandex_economics.name, seconds=3_600),
    JobScheduleRule(name=JOBS.yandex_reputation.name, yandex_source=YandexSyncSource.REPUTATION),
    JobScheduleRule(name=JOBS.ozon_reputation.name, seconds=86_400),
    JobScheduleRule(name=JOBS.ozon_orders.name, seconds=OZON_ORDERS.interval_seconds),
    JobScheduleRule(name=JOBS.ozon_prices.name, seconds=OZON_PRICES.interval_seconds),
    JobScheduleRule(
        name=JOBS.ozon_prices_close.name, kind=ScheduleKind.DAILY, hours=(OZON_PRICES.close_hour_msk,)
    ),
    JobScheduleRule(
        name=JOBS.ozon_advertising_breakdown.name,
        seconds=OZON_ADVERTISING.interval_seconds,
    ),
    JobScheduleRule(name=JOBS.yandex_advertising.name, yandex_source=YandexSyncSource.ADVERTISING),
    JobScheduleRule(name=JOBS.yandex_buyout.name, yandex_source=YandexSyncSource.BUYOUT),
    JobScheduleRule(name=JOBS.yandex_orders_previous_day_close.name, kind=ScheduleKind.DAILY, hours=(0,)),
    JobScheduleRule(name=JOBS.yandex_product_novelty.name, seconds=86_400),
    JobScheduleRule(
        name=JOBS.yandex_storefront_prices.name, kind=ScheduleKind.DAILY, hours=(1, *range(8, 20))
    ),
    JobScheduleRule(
        name=JOBS.yandex_category_commissions.name, kind=ScheduleKind.WEEKLY, hours=(4,), weekdays=(0,)
    ),
    JobScheduleRule(name=JOBS.wb_funnel_previous_day_close.name, kind=ScheduleKind.DAILY, hours=(0,)),
    JobScheduleRule(
        name=JOBS.wb_funnel_weekly_metrics.name, seconds=SCHEDULER.wb_funnel_weekly_metrics_interval_seconds
    ),
    JobScheduleRule(
        name=JOBS.unit_economics_1c_prices.name,
        interval_setting="unit_economics_1c_price_sync_interval_seconds",
    ),
    JobScheduleRule(
        name=JOBS.unit_economics_1c_wallet.name,
        interval_setting="unit_economics_1c_wallet_sync_interval_seconds",
    ),
    JobScheduleRule(name=JOBS.unit_economics_1c_daily_margin.name, seconds=1_800),
    JobScheduleRule(
        name=JOBS.unit_economics_1c_source.name,
        kind=ScheduleKind.DAILY,
        hour_setting="unit_economics_1c_source_sync_hour",
    ),
    JobScheduleRule(
        name=JOBS.ozon_unit_economics_1c_source.name,
        kind=ScheduleKind.DAILY,
        hour_setting="unit_economics_1c_source_sync_hour",
    ),
    JobScheduleRule(name=JOBS.unit_economics_1c_reference.name, seconds=86_400),
    JobScheduleRule(
        name=JOBS.marketplace_stock_history.name, kind=ScheduleKind.DAILY, hours=(23,), minute=59
    ),
    JobScheduleRule(name=JOBS.fulfillment_stock_history.name, kind=ScheduleKind.DAILY, hours=(0,)),
    JobScheduleRule(name=JOBS.stock_sheet_export.name, kind=ScheduleKind.EXTERNAL, seconds=60),
    JobScheduleRule(name=JOBS.ftp_wb_export.name, kind=ScheduleKind.WINDOW),
    JobScheduleRule(name=JOBS.ftp_ozon_export.name, kind=ScheduleKind.WINDOW),
    JobScheduleRule(name=JOBS.wb_token_check.name, interval_setting="token_check_interval_seconds"),
)
