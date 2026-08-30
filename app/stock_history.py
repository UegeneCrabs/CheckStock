from datetime import UTC, date, datetime, timedelta

from app import db
from app.domain import MOSCOW_TIMEZONE
from app.ozon import sync as ozon_sync
from app.wb import sync as wb_sync
from app.yandex import sync as yandex_sync


def _captured_at(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def sync_wb_and_save_daily_history(
    snapshot_day: date | None = None,
    captured_at: datetime | None = None,
    store_slugs: tuple[str, ...] | None = None,
) -> dict:
    report = wb_sync.sync_all(store_slugs)
    day = snapshot_day or datetime.now(MOSCOW_TIMEZONE).date()
    timestamp = _captured_at(captured_at)
    saved: dict[str, dict[str, int]] = {}

    for store_slug, store_report in report.items():
        if not isinstance(store_report, dict):
            continue
        for scheme in ("fbs", "fbo"):
            scope = store_report.get(scheme)
            if not isinstance(scope, dict) or scope.get("ok") is not True:
                continue
            saved.setdefault(store_slug, {})[scheme] = db.replace_marketplace_stock_daily_history(
                store_slug,
                "WB",
                scheme,
                day.isoformat(),
                timestamp,
            )

    return {"day": day.isoformat(), "sync": report, "saved": saved}


def sync_marketplaces_and_save_daily_history(
    snapshot_day: date | None = None,
    captured_at: datetime | None = None,
    target_stores: dict[str, tuple[str, ...]] | None = None,
) -> dict:
    """Refresh and save normalized FBS/FBO boundaries for every marketplace."""

    day = snapshot_day or datetime.now(MOSCOW_TIMEZONE).date()
    timestamp = _captured_at(captured_at)
    stores_by_marketplace = target_stores or {}
    wb_stores = stores_by_marketplace.get("WB", ()) if target_stores is not None else None
    wb_result = sync_wb_and_save_daily_history(day, captured_at, wb_stores)
    reports = {"WB": wb_result["sync"]}
    saved: dict[str, dict[str, dict[str, int]]] = {"WB": wb_result["saved"]}

    configurations = (
        ("OZON", "ozon", ozon_sync.sync_all),
        ("YANDEX MARKET", "yandex", yandex_sync.sync_all),
    )
    for marketplace, result_key, sync in configurations:
        selected_stores = (
            stores_by_marketplace.get(marketplace, ()) if target_stores is not None else None
        )
        report = sync(selected_stores)
        reports[marketplace] = report
        marketplace_saved: dict[str, dict[str, int]] = {}
        for store_slug, store_report in report.items():
            scope = store_report.get(result_key) if isinstance(store_report, dict) else None
            if not isinstance(scope, dict) or scope.get("ok") is not True:
                continue
            marketplace_saved[store_slug] = {
                scheme: db.replace_marketplace_stock_daily_history(
                    store_slug,
                    marketplace,
                    scheme,
                    day.isoformat(),
                    timestamp,
                )
                for scheme in ("fbs", "fbo")
            }
        saved[marketplace] = marketplace_saved

    return {"day": day.isoformat(), "sync": reports, "saved": saved}


def save_previous_day_fulfillment_history(now: datetime | None = None) -> dict:
    current = now or datetime.now(MOSCOW_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW_TIMEZONE)
    moscow_now = current.astimezone(MOSCOW_TIMEZONE)
    snapshot_day = moscow_now.date() - timedelta(days=1)
    saved = db.replace_fulfillment_stock_daily_history(
        snapshot_day.isoformat(),
        _captured_at(current),
    )
    return {"day": snapshot_day.isoformat(), "saved": saved}
