import logging
from datetime import UTC, datetime

from app import db
from app.core.stores import STORES
from app.repositories.stock_snapshot import replace_snapshot
from app.stock.fulfillment_names import fulfillment_lookup, normalize
from app.yandex import api as ya_api
from app.yandex import tokens as ya_tokens

logger = logging.getLogger(__name__)

MARKETPLACE = "YANDEX MARKET"
_DB_LOCK = db.WRITE_LOCK


def _normalize_ff_name(name: str) -> str:
    return normalize(name)


YANDEX_FBS_FF_ALIASES = {
    "ФуллСервис": "ФулСервис Подольск",
    "Фулл Сервис": "ФулСервис Подольск",
    "Afflatus": "AFFLATUS Купавна",
}


def known_ff_by_campaign() -> dict[str, str]:
    return fulfillment_lookup(db.get_fulfillments())


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
    fulfillment_by_campaign = known_ff_by_campaign()
    now = _now()

    totals: dict[str, dict[str, int]] = {}
    warehouse_totals: dict[str, dict[tuple[str, str], int]] = {}
    covered: set[str] = set()
    failed_schemes: set[str] = set()
    errors: list[str] = []
    disabled: list[tuple[str, str, str]] = []
    successful_warehouses: set[tuple[str, str]] = set()

    for campaign in campaigns:
        key = campaign["scheme_key"]
        totals.setdefault(key, {})
        warehouse_totals.setdefault(key, {})

        campaign_warehouse = fulfillment_by_campaign.get(
            _normalize_ff_name(campaign["name"]), campaign["name"]
        )
        try:
            rows = ya_api.get_stocks(api_key, campaign["id"])
        except ya_api.YandexApiError as error:
            message = f"Кампания {campaign['id']} ({campaign['name']}): {error.friendly}"
            if error.status == 403 and "API_DISABLED:" in error.detail:
                disabled.append((key, campaign_warehouse, message))
            else:
                failed_schemes.add(key)
                errors.append(message)
            continue
        successful_warehouses.add((key, campaign_warehouse))

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
                warehouse = fulfillment_by_campaign.get(
                    _normalize_ff_name(campaign["name"]),
                    campaign["name"],
                )

            warehouse_key = (article, warehouse)
            warehouse_totals[key][warehouse_key] = warehouse_totals[key].get(warehouse_key, 0) + quantity

    for key, warehouse, message in disabled:
        logger.warning("%s", message)
        if key == ya_tokens.FBY_SCHEME_KEY or (key, warehouse) not in successful_warehouses:
            failed_schemes.add(key)
            errors.append(message)
    complete = {key: values for key, values in totals.items() if key not in failed_schemes}
    warehouse_snapshot = {
        key: [(article, warehouse, None, quantity) for (article, warehouse), quantity in quantities.items()]
        for key, quantities in warehouse_totals.items()
        if key in complete
    }
    replace_snapshot(
        store_slug,
        MARKETPLACE,
        complete,
        warehouse_snapshot,
        now,
        remove_variants=(ya_tokens.FBS_SCHEME_KEY,) if ya_tokens.FBS_SCHEME_KEY in complete else (),
    )
    if errors:
        raise ya_api.YandexApiError(
            None, "Обновлено частично; сохранены прежние остатки проблемных схем. " + "; ".join(errors)
        )

    logger.info(
        "Яндекс %s: товаров с остатками %s, схем %s",
        _store_label(store_slug),
        len(covered),
        len(totals),
    )
    return len(covered)


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict:

    report: dict = {}
    targets = tuple(STORES) if store_slugs is None else store_slugs

    for slug in targets:
        if not ya_tokens.has_credentials(slug):
            report[slug] = {"token": False, "yandex": None}
            continue

        report[slug] = {"token": True, "yandex": None}
        try:
            report[slug]["yandex"] = {"ok": True, "count": sync_store(slug)}
            db.record_sync_health(slug, MARKETPLACE, "stocks", True, None, _now())
        except ya_api.YandexApiError as e:
            logger.warning(
                "yandex_stock_sync_failed store=%s status=%s error=%s",
                _store_label(slug),
                e.status or "network",
                e.friendly,
            )
            report[slug]["yandex"] = {"ok": False, "error": e.friendly}
            db.record_sync_health(slug, MARKETPLACE, "stocks", False, e.friendly, _now())
        except Exception as e:
            logger.exception(
                "yandex_stock_sync_crashed store=%s error=%s",
                _store_label(slug),
                _error_message(e),
            )
            report[slug]["yandex"] = {"ok": False, "error": _error_message(e)}
            db.record_sync_health(slug, MARKETPLACE, "stocks", False, _error_message(e), _now())

    return report
