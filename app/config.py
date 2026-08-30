import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, model_validator

MIN_PBKDF2_ITERATIONS = 100_000
MAX_PBKDF2_ITERATIONS = 5_000_000
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_local_env(path: Path) -> None:
    """Load local defaults without overriding variables provided by the process."""
    if not path.is_file():
        return
    for name, value in dotenv_values(path).items():
        if value is not None:
            os.environ.setdefault(name, value)


_load_local_env(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_int(name: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return parsed


def _env_csv(name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, "").split(",") if value.strip())


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    base_dir: Path
    database_path: Path
    database_url: str | None = None
    database_timeout_seconds: int = Field(ge=1)
    database_busy_timeout_ms: int = Field(ge=1)
    templates_dir: Path
    static_dir: Path
    admin_seed_path: Path
    wb_tokens_path: Path
    ozon_tokens_path: Path
    yandex_tokens_path: Path
    google_service_account_path: Path
    log_level: str
    slow_request_threshold_ms: int = Field(ge=1)
    background_sync_enabled: bool
    funnel_orders_sync_enabled: bool
    unit_economics_1c_price_sync_enabled: bool
    unit_economics_1c_source_sync_hour: int = Field(ge=0, le=23)
    token_check_interval_seconds: int = Field(ge=1)
    unit_economics_1c_price_sync_startup_delay_seconds: int = Field(ge=0)
    wb_advertising_sync_startup_delay_seconds: int = Field(ge=0)
    decision_sync_startup_delay_seconds: int = Field(ge=0)
    auto_sync_interval_seconds: int = Field(ge=1)
    catalog_sync_hour: int = Field(ge=0, le=23)
    wb_advertising_sync_interval_seconds: int = Field(ge=1)
    wb_funnel_orders_sync_interval_seconds: int = Field(ge=1)
    unit_economics_1c_price_sync_interval_seconds: int = Field(ge=1)
    unit_economics_1c_wallet_sync_interval_seconds: int = Field(ge=1)
    wb_storefront_dest: str = Field(min_length=1)
    wb_storefront_batch_size: int = Field(ge=1, le=1_000)
    decision_sync_check_interval_seconds: int = Field(ge=1)
    session_ttl_days: int = Field(ge=1)
    session_cookie_secure: bool
    pbkdf2_iterations: int = Field(ge=MIN_PBKDF2_ITERATIONS, le=MAX_PBKDF2_ITERATIONS)
    stock_window_days: int = Field(ge=1)
    stock_frozen_days: int = Field(ge=1)
    stock_excess_days: int = Field(ge=1)
    stock_cache_ttl_seconds: int = Field(ge=1)
    stock_detail_page_size: int = Field(ge=1)
    warehouse_display_limit: int = Field(ge=1)
    operation_history_limit: int = Field(ge=1)
    ff_import_timeout_seconds: int = Field(ge=1)
    wb_request_timeout_seconds: int = Field(ge=1)
    ozon_request_timeout_seconds: int = Field(ge=1)
    yandex_request_timeout_seconds: int = Field(ge=1)
    wb_request_attempts: int = Field(ge=1, le=20)
    ozon_request_attempts: int = Field(ge=1, le=20)
    yandex_request_attempts: int = Field(ge=1, le=20)
    wb_retry_backoff_seconds: int = Field(ge=0)
    ozon_retry_backoff_seconds: int = Field(ge=0)
    yandex_retry_backoff_seconds: int = Field(ge=0)
    wb_sales_max_pages: int = Field(ge=1)
    rnp_sync_cooldown_minutes: int = Field(ge=0)
    rnp_report_download_timeout_seconds: int = Field(ge=1)
    rnp_report_poll_attempts: int = Field(ge=1)
    rnp_report_poll_interval_seconds: int = Field(ge=0)
    experimental_owner_logins: tuple[str, ...] = ()
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_sender: str = ""
    smtp_starttls: bool = True

    @model_validator(mode="after")
    def validate_stock_thresholds(self) -> "Settings":
        if self.stock_frozen_days > self.stock_excess_days:
            raise ValueError("stock_frozen_days must not exceed stock_excess_days")
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        base_dir = BASE_DIR
        database_path = Path(os.getenv("CHECKSTOCK_DB_PATH", base_dir / "data" / "checkstock.db"))
        database_url = os.getenv("CHECKSTOCK_DATABASE_URL", "").strip() or None
        if database_url and not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("CHECKSTOCK_DATABASE_URL must be a PostgreSQL URL")
        log_level = os.getenv("CHECKSTOCK_LOG_LEVEL", "INFO").upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("CHECKSTOCK_LOG_LEVEL must be a standard logging level")
        return cls(
            base_dir=base_dir,
            database_path=database_path,
            database_url=database_url,
            database_timeout_seconds=_env_int("CHECKSTOCK_DB_TIMEOUT_SECONDS", 30, minimum=1),
            database_busy_timeout_ms=_env_int("CHECKSTOCK_DB_BUSY_TIMEOUT_MS", 30_000, minimum=1),
            templates_dir=Path(os.getenv("CHECKSTOCK_TEMPLATES_DIR", base_dir / "templates")),
            static_dir=Path(os.getenv("CHECKSTOCK_STATIC_DIR", base_dir / "static")),
            admin_seed_path=Path(
                os.getenv("CHECKSTOCK_ADMIN_SEED_PATH", base_dir / "secrets" / "admin_seed.json")
            ),
            wb_tokens_path=Path(
                os.getenv("CHECKSTOCK_WB_TOKENS_PATH", base_dir / "secrets" / "wb_tokens.json")
            ),
            ozon_tokens_path=Path(
                os.getenv("CHECKSTOCK_OZON_TOKENS_PATH", base_dir / "secrets" / "ozon_tokens.json")
            ),
            yandex_tokens_path=Path(
                os.getenv("CHECKSTOCK_YANDEX_TOKENS_PATH", base_dir / "secrets" / "yandex_tokens.json")
            ),
            google_service_account_path=Path(
                os.getenv(
                    "CHECKSTOCK_GOOGLE_SERVICE_ACCOUNT_PATH",
                    base_dir / "secrets" / "google_service_account.json",
                )
            ),
            log_level=log_level,
            slow_request_threshold_ms=_env_int("CHECKSTOCK_SLOW_REQUEST_THRESHOLD_MS", 1_000, minimum=1),
            background_sync_enabled=not _env_bool("CHECKSTOCK_DISABLE_BACKGROUND_SYNC", False),
            funnel_orders_sync_enabled=_env_bool("CHECKSTOCK_FUNNEL_ORDERS_SYNC_ENABLED", True),
            unit_economics_1c_price_sync_enabled=_env_bool(
                "CHECKSTOCK_UNIT_ECONOMICS_1C_PRICE_SYNC_ENABLED", True
            ),
            unit_economics_1c_source_sync_hour=_env_int(
                "CHECKSTOCK_UNIT_ECONOMICS_1C_SOURCE_SYNC_HOUR", 2, maximum=23
            ),
            token_check_interval_seconds=_env_int(
                "CHECKSTOCK_TOKEN_CHECK_INTERVAL_SECONDS", 24 * 60 * 60, minimum=1
            ),
            unit_economics_1c_price_sync_startup_delay_seconds=_env_int(
                "CHECKSTOCK_UNIT_ECONOMICS_1C_PRICE_SYNC_STARTUP_DELAY_SECONDS", 5, minimum=0
            ),
            wb_advertising_sync_startup_delay_seconds=_env_int(
                "CHECKSTOCK_WB_ADVERTISING_SYNC_STARTUP_DELAY_SECONDS", 10, minimum=0
            ),
            decision_sync_startup_delay_seconds=_env_int(
                "CHECKSTOCK_DECISION_SYNC_STARTUP_DELAY_SECONDS", 20, minimum=0
            ),
            auto_sync_interval_seconds=_env_int("CHECKSTOCK_AUTO_SYNC_INTERVAL_SECONDS", 30 * 60, minimum=1),
            catalog_sync_hour=_env_int("CHECKSTOCK_CATALOG_SYNC_HOUR", 3, maximum=23),
            wb_advertising_sync_interval_seconds=_env_int(
                "CHECKSTOCK_WB_ADVERTISING_SYNC_INTERVAL_SECONDS", 15 * 60, minimum=1
            ),
            wb_funnel_orders_sync_interval_seconds=_env_int(
                "CHECKSTOCK_WB_FUNNEL_ORDERS_SYNC_INTERVAL_SECONDS", 15 * 60, minimum=1
            ),
            unit_economics_1c_price_sync_interval_seconds=_env_int(
                "CHECKSTOCK_UNIT_ECONOMICS_1C_PRICE_SYNC_INTERVAL_SECONDS",
                2 * 60 * 60,
                minimum=1,
            ),
            unit_economics_1c_wallet_sync_interval_seconds=_env_int(
                "CHECKSTOCK_UNIT_ECONOMICS_1C_WALLET_SYNC_INTERVAL_SECONDS",
                5 * 60,
                minimum=1,
            ),
            wb_storefront_dest=os.getenv("CHECKSTOCK_WB_STOREFRONT_DEST", "-1257786").strip() or "-1257786",
            wb_storefront_batch_size=_env_int(
                "CHECKSTOCK_WB_STOREFRONT_BATCH_SIZE", 1_000, minimum=1, maximum=1_000
            ),
            decision_sync_check_interval_seconds=_env_int(
                "CHECKSTOCK_DECISION_SYNC_INTERVAL_SECONDS", 15 * 60, minimum=1
            ),
            session_ttl_days=_env_int("CHECKSTOCK_SESSION_TTL_DAYS", 14, minimum=1),
            session_cookie_secure=_env_bool("CHECKSTOCK_SESSION_COOKIE_SECURE", False),
            pbkdf2_iterations=_env_int(
                "CHECKSTOCK_PBKDF2_ITERATIONS",
                200_000,
                minimum=MIN_PBKDF2_ITERATIONS,
                maximum=MAX_PBKDF2_ITERATIONS,
            ),
            stock_window_days=_env_int("CHECKSTOCK_STOCK_WINDOW_DAYS", 30, minimum=1),
            stock_frozen_days=_env_int("CHECKSTOCK_STOCK_FROZEN_DAYS", 60, minimum=1),
            stock_excess_days=_env_int("CHECKSTOCK_STOCK_EXCESS_DAYS", 90, minimum=1),
            stock_cache_ttl_seconds=_env_int("CHECKSTOCK_STOCK_CACHE_TTL_SECONDS", 5 * 60, minimum=1),
            stock_detail_page_size=_env_int("CHECKSTOCK_STOCK_DETAIL_PAGE_SIZE", 100, minimum=1),
            warehouse_display_limit=_env_int("CHECKSTOCK_WAREHOUSE_DISPLAY_LIMIT", 8, minimum=1),
            operation_history_limit=_env_int("CHECKSTOCK_OPERATION_HISTORY_LIMIT", 500, minimum=1),
            ff_import_timeout_seconds=_env_int("CHECKSTOCK_FF_IMPORT_TIMEOUT_SECONDS", 30, minimum=1),
            wb_request_timeout_seconds=_env_int("CHECKSTOCK_WB_REQUEST_TIMEOUT_SECONDS", 30, minimum=1),
            ozon_request_timeout_seconds=_env_int("CHECKSTOCK_OZON_REQUEST_TIMEOUT_SECONDS", 60, minimum=1),
            yandex_request_timeout_seconds=_env_int(
                "CHECKSTOCK_YANDEX_REQUEST_TIMEOUT_SECONDS", 60, minimum=1
            ),
            wb_request_attempts=_env_int("CHECKSTOCK_WB_REQUEST_ATTEMPTS", 3, minimum=1, maximum=20),
            ozon_request_attempts=_env_int("CHECKSTOCK_OZON_REQUEST_ATTEMPTS", 5, minimum=1, maximum=20),
            yandex_request_attempts=_env_int("CHECKSTOCK_YANDEX_REQUEST_ATTEMPTS", 4, minimum=1, maximum=20),
            wb_retry_backoff_seconds=_env_int("CHECKSTOCK_WB_RETRY_BACKOFF_SECONDS", 5, minimum=0),
            ozon_retry_backoff_seconds=_env_int("CHECKSTOCK_OZON_RETRY_BACKOFF_SECONDS", 5, minimum=0),
            yandex_retry_backoff_seconds=_env_int("CHECKSTOCK_YANDEX_RETRY_BACKOFF_SECONDS", 5, minimum=0),
            wb_sales_max_pages=_env_int("CHECKSTOCK_WB_SALES_MAX_PAGES", 30, minimum=1),
            rnp_sync_cooldown_minutes=_env_int("CHECKSTOCK_RNP_SYNC_COOLDOWN_MINUTES", 55, minimum=0),
            rnp_report_download_timeout_seconds=_env_int(
                "CHECKSTOCK_RNP_REPORT_DOWNLOAD_TIMEOUT_SECONDS", 45, minimum=1
            ),
            rnp_report_poll_attempts=_env_int("CHECKSTOCK_RNP_REPORT_POLL_ATTEMPTS", 18, minimum=1),
            rnp_report_poll_interval_seconds=_env_int(
                "CHECKSTOCK_RNP_REPORT_POLL_INTERVAL_SECONDS", 1, minimum=0
            ),
            experimental_owner_logins=_env_csv("CHECKSTOCK_EXPERIMENTAL_OWNER_LOGINS"),
            smtp_host=os.getenv("CHECKSTOCK_SMTP_HOST", "").strip(),
            smtp_port=_env_int("CHECKSTOCK_SMTP_PORT", 587, minimum=1, maximum=65_535),
            smtp_username=os.getenv("CHECKSTOCK_SMTP_USERNAME", "").strip(),
            smtp_password=os.getenv("CHECKSTOCK_SMTP_PASSWORD", ""),
            smtp_sender=os.getenv("CHECKSTOCK_SMTP_SENDER", "").strip(),
            smtp_starttls=_env_bool("CHECKSTOCK_SMTP_STARTTLS", True),
        )


settings = Settings.from_env()
