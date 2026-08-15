import json
import os
import socket
import time
from datetime import UTC, datetime
from urllib import request

from collections.abc import Callable

from app.bitrix_alerts import send_bitrix_message


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def check_ready(url: str, timeout_seconds: int) -> tuple[bool, str]:
    try:
        http_request = request.Request(url, headers={"User-Agent": "CheckStock/watchdog"})
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        ready = response.status == 200 and payload.get("status") == "ok" and payload.get("database") == "ok"
        return ready, json.dumps(payload, ensure_ascii=False)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def notifier_from_env(environment: dict[str, str] | None = None) -> Callable[[str], None] | None:
    values = environment or os.environ
    bitrix_webhook_url = values.get("CHECKSTOCK_BITRIX_WEBHOOK_URL", "").strip()
    bitrix_dialog_id = values.get("CHECKSTOCK_BITRIX_DIALOG_ID", "").strip()
    if bitrix_webhook_url and bitrix_dialog_id:
        return lambda message: send_bitrix_message(bitrix_webhook_url, bitrix_dialog_id, message)
    return None


def notify(sender: Callable[[str], None], message: str) -> bool:
    try:
        sender(message)
        return True
    except Exception as exc:
        print(f"watchdog notification failed: {type(exc).__name__}: {exc}", flush=True)
        return False


def main() -> None:
    sender = notifier_from_env()
    if sender is None:
        print("watchdog disabled: alert channel is not configured", flush=True)
        while True:
            time.sleep(3600)

    url = os.getenv("CHECKSTOCK_WATCHDOG_URL", "http://app:8000/readyz").strip()
    interval_seconds = _env_int("CHECKSTOCK_WATCHDOG_INTERVAL_SECONDS", 60)
    timeout_seconds = _env_int("CHECKSTOCK_WATCHDOG_TIMEOUT_SECONDS", 10)
    failure_threshold = _env_int("CHECKSTOCK_WATCHDOG_FAILURE_THRESHOLD", 3)
    hostname = socket.gethostname()
    failures = 0
    outage_alerted = False

    print(
        f"watchdog started url={url} interval={interval_seconds}s threshold={failure_threshold}",
        flush=True,
    )
    while True:
        ready, detail = check_ready(url, timeout_seconds)
        timestamp = datetime.now(UTC).isoformat(timespec="seconds")
        if ready:
            failures = 0
            if outage_alerted:
                message = (
                    "✅ CheckStock снова работает\n"
                    f"Монитор: {hostname}\n"
                    f"Время UTC: {timestamp}\n"
                    f"Проверка: {detail}"
                )
                if notify(sender, message):
                    outage_alerted = False
        else:
            failures += 1
            print(f"watchdog failure {failures}/{failure_threshold}: {detail}", flush=True)
            if failures >= failure_threshold and not outage_alerted:
                message = (
                    "🔴 CheckStock недоступен\n"
                    f"Монитор: {hostname}\n"
                    f"Время UTC: {timestamp}\n"
                    f"Неудачных проверок подряд: {failures}\n"
                    f"Причина: {detail}"
                )
                if notify(sender, message):
                    outage_alerted = True
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
