"""Small 2Captcha client. Credentials and solutions are never logged."""

import json
import os
import re
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

from app.config import BASE_DIR


class CaptchaError(RuntimeError):
    pass


def api_key() -> str:
    value = os.environ.get("CHECKSTOCK_CAPTCHA_API_KEY", "").strip()
    if not value:
        path = Path(os.environ.get("CHECKSTOCK_CAPTCHA_KEY_PATH", BASE_DIR / "secrets/captcha.json"))
        if path.is_file():
            value = str(json.loads(path.read_text(encoding="utf-8-sig")).get("api_key") or "").strip()
    if not value:
        raise CaptchaError("Не настроен ключ 2Captcha")
    return value


class Client:
    def __init__(self, key: str | None = None, *, timeout: float = 180, max_tasks: int = 20):
        self.key = key or api_key()
        self.timeout = timeout
        self.max_tasks = max_tasks
        self.submitted = 0
        self.cost = Decimal(0)

    def request(self, method: str, **payload) -> dict:
        request = urllib.request.Request(
            "https://api.2captcha.com/" + method,
            data=json.dumps({"clientKey": self.key, **payload}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.load(response)
        except (OSError, ValueError):

            raise CaptchaError("2Captcha: ошибка соединения или ответа") from None
        if result.get("errorId"):
            code = str(result.get("errorCode") or "API_ERROR")
            code = code if re.fullmatch(r"[A-Z_0-9]{1,80}", code) else "API_ERROR"
            raise CaptchaError("2Captcha: " + code)
        return result

    def balance(self) -> Decimal:
        return Decimal(str(self.request("getBalance")["balance"]))

    def solve(self, task: dict) -> dict:
        if self.submitted >= self.max_tasks:
            raise CaptchaError("Достигнут лимит решений капчи за один обход")
        self.submitted += 1
        created = self.request("createTask", task=task)
        task_id = created.get("taskId")
        if not task_id:
            raise CaptchaError("2Captcha не вернула ID задачи")
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            time.sleep(5)
            result = self.request("getTaskResult", taskId=task_id)
            if result.get("status") == "ready":
                self.cost += Decimal(str(result.get("cost") or 0))
                return result["solution"]
            if result.get("status") != "processing":
                raise CaptchaError("2Captcha: неизвестное состояние задачи")
        raise CaptchaError("2Captcha не ответила за отведённое время; новая задача не создана")
