"""Цены ЯМ в БД. --loop: ежедневно в 01:00 и каждый час 08:00–19:00 по Екатеринбургу."""

import argparse
import base64
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, sync_settings
from app.repositories import yandex_assortment
from app.repositories import yandex_storefront as repository
from app.sync_tracking import run_tracked, set_next_run
from app.yandex.captcha_service import CaptchaError, Client
from app.yandex.storefront_prices import parse_prices, prepare
from app.yandex.storefront_schedule import TIMEZONE, next_run_at

LOG = logging.getLogger("yandex_storefront")
JOB = "yandex_storefront_prices_sync"


def wait_for_next_run(page):
    while True:
        run_at = next_run_at(datetime.now(UTC))
        delay = (run_at - datetime.now(UTC)).total_seconds()
        set_next_run(JOB, delay)
        LOG.info("Следующий обход: %s (Екатеринбург)", run_at.astimezone(TIMEZONE).strftime("%d.%m.%Y %H:%M"))
        while (delay := (run_at - datetime.now(UTC)).total_seconds()) > 0:
            page.wait_for_timeout(min(delay, 60) * 1000)
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


class Browser:
    def __init__(self, page, args):
        self.page, self.args = page, args
        self.client = None

    def captcha(self) -> bool:
        return "/showcaptcha" in self.page.url or self.page.title().strip() == "Вы не робот?"

    def diagnostics(self):
        # Kept in ignored local storage; may contain session data.
        (self.args.state_dir / "last-page.html").write_text(self.page.content(), encoding="utf-8")
        self.page.screenshot(path=str(self.args.state_dir / "last-page.png"))
        return {
            "title": self.page.title(),
            "path": urlsplit(self.page.url).path,
            "inputs": self.page.locator("input").evaluate_all(
                "els=>els.slice(0,20).map(e=>({name:e.name,type:e.type,id:e.id}))"),
            "images": self.page.locator("img").evaluate_all(
                "els=>els.slice(0,20).map(e=>({class:e.className,width:e.naturalWidth,height:e.naturalHeight}))"),
        }

    def solve_captcha(self):
        for _ in range(2):
            if not self.captcha():
                return
            checkbox = self.page.locator("#js-button")
            if checkbox.count():
                checkbox.click(timeout=10000)
                for _ in range(40):
                    self.page.wait_for_timeout(500)
                    if not self.captcha() or not self.page.locator("#js-button").count():
                        break
                if not self.captcha():
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
                        instruction_image = canvas.evaluate("element => element.toDataURL('image/png').split(',')[1]")
                    except Exception:
                        pass
                instruction_image = instruction_image or base64.b64encode(instruction.screenshot()).decode()
                LOG.info("Отправляю изображение и порядок фигур в 2Captcha")
                solution = self.client.solve({
                    "type": "SmartCaptchaTask",
                    "image": base64.b64encode(main.screenshot()).decode(),
                    "imgInstructions": instruction_image,
                    "comment": "select objects in the order of the instruction",
                })
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
                solution = self.client.solve({"type": "ImageToTextTask", "body": base64.b64encode(main.screenshot()).decode()})
                text_input.fill(str(solution["text"]))
            else:
                raise CaptchaError("Неизвестный вариант капчи; сохранена диагностика, платная задача не создана")
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
        def navigate():
            try:
                return self.page.goto(target["url"], wait_until="domcontentloaded", timeout=40000)
            except Exception as error:
                if type(error).__name__ != "TimeoutError":
                    raise
                # Some cards render before slow advertising/navigation requests finish.
                return None

        try:
            response = navigate()
        except Exception as error:
            return {"status": "network_error", "message": type(error).__name__}
        if self.captcha():
            try:
                self.solve_captcha()
            except CaptchaError:
                raise
            except Exception as error:
                if type(error).__name__ != "TimeoutError" or self.captcha():
                    location = traceback.extract_tb(error.__traceback__)[-1]
                    LOG.error("Ошибка при обработке капчи: %s (%s:%s)", type(error).__name__, location.name, location.lineno)
                    raise CaptchaError("Не удалось применить ответ капчи: " + type(error).__name__) from None
            # Always revisit the intended offer after a challenge redirect.
            try:
                response = navigate()
            except Exception as error:
                return {"status": "network_error", "message": type(error).__name__}
            if self.captcha():
                raise CaptchaError("Маркет снова запросил капчу сразу после решения")
        if response and response.status in (403, 429):
            raise CaptchaError(f"Маркет ограничил доступ: HTTP {response.status}")
        if response and response.status >= 400:
            return {"status": "http_error", "message": f"HTTP {response.status}"}
        result = {}
        for _ in range(12):
            result = parse_prices(self.page.content(), self.page.url, target)
            if result["status"] not in {"price_missing", "wrong_page"}:
                break
            self.page.wait_for_timeout(250)
        return result


def selection(args):
    selected = yandex_assortment.load_active_products()
    if args.loop:
        enabled = set(sync_settings.enabled_stores(JOB, "YANDEX MARKET"))
        selected = {(slug, article) for slug, article in selected if slug in enabled}
    if args.article:
        wanted = set()
        for value in args.article:
            slug, separator, article = value.partition(":")
            if not separator or (slug, article) not in selected:
                raise ValueError("--article должен иметь вид магазин:артикул и входить в актуальный список")
            wanted.add((slug, article))
        selected = wanted
    if args.retry_failed:
        prices = {slug: repository.get_prices(slug) for slug, _ in selected}
        selected = {(slug, article) for slug, article in selected
                    if prices[slug].get(article, {}).get("status") not in {"ok", "out_of_stock"}}
    return selected


def run_once(browser: Browser | None, args) -> dict:
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
    report = {"selected": len(selected), "checked": 0, "statuses": {}, "skipped": [], "ok": False,
              "full_scan": not any((args.article, args.limit, args.retry_failed, args.prepare_only, args.inspect)),
              "captcha_tasks": 0, "captcha_cost": "0"}
    try:
        repository.start_run(run_id)
        targets, skipped = prepare(selected)
        report.update(prepared=len(targets), skipped=skipped)
        LOG.info("Выбрано %s, найдены карточки %s, пропущено %s", report["selected"], len(targets), len(skipped))
        for item in skipped:
            LOG.warning("%s:%s — %s", item["store_slug"], item["article"], item["message"])
        if args.prepare_only:
            report.update(ok=not skipped, status="prepared")
            return report
        browser.client = None
        counts = Counter()
        for index, target in enumerate(targets[:args.limit or None], 1):
            if lost.is_set():
                raise RuntimeError("Потеряна блокировка загрузки")
            try:
                result = browser.fetch(target)
            except CaptchaError as error:
                result = {"status": "captcha_blocked", "message": str(error)}
                repository.record(target, result)
                counts[result["status"]] += 1
                report.update(checked=index, statuses=dict(counts), remaining=len(targets) - index)
                try:
                    report["diagnostics"] = browser.diagnostics()
                except Exception:
                    report["diagnostics_error"] = "Не удалось сохранить страницу"
                raise
            repository.record(target, result)
            counts[result["status"]] += 1
            report.update(checked=index, statuses=dict(counts))
            LOG.info("%s/%s %s:%s %s buyer=%s", index, len(targets), target["store_slug"], target["article"],
                     result["status"], result.get("buyer_price"))
            time.sleep(args.delay)
        missing = sum(n for status, n in counts.items() if status not in {"ok", "out_of_stock"})
        report.update(status="complete", ok=not skipped and not missing)
        if skipped or missing:
            report["message"] = f"Обход завершён: пропусков {len(skipped)}, карточек без подтверждённой цены {missing}"
        if args.inspect:
            report["diagnostics"] = browser.diagnostics()
        return report
    except KeyboardInterrupt:
        report.update(status="interrupted", error="Сборщик остановлен")
        raise
    except Exception as error:
        report.update(status="blocked" if isinstance(error, CaptchaError) else "error",
                      error=str(error) if isinstance(error, CaptchaError) else type(error).__name__)
        LOG.error("Обход остановлен: %s", report["error"])
        if browser and "diagnostics" not in report:
            try:
                report["diagnostics"] = browser.diagnostics()
            except Exception:
                pass
        return report
    finally:
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
    parser.add_argument("--force", action="store_true", help="Совместимость: разовый запуск всегда проверяет все выбранные товары")
    parser.add_argument("--loop", action="store_true", help="Ежедневно в 01:00 и 08:00–19:00 каждый час, Екатеринбург")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--article", action="append")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=1)
    parser.add_argument("--max-captchas", type=int, default=10)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="Сохранить диагностику; без платных решений")
    parser.add_argument("--retry-failed", action="store_true", help="Повторить только товары без цены и без подтверждённого отсутствия")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "data/yandex-storefront")
    args = parser.parse_args(argv)
    if args.limit < 0 or args.delay < 0 or args.max_captchas < 1:
        parser.error("Некорректные ограничения")
    if args.loop and (args.prepare_only or args.inspect or args.article or args.limit or args.retry_failed):
        parser.error("Автоматический режим обходит весь включённый список; фильтры и диагностика доступны однократно")
    return args


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
        handlers.append(RotatingFileHandler(args.state_dir / "worker.log", maxBytes=5_000_000,
                                           backupCount=2, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    db.init_db()
    LOG.info("БД готова. Подготовка сборщика")
    with profile_lock(args.state_dir):
        if args.prepare_only:
            report = run_once(None, args)
        else:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                LOG.info("Запускаю Chrome/Chromium с сохранённым профилем")
                context = playwright.chromium.launch_persistent_context(
                    str(args.state_dir / "browser-profile"), headless=args.headless,
                    channel="chrome" if os.name == "nt" else "chromium",
                    locale="ru-RU", timezone_id="Europe/Moscow", viewport={"width": 1440, "height": 1000},
                    accept_downloads=False, chromium_sandbox=True, timeout=30000,
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    for extra in context.pages[1:]:
                        extra.close()
                    context.on("page", lambda popup: popup.close() if popup != page else None)
                    browser = Browser(page, args)
                    while True:
                        if args.loop:
                            wait_for_next_run(page)
                        report = run_tracked(JOB, "scheduled" if args.loop else "manual", lambda: run_once(browser, args))
                        if not args.loop:
                            break
                finally:
                    context.close()
    if sys.stdout is not None:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") in ("complete", "prepared", "already_running") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        frames = traceback.extract_tb(error.__traceback__)
        locations = ", ".join(f"{Path(frame.filename).name}:{frame.lineno}" for frame in frames)
        LOG.error("Сборщик завершился: %s (%s)", type(error).__name__, locations)
        raise SystemExit(1) from None
