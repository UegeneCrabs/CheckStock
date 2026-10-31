import contextvars
import json
import logging
import sys
import uuid
from collections.abc import Mapping, MutableMapping
from copy import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings, settings
from app.integrations.bitrix import bitrix_handler_from_env

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
MOSCOW = ZoneInfo("Europe/Moscow")
LEVEL_COLORS = {
    logging.DEBUG: "\x1b[36m",
    logging.INFO: "\x1b[32m",
    logging.WARNING: "\x1b[33m",
    logging.ERROR: "\x1b[31m",
    logging.CRITICAL: "\x1b[91m",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class LogTags:
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in self.values:
            if (
                not isinstance(value, str)
                or not value.strip()
                or value != value.strip()
                or any(ord(character) < 32 or character in '"\\' for character in value)
            ):
                raise ValueError(
                    "Log tags must be nonempty strings without control characters, quotes or backslashes"
                )


def log_tags(*values: str) -> dict[str, LogTags]:
    return {"log_tags": LogTags(values=values)}


class TaggedLogger(logging.LoggerAdapter[logging.Logger]):
    def __init__(self, logger: logging.Logger, *tags: str) -> None:
        super().__init__(logger, {})
        self.tags = LogTags(values=tags)

    def process(
        self, msg: object, kwargs: MutableMapping[str, Any]
    ) -> tuple[object, MutableMapping[str, Any]]:
        extra = kwargs.get("extra")
        more = extra.get("log_tags") if isinstance(extra, Mapping) else None
        if more is not None and not isinstance(more, LogTags):
            raise TypeError("extra['log_tags'] must be LogTags")
        values = tuple(dict.fromkeys((*self.tags.values, *(more.values if more else ()))))
        kwargs["extra"] = {
            **(extra or {}),
            "log_tags": LogTags(values=values),
        }
        return msg, kwargs


def tagged_logger(name: str, *tags: str) -> TaggedLogger:
    return TaggedLogger(logging.getLogger(name), *tags)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_context.get()
        return True


def event_time(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, MOSCOW).isoformat(timespec="milliseconds")


class CompactFormatter(logging.Formatter):
    def __init__(self, *, use_colors: bool) -> None:
        super().__init__()
        self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        record = copy(record)
        tags = getattr(record, "log_tags", None)
        if tags is not None and not isinstance(tags, LogTags):
            raise TypeError("record.log_tags must be LogTags")
        level = record.levelname
        if self.use_colors and (color := LEVEL_COLORS.get(record.levelno)):
            level = f"{color}{level}\x1b[0m"
        parts = [event_time(record), level, record.name]
        if tags and tags.values:
            parts.append(json.dumps(tags.values, ensure_ascii=False))
        message = record.getMessage()
        request_id = getattr(record, "request_id", "-")
        if request_id != "-":
            message = f"{message} request_id={request_id}"
        parts.append(message)
        formatted = " | ".join(parts)
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            formatted += "\n" + self.formatStack(record.stack_info)
        return formatted


class TagCatalogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"timestamp": event_time(record), "tag": record.catalog_tag}, ensure_ascii=False)


class TagCatalogHandler(logging.FileHandler):
    def __init__(self, filename: Path) -> None:
        super().__init__(filename, encoding="utf-8")
        self.setFormatter(TagCatalogFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        tags = getattr(record, "log_tags", None)
        if not isinstance(tags, LogTags):
            return
        for tag in dict.fromkeys(tags.values):
            entry = copy(record)
            entry.catalog_tag = tag
            super().emit(entry)


def configure_logging(app_settings: Settings = settings) -> None:
    level = getattr(logging, app_settings.log_level, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestContextFilter())
    handler.setFormatter(CompactFormatter(use_colors=sys.stdout.isatty()))
    root = logging.getLogger()
    for previous in root.handlers[:]:
        root.removeHandler(previous)
        previous.close()
    root.addHandler(handler)
    if app_settings.log_file is not None:
        app_settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(app_settings.log_file, encoding="utf-8")
        file_handler.addFilter(RequestContextFilter())
        file_handler.setFormatter(CompactFormatter(use_colors=False))
        root.addHandler(file_handler)
        catalog_path = app_settings.log_file.with_name(f"{app_settings.log_file.stem}.tags.log")
        root.addHandler(TagCatalogHandler(catalog_path))
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
