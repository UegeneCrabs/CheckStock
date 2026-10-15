from dataclasses import dataclass
from enum import StrEnum
from typing import Final


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
