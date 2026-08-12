import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

from app import db
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES

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


def _cluster_by_warehouse() -> dict[str, str]:

    return db.get_warehouse_clusters(MARKETPLACE)


def sync_store(store_slug: str) -> int:

    ozon_api.set_store_context(_store_label(store_slug))
    client_id, api_key = ozon_tokens.get_credentials(store_slug)

    catalog = db.get_catalog_items(store_slug, MARKETPLACE)
    if not catalog:
        return 0
    known_articles = {item["article"] for item in catalog}

    items = ozon_api.get_product_stocks(client_id, api_key)
    totals = _totals_by_scheme(items, known_articles)

    fbo_rows = ozon_api.get_fbo_stock_by_warehouse(client_id, api_key)

    clusters = _cluster_by_warehouse()

    now = _now()
    warehouse_entries: list[tuple[str, str, str | None, int, str]] = []
    seen_articles: set[str] = set()

    for row in fbo_rows:
        article = str(row.get("item_code") or "").strip()
        if not article or article not in known_articles:
            continue
        warehouse = str(row.get("warehouse_name") or "").strip()
        if not warehouse:
            continue
        try:
            quantity = int(row.get("free_to_sell_amount") or 0)
        except (TypeError, ValueError):
            continue

        seen_articles.add(article)
        warehouse_entries.append((article, warehouse, clusters.get(warehouse), quantity, now))

    with _DB_LOCK:
        db.replace_mp_warehouse_stock(store_slug, MARKETPLACE, "fbo", warehouse_entries)

        for scheme in SCHEMES:
            scheme_totals = totals[scheme]

            for item in catalog:
                db.upsert_mp_stock(
                    store_slug,
                    item["article"],
                    MARKETPLACE,
                    scheme,
                    scheme_totals.get(item["article"], 0),
                    now,
                )

    covered = len(seen_articles | {a for s in totals.values() for a in s})
    logger.info(
        "Ozon %s: товаров с остатками %s, строк по складам %s, кластеров %s",
        _store_label(store_slug),
        covered,
        len(warehouse_entries),
        len(set(clusters.values())),
    )
    ozon_api.clear_store_context()
    return covered


def sync_all() -> dict:

    report: dict = {}
    active_slugs = []

    for slug in STORES:
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
