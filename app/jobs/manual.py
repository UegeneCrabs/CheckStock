"""Manual entry points for the same loaders used by the application scheduler."""

import asyncio
import os
from collections.abc import Callable

from app.exports import ftp as ftp_export
from app.exports import ftp_schedule as ftp_export_schedule
from app.jobs import background
from app.jobs import locks as sync_locks
from app.jobs import settings as sync_settings
from app.jobs.tracking import set_next_run
from app.repositories import core, yandex_assortment


def is_running(name: str) -> bool:
    if sync_locks.is_running(name):
        return True
    platform = ftp_export.platform_for_job(name)
    if platform and ftp_export.is_running(platform):
        return True
    if name == "yandex_storefront_prices_sync":
        from app.repositories import yandex_storefront

        with core.get_connection() as connection:
            return bool(
                connection.execute(
                    "SELECT 1 FROM yandex_storefront_lease WHERE name='prices' AND expires_at>?",
                    (yandex_storefront.now(),),
                ).fetchone()
            )
    return False


def _sheets_now() -> dict:
    reports = {}
    for store in sync_settings.enabled_stores("stock_sheet_export"):
        try:
            reports[store] = {"ok": True, "report": background.stock_sheet_export.run_store(store)}
        except Exception as error:
            reports[store] = {"ok": False, "error": str(error)}
    return reports


def _marketplaces_now(name: str, loaders: tuple) -> dict:
    reports = {}
    for marketplace, loader in loaders:
        try:
            reports[marketplace] = loader(sync_settings.enabled_stores(name, marketplace))
        except Exception as error:
            reports[marketplace] = {"ok": False, "error": str(error)}
    return reports


def _storefront_now() -> dict:
    """Reuse the collector's parsing, API preparation, lease and persistence. A separate browser profile allows a one-off run while the scheduled browser is idle."""
    from playwright.sync_api import sync_playwright

    from scripts.parsers import parse_yandex_storefront_prices as collector

    stores = set(sync_settings.enabled_stores(collector.JOB, "YANDEX MARKET"))
    selected = sorted(
        (slug, article) for slug, article in yandex_assortment.load_active_products() if slug in stores
    )
    if not selected:
        return {"ok": False, "error": "В выбранных магазинах нет товаров для загрузки цен"}
    arguments = ["--headless", "--state-dir", str(collector.ROOT / "data/yandex-storefront/manual")]
    for slug, article in selected:
        arguments.extend(["--article", f"{slug}:{article}"])
    args = collector.arguments(arguments)
    with collector.profile_lock(args.state_dir), sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.state_dir / "browser-profile"),
            headless=True,
            channel="chrome" if os.name == "nt" else "chromium",
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 1000},
            accept_downloads=False,
            chromium_sandbox=True,
            timeout=30000,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            for extra in context.pages[1:]:
                extra.close()
            context.on("page", lambda popup: popup.close() if popup != page else None)
            report = collector.run_once(collector.Browser(page, args), args)
            if report.get("status") == "already_running":
                raise sync_locks.SyncJobBusyError()
            return report
        finally:
            context.close()


def callback_for(name: str) -> Callable[[], object]:
    """These loaders return per-store errors; keep them visible in the common run log."""
    jobs = {
        job.name: job
        for job in (*background._jobs(asyncio.Event()), *background._unit_economics_1c_price_jobs())
    }
    overrides = {
        "catalog_sync": lambda: _marketplaces_now(
            "catalog_sync",
            (
                ("WB", background.wb_catalog.sync_all),
                ("OZON", background.ozon_catalog.sync_all),
                ("YANDEX MARKET", background.ya_catalog.sync_all),
            ),
        ),
        "stock_sync": lambda: _marketplaces_now(
            "stock_sync",
            (
                ("WB", background.wb_sync.sync_all),
                ("OZON", background.ozon_sync.sync_all),
                ("YANDEX MARKET", background.ya_sync.sync_all),
            ),
        ),
        "stock_sheet_export": _sheets_now,
        "unit_economics_1c_sync": lambda: background.unit_economics_1c.price_sync.sync_stores(
            sync_settings.enabled_stores("unit_economics_1c_sync")
        ),
        "unit_economics_1c_reference_sync": lambda: background.unit_reference_sync.sync_all(force=True),
        "wb_token_check": lambda: background.token_watch.refresh_token_info(
            sync_settings.enabled_stores("wb_token_check")
        ),
        "yandex_storefront_prices_sync": _storefront_now,
        "wb_advertising_sync": lambda: background.advertising_sync.sync_stores(
            sync_settings.enabled_stores("wb_advertising_sync")
        ),
    }
    if name in overrides:
        callback = overrides[name]
    elif name in jobs:
        callback = jobs[name].run_callback or jobs[name].callback
    else:
        raise ValueError("Ручной запуск этой выгрузки недоступен")

    def run():
        with sync_settings.manual_run(name):
            try:
                return callback()
            finally:
                if ftp_export.platform_for_job(name):
                    set_next_run(name, ftp_export_schedule.next_delay_seconds(name))

    return run
