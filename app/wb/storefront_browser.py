"""Renew the anonymous WB session in a separate browser profile."""

import json
import logging
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

from app.config import BASE_DIR, settings
from app.jobs import locks
from app.wb import storefront_session

logger = logging.getLogger(__name__)
LOCK_WAIT_SECONDS = 150
FAILURE_COOLDOWN_SECONDS = 120


def browser_path(configured: Path | None) -> Path:
    if configured is None and os.environ.get("CHECKSTOCK_WB_BROWSER_PATH"):
        configured = Path(os.environ["CHECKSTOCK_WB_BROWSER_PATH"])
    if configured:
        candidates = [configured]
    else:
        candidates = [
            Path(os.environ[name]) / "Yandex/YandexBrowser/Application/browser.exe"
            for name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")
            if os.environ.get(name)
        ]
        if os.name != "nt":
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                candidates.append(Path(playwright.chromium.executable_path))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise RuntimeError(
        "Браузер WB не найден. Установите Яндекс Браузер (Windows) или Chromium Playwright (Linux), "
        "либо задайте CHECKSTOCK_WB_BROWSER_PATH."
    )


def verify_session(data: dict, article: str) -> None:
    params = {
        "appType": 1,
        "curr": "rub",
        "dest": settings.wb_storefront_dest,
        "lang": "ru",
        "spp": 30,
        "nm": article,
    }
    headers = {
        "Accept": "application/json",
        "Referer": "https://www.wildberries.ru/",
        **storefront_session.request_headers(data),
    }
    request = urllib.request.Request(
        storefront_session.CARDS_URL + "?" + urllib.parse.urlencode(params), headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise RuntimeError(f"WB не принял подготовленный сеанс (HTTP {status}).") from None
    except (OSError, ValueError):
        raise RuntimeError("Не удалось проверить ответ WB. Прежний сеанс сохранён.") from None
    rows = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("WB не вернул корректный ответ карточек; новый сеанс не сохранён.")


def _prepare_browser(article: str, executable_path: Path | None, wait_seconds: int) -> Path:
    try:
        import playwright.sync_api  # noqa: F401 - Check the optional dependency before starting Xvfb.
    except ImportError:
        raise RuntimeError("Для обновления сеанса WB установите requirements-parser.txt.") from None

    if os.name != "nt" and not os.environ.get("DISPLAY"):
        from pyvirtualdisplay import Display

        with Display(visible=False, size=(1440, 1000), manage_global_env=False) as display:
            return _prepare_browser_in_environment(article, executable_path, wait_seconds, display.env())
    return _prepare_browser_in_environment(article, executable_path, wait_seconds, os.environ.copy())


def _prepare_browser_in_environment(
    article: str, executable_path: Path | None, wait_seconds: int, environment: dict[str, str]
) -> Path:
    from playwright.sync_api import sync_playwright

    executable = browser_path(executable_path)
    profile = BASE_DIR / "data/wb-storefront/browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen(
        [
            str(executable),
            "--user-data-dir=" + str(profile),
            "--remote-debugging-port=" + str(port),
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            f"https://www.wildberries.ru/catalog/{article}/detail.aspx",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        startupinfo=startupinfo,
        env=environment,
    )
    browser = None
    try:
        logger.info("Ожидаем автоматическую проверку WB в отдельном профиле браузера")
        # Let the regular browser finish its first navigation before attaching CDP.
        time.sleep(25)
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=15000)
                context = browser.contexts[0]
                page = next((tab for tab in context.pages if "wildberries.ru/catalog/" in tab.url), None)
                if page is None:
                    raise RuntimeError("Карточка WB не открылась в тестовом браузере.")
                page.set_default_timeout(5000)
                deadline = time.monotonic() + wait_seconds
                while time.monotonic() < deadline:
                    body = page.locator("body").inner_text()
                    if "Возможно, нужно выключить VPN" in body:
                        raise RuntimeError("WB отклонил подключение браузера и предлагает отключить VPN.")
                    resources = page.evaluate(
                        "performance.getEntriesByType('resource').map(entry => entry.name)"
                    )
                    if "Подозрительная активность" not in body and any(
                        re.search(r"/([\d.]+)-host-remoteEntry\.js", url) for url in resources
                    ):
                        break
                    page.wait_for_timeout(1000)
                else:
                    raise RuntimeError("WB не завершил проверку браузера. Сеанс не обновлён.")
                resources = page.evaluate("performance.getEntriesByType('resource').map(entry => entry.name)")
                version = next(
                    (
                        match[1]
                        for url in resources
                        if (match := re.search(r"/([\d.]+)-host-remoteEntry\.js", url))
                    ),
                    None,
                )
                cookie = next(
                    (
                        item
                        for item in context.cookies("https://www.wildberries.ru")
                        if item["name"] == "x_wbaas_token"
                    ),
                    None,
                )
                if not version or not cookie:
                    raise RuntimeError("Не удалось получить версию витрины или служебный сеанс WB.")
                data = {
                    "token": cookie["value"],
                    "expires_at": cookie["expires"],
                    "user_agent": page.evaluate("navigator.userAgent"),
                    "spa_version": version,
                    "device_id": str(uuid4()),
                    "prepared_at": time.time(),
                }
                verify_session(data, article)
                return storefront_session.save_session(data)
            finally:
                if browser:
                    with suppress(Exception):
                        browser.new_browser_cdp_session().send("Browser.close")
                    with suppress(Exception):
                        browser.close()
    finally:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


@contextmanager
def _preparation_lock():
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            handle = locks.acquire("wb_storefront_session_prepare")
            break
        except locks.SyncJobBusyError:
            if time.monotonic() >= deadline:
                raise storefront_session.StorefrontSessionError(
                    "Обновление сеанса WB уже выполняется и не завершилось вовремя."
                ) from None
            time.sleep(0.2)
    try:
        yield
    finally:
        handle.close()


def prepare(
    article: str = "153985484",
    *,
    executable_path: Path | None = None,
    wait_seconds: int = 90,
    rejected_fingerprint: str | None = None,
    force: bool = True,
) -> Path:
    """Serialize refreshes and reuse a session another worker has just prepared."""
    if not article.isascii() or not article.isdigit() or int(article) <= 0:
        raise ValueError("Для проверки сеанса нужен числовой артикул WB.")
    with _preparation_lock():
        if not force:
            try:
                current = storefront_session.read_session()
            except storefront_session.StorefrontSessionError:
                current = None
            if current is not None and storefront_session.fingerprint(current) != rejected_fingerprint:
                logger.info("Сеанс WB уже обновлён другой задачей; используем его")
                return storefront_session.session_path()
        failure_path = storefront_session.session_path().with_suffix(".failure.json")
        if not force:
            try:
                failure = json.loads(failure_path.read_text(encoding="utf-8"))
                retry_at = float(failure["retry_at"])
            except (OSError, ValueError, TypeError, KeyError):
                retry_at = 0
            if retry_at > time.time():
                raise storefront_session.StorefrontSessionError(
                    f"Сеанс WB не удалось обновить. Повтор через {max(1, int(retry_at - time.time()))} сек."
                )
        logger.info("Обновляем анонимный сеанс WB")
        try:
            path = _prepare_browser(article, executable_path, wait_seconds)
        except Exception as error:
            # Browser exceptions can include private request headers; don't expose them.
            message = (
                str(error)
                if type(error) in {RuntimeError, storefront_session.StorefrontSessionError}
                else type(error).__name__
            )
            with suppress(OSError):
                failure_path.parent.mkdir(parents=True, exist_ok=True)
                failure_path.write_text(
                    json.dumps({"retry_at": time.time() + FAILURE_COOLDOWN_SECONDS}), encoding="utf-8"
                )
            raise storefront_session.StorefrontSessionError(
                "Не удалось автоматически обновить сеанс WB: " + message
            ) from None
        with suppress(OSError):
            failure_path.unlink(missing_ok=True)
        logger.info("Сеанс WB обновлён и проверен")
        return path
