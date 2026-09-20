"""Цены ЯМ в БД. --loop: ежедневно в 01:00 и каждый час 08:00–19:00 по Москве."""

import argparse
import base64
import copy
import json
import logging
import math
import os
import re
import signal
import sys
import threading
import time
import traceback
import uuid
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import db
from app.core.stores import STORES
from app.jobs import settings as sync_settings
from app.jobs.locks import SyncJobBusyError
from app.jobs.tracking import run_tracked, set_next_run
from app.repositories import yandex_assortment
from app.repositories import yandex_storefront as repository
from app.yandex import storefront_proxies
from app.yandex.captcha_service import CaptchaError, Client
from app.yandex.storefront_prices import parse_prices, prepare
from app.yandex.storefront_schedule import TIMEZONE, next_run_at

LOG = logging.getLogger("yandex_storefront")
JOB = "yandex_storefront_prices_sync"
PROXY_NETWORK_ERRORS = {
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_INVALID_AUTH_CREDENTIALS",
    "ERR_PROXY_AUTH_REQUESTED",
    "ERR_UNEXPECTED_PROXY_AUTH",
    "ERR_NO_SUPPORTED_PROXIES",
    "ERR_PROXY_CERTIFICATE_INVALID",
}
RETRYABLE_NETWORK_ERRORS = PROXY_NETWORK_ERRORS | {
    "ERR_ABORTED",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_REFUSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_ADDRESS_UNREACHABLE",
    "ERR_TIMED_OUT",
    "ERR_EMPTY_RESPONSE",
    "ERR_HTTP2_PROTOCOL_ERROR",
}


def wait_for_next_run():
    while True:
        after = datetime.now(UTC)
        if pause := repository.cooldown(after):
            after = datetime.fromisoformat(pause["retry_after"]) - timedelta(microseconds=1)
        run_at = next_run_at(after)
        delay = (run_at - datetime.now(UTC)).total_seconds()
        set_next_run(JOB, delay)
        LOG.info("Следующий обход: %s (МСК)", run_at.astimezone(TIMEZONE).strftime("%d.%m.%Y %H:%M"))
        while (delay := (run_at - datetime.now(UTC)).total_seconds()) > 0:
            time.sleep(min(delay, 60))
        if (datetime.now(UTC) - run_at).total_seconds() <= 60:
            return
        # A sleeping computer must not start a missed daytime check at night.
        LOG.info("Время запуска пропущено; ожидаю следующую проверку по расписанию")


@contextmanager
def profile_lock(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    with (path / "worker.lock").open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Другой сборщик уже использует этот профиль") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class AccessBlocked(CaptchaError):
    def __init__(self, status: int, retry_after: str | None = None):
        self.server_retry_after = None
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    delay = 0
            if 0 < delay < float("inf"):
                try:
                    deadline = datetime.now(UTC) + timedelta(seconds=delay)
                except OverflowError:
                    pass
                else:
                    self.server_retry_after = deadline.isoformat()
        message = (
            "Яндекс временно запретил доступ к витрине (HTTP 403). При блокировке IP обратитесь в поддержку Яндекса"
            if status == 403
            else "Яндекс ограничил частоту обращений к витрине (HTTP 429)"
        )
        super().__init__(message)


class Browser:
    def __init__(self, page, args):
        self.page, self.args = page, args
        self.client = None

    def captcha(self) -> bool:
        return "/showcaptcha" in self.page.url or self.page.title().strip() == "Вы не робот?"

    @staticmethod
    def network_error(error: Exception) -> dict:
        codes = re.findall(r"\bERR_[A-Z_]+\b", str(error))
        code = codes[0] if codes else None
        return {
            "status": "network_error",
            "message": f"{type(error).__name__}: {code}" if code else type(error).__name__,
            "error_code": code,
        }

    def diagnostics(self):
        # Kept in ignored local storage; may contain session data.
        (self.args.state_dir / "last-page.html").write_text(self.page.content(), encoding="utf-8")
        self.page.screenshot(path=str(self.args.state_dir / "last-page.png"))
        return {
            "title": self.page.title(),
            "path": urlsplit(self.page.url).path,
            "inputs": self.page.locator("input").evaluate_all(
                "els=>els.slice(0,20).map(e=>({name:e.name,type:e.type,id:e.id}))"
            ),
            "images": self.page.locator("img").evaluate_all(
                "els=>els.slice(0,20).map(e=>({class:e.className,width:e.naturalWidth,height:e.naturalHeight}))"
            ),
        }

    def wait_for_captcha_challenge(self) -> bool:
        """Wait for the checkbox redirect or a fully loaded supported challenge."""
        for _ in range(80):
            if not self.captcha():
                return False
            main = self.page.locator(".AdvancedCaptcha-ImageWrapper img, img.AdvancedCaptcha-Image").first
            instruction = self.page.locator(".AdvancedCaptcha-SilhouetteTask .TaskImage").first
            text_input = self.page.locator('input[name="rep"]:not([type="hidden"])')
            if (
                main.count()
                and main.is_visible()
                and main.evaluate("image => image.complete && image.naturalWidth > 0")
                and (
                    (instruction.count() and instruction.is_visible())
                    or (text_input.count() and text_input.is_visible())
                )
            ):
                return True
            self.page.wait_for_timeout(250)
        return self.captcha()

    def solve_captcha(self):
        for _ in range(2):
            if not self.captcha():
                return
            checkbox = self.page.locator("#js-button")
            if checkbox.count():
                checkbox.click(timeout=10000)
            if not self.wait_for_captcha_challenge():
                LOG.info("Проверка завершена без платного задания; продолжаю обход")
                return
            if self.args.inspect:
                raise CaptchaError("Диагностика капчи: платные задачи не отправлялись")
            # Yandex's own /showcaptcha uses an image challenge, not a third-party sitekey.
            main = self.page.locator(".AdvancedCaptcha-ImageWrapper img, img.AdvancedCaptcha-Image").first
            instruction = self.page.locator(".AdvancedCaptcha-SilhouetteTask .TaskImage").first
            text_input = self.page.locator('input[name="rep"]:not([type="hidden"])')
            if main.count() and main.is_visible() and instruction.count() and instruction.is_visible():
                if not self.client:
                    self.client = Client(max_tasks=self.args.max_captchas)
                # The visible instruction is painted on canvas; its underlying img may be hidden.
                # Preserve the canvas resolution (480 px), rather than a tiny CSS thumbnail.
                canvas = instruction.locator("canvas")
                instruction_image = None
                if canvas.count():
                    try:
                        instruction_image = canvas.evaluate(
                            "element => element.toDataURL('image/png').split(',')[1]"
                        )
                    except Exception:
                        pass
                instruction_image = instruction_image or base64.b64encode(instruction.screenshot()).decode()
                LOG.info("Отправляю изображение и порядок фигур в 2Captcha")
                solution = self.client.solve(
                    {
                        "type": "SmartCaptchaTask",
                        "image": base64.b64encode(main.screenshot()).decode(),
                        "imgInstructions": instruction_image,
                        "comment": "select objects in the order of the instruction",
                    }
                )
                box = main.bounding_box()
                if not box:
                    raise CaptchaError("Изображение капчи исчезло")
                points = solution.get("coordinates") or []
                if not points:
                    raise CaptchaError("2Captcha не вернула координаты ответа")
                for point in points:
                    x, y = float(point["x"]), float(point["y"])
                    if not (0 <= x < box["width"] and 0 <= y < box["height"]):
                        raise CaptchaError("2Captcha вернула координаты вне изображения")
                    self.page.mouse.click(box["x"] + x, box["y"] + y)
            elif main.count() and main.is_visible() and text_input.count() and text_input.is_visible():
                if not self.client:
                    self.client = Client(max_tasks=self.args.max_captchas)
                solution = self.client.solve(
                    {"type": "ImageToTextTask", "body": base64.b64encode(main.screenshot()).decode()}
                )
                text_input.fill(str(solution["text"]))
            else:
                if not self.captcha():
                    return
                raise CaptchaError(
                    "Неизвестный вариант капчи; сохранена диагностика, платная задача не создана"
                )
            button = self.page.get_by_role("button", name="Отправить", exact=True)
            if button.count():
                button.click(timeout=10000)
            else:
                raise CaptchaError("Не найдена кнопка отправки ответа")
            self.page.wait_for_timeout(2500)
            if not self.captcha():
                LOG.info("Капча пройдена; продолжаю тот же обход")
                return
        raise CaptchaError("Маркет не принял решение капчи после двух попыток")

    def fetch(self, target: dict) -> dict:
        navigation_timed_out = False

        def navigate():
            nonlocal navigation_timed_out
            try:
                return self.page.goto(target["url"], wait_until="domcontentloaded", timeout=40000)
            except Exception as error:
                if type(error).__name__ != "TimeoutError":
                    raise
                # Some cards render before slow advertising/navigation requests finish.
                navigation_timed_out = True
                return None

        try:
            response = navigate()
        except Exception as error:
            return self.network_error(error)
        if response and response.status in (403, 429):
            raise AccessBlocked(response.status, response.headers.get("retry-after"))
        if self.captcha():
            try:
                self.solve_captcha()
            except CaptchaError:
                raise
            except Exception as error:
                if type(error).__name__ != "TimeoutError" or self.captcha():
                    location = traceback.extract_tb(error.__traceback__)[-1]
                    LOG.error(
                        "Ошибка при обработке капчи: %s (%s:%s)",
                        type(error).__name__,
                        location.name,
                        location.lineno,
                    )
                    raise CaptchaError("Не удалось применить ответ капчи: " + type(error).__name__) from None
            # Always revisit the intended offer after a challenge redirect.
            try:
                response = navigate()
            except Exception as error:
                return self.network_error(error)
            if response and response.status in (403, 429):
                raise AccessBlocked(response.status, response.headers.get("retry-after"))
            if self.captcha():
                raise CaptchaError("Маркет снова запросил капчу сразу после решения")
        if response and response.status in (403, 429):
            raise AccessBlocked(response.status, response.headers.get("retry-after"))
        if response and response.status >= 400:
            return {"status": "http_error", "message": f"HTTP {response.status}"}
        result = {}
        for _ in range(12):
            result = parse_prices(self.page.content(), self.page.url, target)
            if result["status"] not in {"price_missing", "wrong_page"}:
                break
            self.page.wait_for_timeout(250)
        if navigation_timed_out and result["status"] in {"price_missing", "wrong_page"}:
            return {"status": "network_error", "error_code": "ERR_TIMED_OUT", "message": "ERR_TIMED_OUT"}
        return result


class StoreBrowsers:
    """One browser at a time; unavailable proxies fall back to the host's direct connection."""

    def __init__(self, args, proxies, launch):
        self.args, self.proxies, self.launch = args, proxies, launch
        self.browser = self.context = self.current_route = None
        self.client = None
        self.failed = {}
        self.page_listener = None

    def close(self):
        if self.context:
            context, self.context = self.context, None
            self.browser = None
            self.page_listener = None
            context.close()

    def new_page(self):
        if self.page_listener:
            self.context.remove_listener("page", self.page_listener)
        page = self.context.new_page()
        for old in self.context.pages:
            if old != page:
                old.close()
        self.page_listener = lambda popup: popup.close() if popup != page else None
        self.context.on("page", self.page_listener)
        return page

    def fetch(self, target):
        slug = target["store_slug"]
        proxy = self.proxies.get(slug) if self.proxies is not None and slug not in self.failed else None
        if self.proxies is not None and proxy is None:
            self.failed.setdefault(slug, "Прокси не настроен")
        route = slug if proxy else "direct"
        if route != self.current_route or self.browser is None:
            self.close()
            browser_args = copy.copy(self.args)
            if proxy:
                browser_args.state_dir = self.args.state_dir / "stores" / slug
            browser_args.state_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.context = self.launch(browser_args, proxy)
                page = self.new_page()
                self.browser = Browser(page, browser_args)
                self.current_route = route
                LOG.info("%s: подключение %s", slug, "через прокси магазина" if proxy else "напрямую")
            except Exception:
                if not proxy:
                    raise
                return self.fetch_direct(target, "Не удалось запустить браузер с прокси")
        self.browser.client = self.client
        try:
            result = self.browser.fetch(target)
            if proxy and (
                result.get("error_code") in RETRYABLE_NETWORK_ERRORS or result.get("message") == "HTTP 407"
            ):
                LOG.info(
                    "%s:%s: %s; один повтор в новой вкладке через тот же прокси",
                    slug,
                    target["article"],
                    result.get("error_code") or "HTTP 407",
                )
                try:
                    self.browser.page = self.new_page()
                except Exception as error:
                    result = Browser.network_error(error)
                else:
                    result = self.browser.fetch(target)
        finally:
            self.client = self.browser.client
        if proxy and (
            result.get("error_code") in RETRYABLE_NETWORK_ERRORS or result.get("message") == "HTTP 407"
        ):
            code = result.get("error_code") or "HTTP 407"
            return self.fetch_direct(
                target, f"{code}; соединение через прокси не восстановилось после повтора"
            )
        return result

    def fetch_direct(self, target, reason):
        slug = target["store_slug"]
        self.failed[slug] = reason
        LOG.warning("%s: %s; продолжаем напрямую с IP сервера", slug, reason)
        self.close()
        return self.fetch(target)

    def diagnostics(self):
        return self.browser.diagnostics() if self.browser else {}


def selection(args):
    enabled = (
        tuple(sync_settings.enabled_stores(JOB, "YANDEX MARKET"))
        if args.loop
        else tuple(args.store)
        if args.store
        else None
    )
    selected = yandex_assortment.storefront_products(enabled)
    if args.article:
        wanted = set()
        for value in args.article:
            slug, separator, article = value.partition(":")
            if not separator or slug not in STORES or not article.strip():
                raise ValueError(
                    "--article должен иметь вид магазин:артикул с известным магазином и непустым артикулом"
                )
            wanted.add((slug, article))
        # A product can lose its stock/activity between the button click and this read.
        # Such products are skipped, without cancelling all other selected products.
        selected &= wanted
    if args.retry_failed:
        prices = {slug: repository.get_prices(slug) for slug, _ in selected}
        selected = {
            (slug, article)
            for slug, article in selected
            if prices[slug].get(article, {}).get("status") not in {"ok", "out_of_stock"}
        }
    return selected


def run_once(browser: Browser | StoreBrowsers | None, args) -> dict:
    selected = selection(args)
    run_id = uuid.uuid4().hex
    if not repository.acquire(run_id):
        return {"ok": True, "status": "already_running"}
    stopped = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not stopped.wait(30):
            try:
                if not repository.acquire(run_id):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    keeper = threading.Thread(target=heartbeat, daemon=True)
    keeper.start()
    report = {
        "selected": len(selected),
        "checked": 0,
        "statuses": {},
        "skipped": [],
        "ok": False,
        "full_scan": not any((args.article, args.limit, args.retry_failed, args.prepare_only, args.inspect)),
        "captcha_tasks": 0,
        "captcha_cost": "0",
    }
    try:
        repository.start_run(run_id)
        if not args.prepare_only and (pause := repository.cooldown()):
            report.update(pause)
            return report
        targets, skipped = prepare(selected)
        report.update(prepared=len(targets), skipped=skipped)
        LOG.info(
            "Выбрано %s, найдены карточки %s, пропущено %s", report["selected"], len(targets), len(skipped)
        )
        for item in skipped:
            LOG.warning("%s:%s — %s", item["store_slug"], item["article"], item["message"])
        if args.prepare_only:
            report.update(ok=not skipped, status="prepared")
            return report
        browser.client = None
        counts = Counter()
        for index, target in enumerate(targets[: args.limit or None], 1):
            if lost.is_set():
                raise RuntimeError("Потеряна блокировка загрузки")
            target = {**target, "_price_basis": repository.price_basis(target)}
            try:
                result = browser.fetch(target)
            except CaptchaError as error:
                result = {
                    "status": "access_blocked" if isinstance(error, AccessBlocked) else "captcha_blocked",
                    "message": str(error),
                }
                repository.record(target, result)
                counts[result["status"]] += 1
                report.update(checked=index, statuses=dict(counts), remaining=len(targets) - index)
                try:
                    report["diagnostics"] = browser.diagnostics()
                except Exception:
                    report["diagnostics_error"] = "Не удалось сохранить страницу"
                raise
            repository.record(target, result)
            if result["status"] == "proxy_error":
                report.setdefault("proxy_errors", {})[target["store_slug"]] = result["message"]
            counts[result["status"]] += 1
            report.update(checked=index, statuses=dict(counts))
            LOG.info(
                "%s/%s %s:%s %s buyer=%s",
                index,
                len(targets),
                target["store_slug"],
                target["article"],
                result["status"],
                result.get("buyer_price"),
            )
            if index < min(len(targets), args.limit or len(targets)):
                time.sleep(max(5, args.delay))
        missing = sum(n for status, n in counts.items() if status not in {"ok", "out_of_stock"})
        report.update(status="complete", ok=not skipped and not missing)
        if skipped or missing:
            report["message"] = (
                f"Обход завершён: пропусков {len(skipped)}, карточек без подтверждённой цены {missing}"
            )
            if report.get("proxy_errors"):
                report["message"] += ". " + "; ".join(report["proxy_errors"].values())
        if args.inspect:
            report["diagnostics"] = browser.diagnostics()
        return report
    except KeyboardInterrupt:
        report.update(status="interrupted", error="Сборщик остановлен")
        raise
    except Exception as error:
        report.update(
            status="blocked" if isinstance(error, CaptchaError) else "error",
            error=str(error)
            if isinstance(error, (CaptchaError, storefront_proxies.ProxyConfigError))
            else type(error).__name__,
        )
        if server_retry_after := getattr(error, "server_retry_after", None):
            deadline = datetime.fromisoformat(server_retry_after)
            report["server_retry_after"] = server_retry_after
            set_next_run(
                JOB, (next_run_at(deadline - timedelta(microseconds=1)) - datetime.now(UTC)).total_seconds()
            )
        LOG.error("Обход остановлен: %s", report["error"])
        if browser and "diagnostics" not in report:
            try:
                report["diagnostics"] = browser.diagnostics()
            except Exception:
                pass
        return report
    finally:
        if isinstance(browser, StoreBrowsers) and browser.failed:
            report["direct_fallbacks"] = dict(browser.failed)
        if browser and browser.client:
            report.update(captcha_tasks=browser.client.submitted, captcha_cost=str(browser.client.cost))
        try:
            repository.finish_run(run_id, report)
            path = args.state_dir / "latest.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            stopped.set()
            keeper.join(timeout=5)
            repository.release(run_id)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Совместимость: разовый запуск всегда проверяет все выбранные товары",
    )
    parser.add_argument("--loop", action="store_true", help="Ежедневно в 01:00 и 08:00–19:00 каждый час, МСК")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--store",
        action="append",
        choices=tuple(STORES),
        help="Ограничить разовый запуск выбранными магазинами",
    )
    parser.add_argument("--article", action="append")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=5, help="Пауза между карточками, минимум 5 секунд")
    parser.add_argument("--max-captchas", type=int, default=10)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="Сохранить диагностику; без платных решений")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Повторить только товары без цены и без подтверждённого отсутствия",
    )
    default_state = ROOT / "data/yandex-storefront" / ("live-check" if os.name == "nt" else "")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("CHECKSTOCK_YANDEX_STOREFRONT_STATE_DIR") or default_state),
    )
    args = parser.parse_args(argv)
    if args.limit < 0 or args.delay < 0 or not math.isfinite(args.delay) or args.max_captchas < 1:
        parser.error("Некорректные ограничения")
    if args.loop and (
        args.prepare_only or args.inspect or args.article or args.store or args.limit or args.retry_failed
    ):
        parser.error(
            "Автоматический режим обходит весь включённый список; фильтры и диагностика доступны однократно"
        )
    return args


def run_browser_once(args) -> dict:
    """One shared browser configuration/profile for CLI, schedule and the web button."""
    with profile_lock(args.state_dir):
        if args.prepare_only or repository.cooldown():
            return run_once(None, args)
        try:
            proxies = storefront_proxies.load()
        except storefront_proxies.ProxyConfigError as error:
            LOG.warning("%s; продолжаем напрямую с IP сервера", error)
            proxies = None
        from playwright.sync_api import sync_playwright

        with ExitStack() as stack:
            browser_env = dict(os.environ)
            if sys.platform.startswith("linux") and not args.headless and not browser_env.get("DISPLAY"):
                from pyvirtualdisplay import Display

                display = stack.enter_context(
                    Display(visible=False, size=(1440, 1000), manage_global_env=False)
                )
                browser_env = display.env()
            playwright = stack.enter_context(sync_playwright())

            def launch(browser_args, proxy):
                options = {"proxy": proxy} if proxy else {}
                return playwright.chromium.launch_persistent_context(
                    str(browser_args.state_dir / "browser-profile"),
                    headless=args.headless,
                    channel="chrome" if os.name == "nt" else "chromium",
                    locale="ru-RU",
                    timezone_id="Europe/Moscow",
                    viewport={"width": 1440, "height": 1000},
                    accept_downloads=False,
                    chromium_sandbox=True,
                    timeout=30000,
                    env=browser_env,
                    **options,
                )

            browser = StoreBrowsers(args, proxies, launch)
            stack.callback(browser.close)
            return run_once(browser, args)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = arguments(argv)

    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    handlers = [logging.StreamHandler()] if sys.stderr is not None else []
    if args.loop:
        from logging.handlers import RotatingFileHandler

        handlers.append(
            RotatingFileHandler(
                args.state_dir / "worker.log", maxBytes=5_000_000, backupCount=2, encoding="utf-8"
            )
        )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    db.init_db()
    LOG.info("БД готова. Подготовка сборщика")
    while True:
        if args.loop:
            wait_for_next_run()
            if repository.cooldown():
                continue
        try:
            report = run_tracked(JOB, "scheduled" if args.loop else "manual", lambda: run_browser_once(args))
        except SyncJobBusyError:
            report = {"ok": True, "status": "already_running"}
        except Exception:
            if not args.loop:
                raise
            LOG.exception("Не удалось запустить браузер; повтор в следующий срок расписания")
            continue
        if not args.loop:
            break
    if sys.stdout is not None:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in ("complete", "prepared", "already_running") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        frames = traceback.extract_tb(error.__traceback__)
        locations = ", ".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames)
        detail = (
            str(error) if isinstance(error, storefront_proxies.ProxyConfigError) else type(error).__name__
        )
        LOG.error("Сборщик завершился: %s (%s)", detail, locations)
        raise SystemExit(1) from None
