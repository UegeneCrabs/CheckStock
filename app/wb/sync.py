import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)


_DB_LOCK = db.WRITE_LOCK


EXCLUDED_FBO_WAREHOUSES = {
    "Электросталь",
    "Невинномысск",
    "Краснодар",
    "СЦ Шушары",
    "Склад СПБ Шушары Московское",
    "Санкт-Петербург Уткина Заводь",
    "Рязань (Тюшевское)",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _store_label(store_slug: str) -> str:

    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _error_message(e: Exception) -> str:

    if isinstance(e, wb_api.WBApiError):
        return e.friendly
    return f"непредвиденная ошибка ({type(e).__name__}): {e}"


def _normalize_ff_name(name: str) -> str:

    return " ".join((name or "").strip().casefold().split())


WB_WAREHOUSE_FF_ALIASES = {
    "gogol": {
        "ФуллСервис Подольск": "ФулСервис Подольск",
        "ФФ GO Екатерибург": "ФФ GO Екатеринбург",
    },
}


def _known_ff_by_warehouse(store_slug: str) -> dict[str, str]:
    known = {_normalize_ff_name(name): name for name in db.get_fulfillments()}
    for warehouse_name, fulfillment_name in WB_WAREHOUSE_FF_ALIASES.get(store_slug, {}).items():
        canonical = known.get(_normalize_ff_name(fulfillment_name))
        if canonical:
            known[_normalize_ff_name(warehouse_name)] = canonical
        else:
            logger.warning(
                "WB %s: алиас склада %s указывает на неизвестный ФФ %s",
                store_slug,
                warehouse_name,
                fulfillment_name,
            )
    return known


def sync_store_fbs(store_slug: str) -> int:

    token = wb_tokens.get_token(store_slug)
    catalog = db.get_catalog_items(store_slug)
    if not catalog:
        return 0

    warehouses = wb_api.get_own_warehouses(token)
    if not warehouses:
        raise wb_api.WBApiError(None, detail="в личном кабинете WB не настроен ни один склад FBS")

    known_by_norm = _known_ff_by_warehouse(store_slug)
    barcodes = [item["barcode"] for item in catalog]

    stock_by_warehouse: dict[int, dict[str, int]] = {}
    warehouse_errors: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(warehouses))) as executor:
        future_to_wh = {
            executor.submit(wb_api.get_fbs_stock, token, wh["id"], barcodes): wh for wh in warehouses
        }
        for future in as_completed(future_to_wh):
            wh = future_to_wh[future]
            try:
                stock_by_warehouse[wh["id"]] = future.result()
            except Exception as e:
                warehouse_errors[wh["id"]] = _error_message(e)
                logger.warning(
                    "FBS: склад %s (%s) магазина %s не ответил: %s",
                    wh["id"],
                    wh.get("name"),
                    store_slug,
                    warehouse_errors[wh["id"]],
                )

    if warehouse_errors and len(warehouse_errors) == len(warehouses):
        first_err = next(iter(warehouse_errors.values()))
        raise wb_api.WBApiError(
            None, detail=f"не удалось получить остатки FBS ни по одному складу продавца: {first_err}"
        )

    now = _now()

    ff_label_by_warehouse: dict[int, str] = {}
    for wh in warehouses:
        norm = _normalize_ff_name(wh["name"])
        ff_label_by_warehouse[wh["id"]] = known_by_norm.get(norm, wh["name"])

    totals: dict[str, int] = {item["article"]: 0 for item in catalog}
    ff_quantities: dict[tuple[str, str], int] = {}
    seen_labels: dict[str, int] = {}
    ff_map_entries: list[tuple[str, int, str, str]] = []

    for wh in warehouses:
        label = ff_label_by_warehouse[wh["id"]]
        if label not in seen_labels:
            seen_labels[label] = wh["id"]
            ff_map_entries.append((label, wh["id"], wh["name"], now))

        stock_by_barcode = stock_by_warehouse.get(wh["id"], {})
        for item in catalog:
            qty = stock_by_barcode.get(item["barcode"], 0)
            totals[item["article"]] += qty
            key = (item["article"], label)
            ff_quantities[key] = ff_quantities.get(key, 0) + qty

    ff_stock_entries = [(article, label, qty, now) for (article, label), qty in ff_quantities.items()]

    with _DB_LOCK:
        db.replace_ff_warehouse_map(store_slug, ff_map_entries)
        db.replace_mp_warehouse_stock(
            store_slug,
            "WB",
            "fbs",
            [
                (article, fulfillment, None, quantity, updated_at)
                for article, fulfillment, quantity, updated_at in ff_stock_entries
            ],
        )
        for item in catalog:
            db.upsert_mp_stock(store_slug, item["article"], "WB", "fbs", totals[item["article"]], now)

    return len(catalog)


def sync_store_fbo(store_slug: str) -> int:
    token = wb_tokens.get_token(store_slug)
    catalog = db.get_catalog_items(store_slug)
    if not catalog:
        return 0

    by_warehouse = wb_api.get_fbo_stock_by_warehouse(token)

    barcode_to_warehouses: dict[str, list[tuple[str, int]]] = {}
    for (barcode, warehouse), quantity in by_warehouse.items():
        barcode_to_warehouses.setdefault(barcode, []).append((warehouse, quantity))

    now = _now()
    warehouse_entries: list[tuple[str, str, int, str]] = []
    totals: list[tuple[str, int]] = []
    updated = 0

    for item in catalog:
        entries = barcode_to_warehouses.get(item["barcode"], [])

        sellable_entries = [(wh, qty) for wh, qty in entries if wh not in EXCLUDED_FBO_WAREHOUSES]

        total = sum(qty for _, qty in sellable_entries)
        totals.append((item["article"], total))

        for warehouse, qty in sellable_entries:
            warehouse_entries.append((item["article"], warehouse, qty, now))

        updated += 1

    with _DB_LOCK:
        for article, total in totals:
            db.upsert_mp_stock(store_slug, article, "WB", "fbo", total, now)
        db.replace_mp_warehouse_stock(
            store_slug,
            "WB",
            "fbo",
            [
                (article, warehouse, None, quantity, updated_at)
                for article, warehouse, quantity, updated_at in warehouse_entries
            ],
        )
    return updated


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict:

    report: dict = {}
    active_slugs = []
    targets = tuple(STORES) if store_slugs is None else store_slugs

    for slug in targets:
        if not wb_tokens.has_token(slug):
            report[slug] = {"token": False, "fbs": None, "fbo": None}
        else:
            report[slug] = {"token": True, "fbs": None, "fbo": None}
            active_slugs.append(slug)

    if not active_slugs:
        return report

    jobs = {
        "fbs": sync_store_fbs,
        "fbo": sync_store_fbo,
    }

    with ThreadPoolExecutor(max_workers=max(2, len(active_slugs) * 2)) as executor:
        future_to_task = {
            executor.submit(func, slug): (slug, kind) for slug in active_slugs for kind, func in jobs.items()
        }

        for future in as_completed(future_to_task):
            slug, kind = future_to_task[future]
            try:
                count = future.result()
                report[slug][kind] = {"ok": True, "count": count}
                db.record_sync_health(slug, "WB", kind, True, None, _now())
            except Exception as e:
                logger.exception(
                    "WB %s / %s: %s",
                    _store_label(slug),
                    kind.upper(),
                    _error_message(e),
                )
                report[slug][kind] = {"ok": False, "error": _error_message(e)}
                db.record_sync_health(slug, "WB", kind, False, _error_message(e), _now())

    return report
