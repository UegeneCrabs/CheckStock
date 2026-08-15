import json
import os
import socket
import time
from datetime import UTC, datetime
from urllib import request

from app.telegram_alerts import send_telegram_message


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


def notify(bot_token: str, chat_id: str, message: str) -> bool:
    try:
        send_telegram_message(bot_token, chat_id, message)
        return True
    except Exception as exc:
        print(f"watchdog notification failed: {type(exc).__name__}: {exc}", flush=True)
        return False


def main() -> None:
    bot_token = os.getenv("CHECKSTOCK_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("CHECKSTOCK_TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        print("watchdog disabled: Telegram credentials are not configured", flush=True)
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
                if notify(bot_token, chat_id, message):
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
                if notify(bot_token, chat_id, message):
                    outage_alerted = True
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
