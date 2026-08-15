import contextvars
import logging
import sys
import uuid

from app.config import Settings, settings
from app.bitrix_alerts import bitrix_handler_from_env

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


def configure_logging(app_settings: Settings = settings) -> None:
    level = getattr(logging, app_settings.log_level, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
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
