import logging
from datetime import UTC, datetime

from app import db
from app.stores import STORES
from app.yandex import api as ya_api
from app.yandex import tokens as ya_tokens

logger = logging.getLogger(__name__)

MARKETPLACE = "YANDEX MARKET"
_DB_LOCK = db.WRITE_LOCK


def _store_label(store_slug: str) -> str:
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _error_message(e: Exception) -> str:
    if isinstance(e, ya_api.YandexApiError):
        return e.friendly
    return f"непредвиденная ошибка ({type(e).__name__}): {e}"


def resolve_campaigns(store_slug: str, api_key: str) -> list[dict]:

    configured = ya_tokens.get_campaigns(store_slug)
    if configured:
        return configured

    campaigns = []
    for row in ya_api.get_campaigns(api_key):
        normalized = ya_api.normalize_campaign(row)
        campaign_id = normalized["campaign_id"]
        if not campaign_id:
            continue
        campaigns.append(
            {
                "id": campaign_id,
                "scheme": normalized["scheme"],
                "name": normalized["domain"] or f"Магазин {campaign_id}",
                "scheme_key": ya_tokens.scheme_key(normalized["scheme"], campaign_id),
            }
        )
    return campaigns


def store_schemes(store_slug: str) -> list[tuple[str, str]]:

    schemes: list[tuple[str, str]] = []
    seen: set[str] = set()

    for campaign in ya_tokens.get_campaigns(store_slug):
        key = campaign["scheme_key"]
        if key in seen:
            continue
        seen.add(key)
        schemes.append((key, ya_tokens.scheme_label(campaign)))

    schemes.sort(key=lambda item: (item[0] != ya_tokens.FBY_SCHEME_KEY, item[1]))
    return schemes


def _warehouse_names(api_key: str) -> dict[int, str]:

    try:
        warehouses = ya_api.get_fulfillment_warehouses(api_key)
    except Exception as e:
        logger.warning("Яндекс: список складов не получен (%s), покажем id", _error_message(e))
        return {}

    names = {}
    for row in warehouses:
        try:
            names[int(row["id"])] = str(row.get("name") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
    return names


def sync_store(store_slug: str) -> int:

    api_key = ya_tokens.get_api_key(store_slug)

    catalog = db.get_catalog_items(store_slug, MARKETPLACE)
    if not catalog:
        return 0
    known_articles = {item["article"] for item in catalog}

    campaigns = resolve_campaigns(store_slug, api_key)
    if not campaigns:
        logger.warning("Яндекс %s: у ключа нет магазинов", _store_label(store_slug))
        return 0

    warehouse_names = _warehouse_names(api_key)
    now = _now()

    totals: dict[str, dict[str, int]] = {}
    warehouse_entries: dict[str, list[tuple]] = {}
    covered: set[str] = set()

    for campaign in campaigns:
        key = campaign["scheme_key"]
        totals.setdefault(key, {})
        warehouse_entries.setdefault(key, [])

        rows = ya_api.get_stocks(api_key, campaign["id"])

        for row in rows:
            article = row["article"]
            if not article or article not in known_articles:
                continue

            quantity = ya_api.available_quantity(row["stocks"])
            if quantity <= 0:
                continue

            totals[key][article] = totals[key].get(article, 0) + quantity
            covered.add(article)

            warehouse_id = row["warehouse_id"]
            if key == ya_tokens.FBY_SCHEME_KEY:
                warehouse = warehouse_names.get(warehouse_id) or f"Склад {warehouse_id}"
            else:
                warehouse = campaign["name"]

            warehouse_entries[key].append((article, warehouse, None, quantity, now))

    with _DB_LOCK:
        for key, entries in warehouse_entries.items():
            db.replace_mp_warehouse_stock(store_slug, MARKETPLACE, key, entries)

        for key, scheme_totals in totals.items():
            for item in catalog:
                db.upsert_mp_stock(
                    store_slug,
                    item["article"],
                    MARKETPLACE,
                    key,
                    scheme_totals.get(item["article"], 0),
                    now,
                )

    logger.info(
        "Яндекс %s: товаров с остатками %s, схем %s",
        _store_label(store_slug),
        len(covered),
        len(totals),
    )
    return len(covered)


def sync_all() -> dict:

    report: dict = {}

    for slug in STORES:
        if not ya_tokens.has_credentials(slug):
            report[slug] = {"token": False, "yandex": None}
            continue

        report[slug] = {"token": True, "yandex": None}
        try:
            report[slug]["yandex"] = {"ok": True, "count": sync_store(slug)}
            db.record_sync_health(slug, MARKETPLACE, "stocks", True, None, _now())
        except Exception as e:
            logger.exception(
                "Яндекс %s: остатки не выгружены — %s",
                _store_label(slug),
                _error_message(e),
            )
            report[slug]["yandex"] = {"ok": False, "error": _error_message(e)}
            db.record_sync_health(slug, MARKETPLACE, "stocks", False, _error_message(e), _now())

    return report
