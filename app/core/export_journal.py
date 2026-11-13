import logging
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TextIO

from app.core.constants import SCHEDULER, TIME
from app.integrations.bitrix import redact_secrets

_SHARD = re.compile(r"(\d{4}-\d{2}-\d{2})\.\d+\.log")
_AUTH_HEADER = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization)[\"']?\s*[:=]\s*[\"']?)(?:bearer|basic)\s+[^\s,;\"']+"
)
_URL_AUTH = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@")
_PEM_KEY = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:key|token|access_token|refresh_token|api_key|password|signature|client_secret)=)[^&\s\"']+"
)
_COOKIE_HEADER = re.compile(r"(?im)(\b(?:set-cookie|cookie)[\"']?\s*[:=]\s*)[^\r\n]+")
_SECRET_VALUE = re.compile(
    r"""(?i)(["']?(?:access_token|refresh_token|client_secret|private_key|set-cookie|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)"""
)
_MAX_DIAGNOSTIC_KEYS = 50_000
_diagnostics = logging.getLogger("app.exports.diagnostics")


def clean_detail(value: str) -> str:
    value = _PEM_KEY.sub("<redacted>", value)
    value = _AUTH_HEADER.sub(r"\1<redacted>", value)
    value = _URL_AUTH.sub(r"\1<redacted>@", value)
    value = _JWT.sub("<redacted>", value)
    value = _QUERY_SECRET.sub(r"\1<redacted>", value)
    value = _COOKIE_HEADER.sub(r"\1<redacted>", value)
    value = _SECRET_VALUE.sub(r"\1<redacted>", value)
    return redact_secrets(value).replace("\x00", "")


def journal_day() -> date:
    return datetime.now(TIME.moscow_timezone).date()


def oldest_day(today: date) -> date:
    return today - timedelta(days=SCHEDULER.job_run_retention_days - 1)


def diagnostic(event: str, *, detail: object, **context: object) -> None:
    if not detail:
        return
    fields = " ".join(f"{key}={value}" for key, value in context.items() if value is not None and value != "")
    _diagnostics.warning(
        "%s %s detail=%s", event, fields, detail, extra={"journal_only": True, "journal_deduplicate": True}
    )


def product_diagnostics(marketplace: str, products: Iterable[Mapping[str, object]]) -> None:
    for product in products:
        details = [product.get(key) for key in ("data_errors", "data_issues", "current_issues")]
        for scope in ("current_economics", "economics_7d"):
            data = product.get(scope)
            if isinstance(data, Mapping):
                for field, value in data.items():
                    if field == "issues" and value:
                        details.append({scope: value})
                    elif (
                        isinstance(value, Mapping)
                        and field.endswith("_coverage")
                        and value.get("complete") is False
                    ):
                        details.append({scope: {field: value}})
        if details := [item for item in details if item]:
            diagnostic(
                "product_data_incomplete",
                marketplace=marketplace,
                store=product.get("store_slug") or product.get("store"),
                article=product.get("article"),
                detail=details,
            )


class DailyExportJournal(logging.Handler):
    def __init__(self, directory: Path) -> None:
        super().__init__(level=logging.WARNING)
        self.directory = directory
        self.day: date | None = None
        self.stream: TextIO | None = None
        self.seen: set[bytes] = set()

    def _rotate(self, day: date) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.directory.mkdir(parents=True, exist_ok=True)
        for shard in self.directory.iterdir():
            match = _SHARD.fullmatch(shard.name)
            if match and match[1] < oldest_day(day).isoformat():
                shard.unlink(missing_ok=True)
        self.stream = (self.directory / f"{day.isoformat()}.{os.getpid()}.log").open("a", encoding="utf-8")
        self.day = day
        self.seen.clear()

    def emit(self, record: logging.LogRecord) -> None:
        if not (record.name.startswith("app.") or record.name == "__main__"):
            return
        try:
            day = datetime.fromtimestamp(record.created, TIME.moscow_timezone).date()
            if day != self.day or self.stream is None:
                self._rotate(day)
            if self.stream is None:
                return
            key = None
            if getattr(record, "journal_deduplicate", False):
                key = sha256(record.getMessage().encode()).digest()
                if key in self.seen:
                    return
            self.stream.write(clean_detail(self.format(record)) + "\n")
            self.stream.flush()
            if key is not None and len(self.seen) < _MAX_DIAGNOSTIC_KEYS:
                self.seen.add(key)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self.stream is not None:
                self.stream.close()
        finally:
            self.stream = None
            super().close()


def daily_log_paths(directory: Path, day: date) -> Iterator[Path]:
    for path in sorted(directory.glob(f"{day.isoformat()}.*.log")):
        if _SHARD.fullmatch(path.name) and not path.is_symlink():
            yield path
