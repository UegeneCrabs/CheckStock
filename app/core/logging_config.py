import contextvars
import logging
import sys
import uuid
from copy import copy

from app.config import Settings, settings
from app.integrations.bitrix import bitrix_handler_from_env

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
LEVEL_COLORS = {
    logging.DEBUG: "\x1b[36m",
    logging.INFO: "\x1b[32m",
    logging.WARNING: "\x1b[33m",
    logging.ERROR: "\x1b[31m",
    logging.CRITICAL: "\x1b[91m",
}


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


class CompactFormatter(logging.Formatter):
    def __init__(self, fmt: str, *, datefmt: str, use_colors: bool) -> None:
        super().__init__(fmt, datefmt=datefmt)
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        record = copy(record)
        record.short_name = record.name.removeprefix("app.")
        request_id = getattr(record, "request_id", "-")
        record.request_context = f" request={request_id[:8]}" if request_id != "-" else ""
        if self.use_colors and (color := LEVEL_COLORS.get(record.levelno)):
            record.levelname = f"{color}{record.levelname}\x1b[0m"
        return super().format(record)


def configure_logging(app_settings: Settings = settings) -> None:
    level = getattr(logging, app_settings.log_level, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(
        CompactFormatter(
            "%(asctime)s %(levelname)s %(short_name)s%(request_context)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            use_colors=sys.stdout.isatty(),
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    bitrix_handler = bitrix_handler_from_env()
    if bitrix_handler is not None:
        root.addHandler(bitrix_handler)
    root.setLevel(level)
    logging.captureWarnings(True)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def new_request_id(value: str | None) -> str:
    normalized = (value or "").strip()
    safe = "".join(character for character in normalized if character.isalnum() or character in "-_.")
    return safe[:128] if safe else uuid.uuid4().hex
