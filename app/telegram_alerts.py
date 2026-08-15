from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from urllib import parse, request

TELEGRAM_API_BASE = "https://api.telegram.org"
MAX_TELEGRAM_MESSAGE_LENGTH = 3900
DEFAULT_ALERT_COOLDOWN_SECONDS = 300

_BOT_URL_PATTERN = re.compile(r"/bot\d+:[A-Za-z0-9_-]+/")
_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|api[_-]?key|authorization|password)\b([\s:=\"']+)([^\s,;\"']+)"
)


def redact_secrets(value: str) -> str:
    value = _BOT_URL_PATTERN.sub("/bot<redacted>/", value)
    return _SECRET_PATTERN.sub(r"\1\2<redacted>", value)


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    timeout_seconds: float = 10,
) -> None:
    token = bot_token.strip()
    recipient = chat_id.strip()
    if not token or not recipient:
        raise ValueError("Telegram bot token and chat id are required")

    url = f"{TELEGRAM_API_BASE}/bot{parse.quote(token, safe=':')}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": recipient,
            "text": text[:MAX_TELEGRAM_MESSAGE_LENGTH],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    http_request = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "CheckStock/telegram-alerts"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=timeout_seconds) as response:
        response_body = response.read()
    result = json.loads(response_body.decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API rejected message: {result.get('description', 'unknown error')}")


def format_error_record(record: logging.LogRecord) -> str:
    request_id = str(getattr(record, "request_id", "-") or "-")
    lines = [
        "🚨 CheckStock: ошибка приложения",
        f"Сервер: {socket.gethostname()}",
        f"Время UTC: {datetime.now(UTC).isoformat(timespec='seconds')}",
        f"Логгер: {record.name}",
        f"Request ID: {request_id}",
        f"Сообщение: {record.getMessage()}",
    ]
    if record.exc_info:
        exception_text = "".join(traceback.format_exception(*record.exc_info)).strip()
        if exception_text:
            lines.extend(("", "Traceback:", exception_text))
    return redact_secrets("\n".join(lines))[:MAX_TELEGRAM_MESSAGE_LENGTH]


class TelegramErrorHandler(logging.Handler):
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        cooldown_seconds: int = DEFAULT_ALERT_COOLDOWN_SECONDS,
        sender: Callable[[str, str, str], None] = send_telegram_message,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(level=logging.ERROR)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.sender = sender
        self.clock = clock
        self._recent: dict[str, float] = {}
        self._recent_lock = threading.Lock()
        self._messages: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=100)
        self._worker = threading.Thread(
            target=self._send_messages,
            name="telegram-error-alerts",
            daemon=True,
        )
        self._worker.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.exc_info:
                exception_type, exception, _traceback = record.exc_info
                fingerprint_source = f"{exception_type.__name__}|{exception}"
            else:
                fingerprint_source = f"{record.name}|{record.getMessage()}"
            fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
            now = self.clock()
            with self._recent_lock:
                previous = self._recent.get(fingerprint)
                if previous is not None and now - previous < self.cooldown_seconds:
                    return
                self._recent[fingerprint] = now
                expiry = now - max(self.cooldown_seconds * 2, 60)
                self._recent = {key: seen_at for key, seen_at in self._recent.items() if seen_at >= expiry}
            self._messages.put_nowait((fingerprint, format_error_record(record)))
        except Exception as exc:
            self._write_internal_error(f"could not enqueue alert: {exc}")

    def _send_messages(self) -> None:
        while True:
            fingerprint, message = self._messages.get()
            try:
                self.sender(self.bot_token, self.chat_id, message)
            except Exception as exc:
                with self._recent_lock:
                    self._recent.pop(fingerprint, None)
                self._write_internal_error(f"could not send alert: {exc}")
            finally:
                self._messages.task_done()

    @staticmethod
    def _write_internal_error(message: str) -> None:
        sys.stderr.write(f"telegram_alerts: {redact_secrets(message)}\n")


def telegram_handler_from_env(
    environment: Mapping[str, str] | None = None,
) -> TelegramErrorHandler | None:
    values = environment or os.environ
    token = values.get("CHECKSTOCK_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = values.get("CHECKSTOCK_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    try:
        cooldown = int(
            values.get(
                "CHECKSTOCK_TELEGRAM_ALERT_COOLDOWN_SECONDS",
                str(DEFAULT_ALERT_COOLDOWN_SECONDS),
            )
        )
    except ValueError:
        cooldown = DEFAULT_ALERT_COOLDOWN_SECONDS
    return TelegramErrorHandler(token, chat_id, cooldown_seconds=cooldown)
