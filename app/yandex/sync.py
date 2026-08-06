"""
Синхронизация остатков Яндекс Маркета.

Устройство отличается от WB и Ozon тем, что остатки живут не в кабинете,
а в магазинах: у одного кабинета их несколько, по одному на модель работы.
У TRIS это FBY плюс два разных FBS — «ФуллСервис» и «Afflatus». Это разные
склады разных партнёров, поэтому каждый FBS-магазин получает свою колонку:
свести их в одну значило бы потерять то, ради чего детализация и делалась.

Из типов остатка берём только AVAILABLE. Проверено на реальных данных:
FIT = AVAILABLE + FREEZE, то есть «Годный» включает уже зарезервированное
под заказы. Плюс DEFECT, QUARANTINE и UTILIZATION — это 64 единицы у TRIS,
которые продать нельзя вовсе. Возьми мы FIT, наличие оказалось бы завышено.

Маркет отдаёт все пары «товар × склад», включая нулевые: 325 строк на
59 товаров с остатком. Нули не храним — политика «ноль это отсутствие
строки» здесь экономит особенно много.
"""

import logging
from datetime import datetime, timezone

from app import db
from app.stores import STORES
from app.yandex import api as ya_api
from app.yandex import tokens as ya_tokens

logger = logging.getLogger("checkstock.yandex_sync")

MARKETPLACE = "YANDEX MARKET"
_DB_LOCK = db.WRITE_LOCK


def _store_label(store_slug: str) -> str:
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_message(e: Exception) -> str:
    if isinstance(e, ya_api.YandexApiError):
        return e.friendly
    return f"непредвиденная ошибка ({type(e).__name__}): {e}"


def resolve_campaigns(store_slug: str, api_key: str) -> list[dict]:
    """Магазины кабинета: из настроек или прямо у Маркета.

    Спрашивать у API удобнее, чем вести список руками: новый FBS-магазин
    появится в отчёте сам, без правки secrets.
    """
    configured = ya_tokens.get_campaigns(store_slug)
    if configured:
        return configured

    campaigns = []
    for row in ya_api.get_campaigns(api_key):
        normalized = ya_api.normalize_campaign(row)
        campaign_id = normalized["campaign_id"]
        if not campaign_id:
            continue
        campaigns.append({
            "id": campaign_id,
            "scheme": normalized["scheme"],
            "name": normalized["domain"] or f"Магазин {campaign_id}",
            "scheme_key": ya_tokens.scheme_key(normalized["scheme"], campaign_id),
        })
    return campaigns


def store_schemes(store_slug: str) -> list[tuple[str, str]]:
    """[(ключ схемы, подпись колонки)] для таблицы остатков.

    Набор зависит от магазина, а не только от площадки: у одного продавца
    один FBS-партнёр, у другого три. Читаем из настроек — без обращения к
    API, потому что вызывается при каждом открытии страницы.
    """
    schemes: list[tuple[str, str]] = []
    seen: set[str] = set()

    for campaign in ya_tokens.get_campaigns(store_slug):
        key = campaign["scheme_key"]
        if key in seen:
            continue
        seen.add(key)
        schemes.append((key, ya_tokens.scheme_label(campaign)))

    # FBY всегда первым: это основной канал, а FBS-партнёры идут следом
    schemes.sort(key=lambda item: (item[0] != ya_tokens.FBY_SCHEME_KEY, item[1]))
    return schemes


def _warehouse_names(api_key: str) -> dict[int, str]:
    """{id склада Маркета: название}. В остатках приходит только id."""
    try:
        warehouses = ya_api.get_fulfillment_warehouses(api_key)
    except Exception as e:
        logger.warning("Яндекс: список складов не получен (%s), покажем id",
                       _error_message(e))
        return {}

    names = {}
    for row in warehouses:
        try:
            names[int(row["id"])] = str(row.get("name") or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
    return names


def sync_store(store_slug: str) -> int:
    """Полная синхронизация одного магазина. Возвращает число товаров с остатком."""
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

    # {ключ схемы: {артикул: количество}} и строки для детализации складов
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
                continue  # нули не храним

            totals[key][article] = totals[key].get(article, 0) + quantity
            covered.add(article)

            # У FBY склад — это склад Маркета с человеческим названием.
            # У FBS склад партнёрский, названия в API нет, поэтому берём имя
            # магазина: у одного FBS-магазина склад ровно один.
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
            # проходим по всему каталогу, чтобы обнулившиеся позиции удалились
            for item in catalog:
                db.upsert_mp_stock(
                    store_slug, item["article"], MARKETPLACE, key,
                    scheme_totals.get(item["article"], 0), now,
                )

    logger.info(
        "Яндекс %s: товаров с остатками %s, схем %s",
        _store_label(store_slug), len(covered), len(totals),
    )
    return len(covered)


def sync_all() -> dict:
    """Синхронизирует все магазины с ключами Яндекса.

    Последовательно: у Маркета лимит считается на кабинет, а магазины одного
    продавца сидят в одном кабинете — параллелить их значит соревноваться
    с самим собой за один и тот же лимит.
    """
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
            logger.error("Яндекс %s: остатки не выгружены — %s",
                         _store_label(slug), _error_message(e))
            logger.debug("Подробности ошибки Яндекса %s", slug, exc_info=True)
            report[slug]["yandex"] = {"ok": False, "error": _error_message(e)}
            db.record_sync_health(slug, MARKETPLACE, "stocks", False, _error_message(e), _now())

    return report
