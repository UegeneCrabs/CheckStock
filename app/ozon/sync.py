import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from app import db
from app.core.stores import STORES
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.repositories.stock_snapshot import replace_snapshot
from app.stock.fulfillment_names import fulfillment_lookup, normalize

logger = logging.getLogger(__name__)

MARKETPLACE = "OZON"
_DB_LOCK = db.WRITE_LOCK


SCHEMES = ("fbo", "fbs", "rfbs")


def _store_label(store_slug: str) -> str:

    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _error_message(e: Exception) -> str:
    if isinstance(e, ozon_api.OzonApiError):
        return e.friendly
    return f"непредвиденная ошибка ({type(e).__name__}): {e}"


def _totals_by_scheme(items: list[dict], known_articles: set[str]) -> dict[str, dict[str, int]]:

    totals: dict[str, dict[str, int]] = {scheme: {} for scheme in SCHEMES}

    for item in items:
        article = str(item.get("offer_id") or "").strip()
        if not article or article not in known_articles:
            continue

        for stock in item.get("stocks") or []:
            scheme = str(stock.get("type") or "").lower()
            if scheme not in totals:
                continue
            try:
                present = int(stock.get("present") or 0)
            except (TypeError, ValueError):
                continue
            totals[scheme][article] = totals[scheme].get(article, 0) + present

    return totals


def _fbo_skus_by_article(items: list[dict], known_articles: set[str]) -> dict[str, set[str]]:

    result: dict[str, set[str]] = {}
    for item in items:
        article = str(item.get("offer_id") or "").strip()
        if not article or article not in known_articles:
            continue

        for stock in item.get("stocks") or []:
            if str(stock.get("type") or "").lower() != "fbo":
                continue
            sku = str(stock.get("sku") or "").strip()
            if sku:
                result.setdefault(article, set()).add(sku)

    return result


def _cluster_by_warehouse() -> dict[str, str]:

    return db.get_warehouse_clusters(MARKETPLACE)


def sync_store(store_slug: str) -> int:
    ozon_api.set_store_context(_store_label(store_slug))
    try:
        return _sync_store(store_slug)
    finally:
        ozon_api.clear_store_context()


def _sync_store(store_slug: str) -> int:
    client_id, api_key = ozon_tokens.get_credentials(store_slug)

    catalog = db.get_catalog_items(store_slug, MARKETPLACE)
    if not catalog:
        return 0
    known_articles = {item["article"] for item in catalog}

    items = ozon_api.get_product_stocks(client_id, api_key)
    totals = _totals_by_scheme(items, known_articles)
    fbo_skus_by_article = _fbo_skus_by_article(items, known_articles)

    fbo_rows = ozon_api.get_fbo_stock_by_warehouse(client_id, api_key)
    own_warehouses = {
        str(row["warehouse_id"]): row for row in ozon_api.get_own_warehouses(client_id, api_key)
    }
    skus = [
        str(stock["sku"])
        for item in items
        for stock in item.get("stocks", [])
        if stock.get("sku") and stock.get("type", "").lower() in {"fbs", "rfbs"}
    ]
    fbs_rows = ozon_api.get_fbs_stock_by_warehouse(client_id, api_key, skus)

    clusters = _cluster_by_warehouse()

    now = _now()
    warehouse_quantities: dict[tuple[str, str], int] = {}
    seen_articles: set[str] = set()

    for row in fbo_rows:
        article = str(row.get("item_code") or "").strip()
        if not article or article not in known_articles:
            continue
        warehouse = str(row.get("warehouse_name") or "").strip()
        if not warehouse:
            continue
        sku = str(row.get("sku") or "").strip()
        active_skus = fbo_skus_by_article.get(article)
        if active_skus and sku and sku not in active_skus:
            continue
        try:
            quantity = int(row.get("free_to_sell_amount") or 0)
        except (TypeError, ValueError):
            continue

        seen_articles.add(article)
        key = (article, warehouse)
        warehouse_quantities[key] = warehouse_quantities.get(key, 0) + quantity

    warehouse_entries = [
        (article, warehouse, clusters.get(warehouse), quantity, now)
        for (article, warehouse), quantity in warehouse_quantities.items()
    ]

    names = fulfillment_lookup(db.get_fulfillments())
    seller_quantities: dict[str, dict[tuple[str, str], int]] = {"fbs": {}, "rfbs": {}}
    seen_stock = set()
    for row in fbs_rows:
        article = str(row.get("offer_id") or "")
        if article not in known_articles:
            continue
        warehouse_id = str(row.get("warehouse_id") or "")
        identity = (article, str(row.get("sku")), warehouse_id)
        if identity in seen_stock:
            continue
        seen_stock.add(identity)
        quantity = int(row.get("free_stock") or 0)
        if not quantity:
            continue
        info = own_warehouses.get(warehouse_id)
        if info is None:
            raise ozon_api.OzonApiError(None, f"Неизвестный склад FBS {warehouse_id}; остатки сохранены")
        scheme = "rfbs" if info.get("is_rfbs") or info.get("warehouse_type") == "rfbs" else "fbs"
        raw_name = str(row.get("warehouse_name") or info.get("name") or warehouse_id)
        warehouse = names.get(normalize(raw_name), raw_name)
        key = (article, warehouse)
        seller_quantities[scheme][key] = seller_quantities[scheme].get(key, 0) + quantity

    warehouse_snapshot = {"fbo": [entry[:4] for entry in warehouse_entries]}
    for scheme, quantities in seller_quantities.items():
        totals[scheme] = {}
        warehouse_snapshot[scheme] = []
        for (article, warehouse), quantity in quantities.items():
            totals[scheme][article] = totals[scheme].get(article, 0) + quantity
            warehouse_snapshot[scheme].append((article, warehouse, None, quantity))
    replace_snapshot(store_slug, MARKETPLACE, totals, warehouse_snapshot, now)

    covered = len(seen_articles | {a for s in totals.values() for a in s})
    logger.info(
        "Ozon %s: товаров с остатками %s, строк по складам %s, кластеров %s",
        _store_label(store_slug),
        covered,
        len(warehouse_entries),
        len(set(clusters.values())),
    )
    return covered


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict:

    report: dict = {}
    active_slugs = []
    targets = tuple(STORES) if store_slugs is None else store_slugs

    for slug in targets:
        if ozon_tokens.has_credentials(slug):
            report[slug] = {"token": True, "ozon": None}
            active_slugs.append(slug)
        else:
            report[slug] = {"token": False, "ozon": None}

    if not active_slugs:
        return report

    with ThreadPoolExecutor(max_workers=max(1, len(active_slugs))) as executor:
        future_to_slug = {executor.submit(sync_store, slug): slug for slug in active_slugs}
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            try:
                report[slug]["ozon"] = {"ok": True, "count": future.result()}
                db.record_sync_health(slug, MARKETPLACE, "stocks", True, None, _now())
            except Exception as e:
                logger.exception(
                    "Ozon %s: остатки не выгружены — %s",
                    _store_label(slug),
                    _error_message(e),
                )
                report[slug]["ozon"] = {"ok": False, "error": _error_message(e)}
                db.record_sync_health(slug, MARKETPLACE, "stocks", False, _error_message(e), _now())

    return report
