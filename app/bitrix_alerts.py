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
from urllib import request

MAX_ALERT_MESSAGE_LENGTH = 3900
DEFAULT_ALERT_COOLDOWN_SECONDS = 300

_BITRIX_WEBHOOK_PATTERN = re.compile(r"(?i)(https?://[^/\s]+/rest/\d+/)[^/\s]+(?=/|$)")
_SECRET_PATTERN = re.compile(
    r"(?i)\b(token|api[_-]?key|authorization|password)\b([\s:=\"']+)([^\s,;\"']+)"
)


def redact_secrets(value: str) -> str:
    value = _BITRIX_WEBHOOK_PATTERN.sub(r"\1<redacted>", value)
    return _SECRET_PATTERN.sub(r"\1\2<redacted>", value)


def send_bitrix_message(
    webhook_url: str,
    dialog_id: str,
    text: str,
    *,
    timeout_seconds: float = 10,
) -> None:
    webhook = webhook_url.strip().rstrip("/")
    dialog = dialog_id.strip()
    if not webhook or not dialog:
        raise ValueError("Bitrix webhook URL and dialog id are required")
    url = webhook if webhook.endswith(".json") else f"{webhook}/im.message.add.json"
    payload = json.dumps(
        {
            "DIALOG_ID": dialog,
            "MESSAGE": text[:MAX_ALERT_MESSAGE_LENGTH],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    http_request = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "CheckStock/alerts"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=timeout_seconds) as response:
        response_body = response.read()
    result = json.loads(response_body.decode("utf-8"))
    if result.get("error") or "result" not in result:
        raise RuntimeError(f"Bitrix API rejected message: {result.get('error_description', 'unknown error')}")


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
    return redact_secrets("\n".join(lines))[:MAX_ALERT_MESSAGE_LENGTH]


class NotificationErrorHandler(logging.Handler):
    def __init__(
        self,
        credential: str,
        destination: str,
        *,
        cooldown_seconds: int = DEFAULT_ALERT_COOLDOWN_SECONDS,
        sender: Callable[[str, str, str], None],
        clock: Callable[[], float] = time.monotonic,
        channel_name: str = "notification",
    ) -> None:
        super().__init__(level=logging.ERROR)
        self.credential = credential
        self.destination = destination
        self.cooldown_seconds = max(0, cooldown_seconds)
        self.sender = sender
        self.clock = clock
        self.channel_name = channel_name
        self._recent: dict[str, float] = {}
        self._recent_lock = threading.Lock()
        self._messages: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=100)
        self._worker = threading.Thread(
            target=self._send_messages,
            name=f"{channel_name}-error-alerts",
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
                self.sender(self.credential, self.destination, message)
            except Exception as exc:
                with self._recent_lock:
                    self._recent.pop(fingerprint, None)
                self._write_internal_error(f"could not send alert: {exc}")
            finally:
                self._messages.task_done()

    @staticmethod
    def _write_internal_error(message: str) -> None:
        sys.stderr.write(f"notification_alerts: {redact_secrets(message)}\n")


class BitrixErrorHandler(NotificationErrorHandler):
    def __init__(
        self,
        webhook_url: str,
        dialog_id: str,
        *,
        cooldown_seconds: int = DEFAULT_ALERT_COOLDOWN_SECONDS,
        sender: Callable[[str, str, str], None] = send_bitrix_message,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            webhook_url,
            dialog_id,
            cooldown_seconds=cooldown_seconds,
            sender=sender,
            clock=clock,
            channel_name="bitrix",
        )


def bitrix_handler_from_env(
    environment: Mapping[str, str] | None = None,
) -> BitrixErrorHandler | None:
    values = environment or os.environ
    webhook_url = values.get("CHECKSTOCK_BITRIX_WEBHOOK_URL", "").strip()
    dialog_id = values.get("CHECKSTOCK_BITRIX_DIALOG_ID", "").strip()
    if not webhook_url or not dialog_id:
        return None
    try:
        cooldown = int(
            values.get(
                "CHECKSTOCK_BITRIX_ALERT_COOLDOWN_SECONDS",
                str(DEFAULT_ALERT_COOLDOWN_SECONDS),
            )
        )
    except ValueError:
        cooldown = DEFAULT_ALERT_COOLDOWN_SECONDS
    return BitrixErrorHandler(webhook_url, dialog_id, cooldown_seconds=cooldown)
