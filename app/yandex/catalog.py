"""
Выгрузка каталога товаров с Яндекс Маркета.

Как и у Ozon, каталог живёт своей жизнью и не связывается с каталогом WB.
Проверка на TRIS: из 243 позиций кабинета 165 совпали с WB по артикулу,
61 по баркоду и 17 не совпали никак. Даже 17 потерянных позиций — это
17 строк, которых не будет в таблице, а связывать их вручную мы решили
не заводить.

Особенность Яндекса: каталог принадлежит КАБИНЕТУ (businessId), а не
магазину. У кабинета может быть несколько магазинов под разные модели
работы, и каталог у них общий — поэтому тянем его один раз, а не по разу
на каждый campaignId.
"""

import logging
from datetime import datetime, timezone

from app import db
from app.stores import STORES
from app.yandex import api as ya_api
from app.yandex import tokens as ya_tokens

logger = logging.getLogger("checkstock.yandex_catalog")

MARKETPLACE = "YANDEX MARKET"


def _store_label(store_slug: str) -> str:
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_business_id(store_slug: str, api_key: str) -> int | None:
    """Кабинет магазина: из настроек, а если там пусто — спрашиваем у Маркета.

    Так ключ достаточно положить в secrets, не выясняя идентификаторы руками.
    """
    business_id = ya_tokens.get_business_id(store_slug)
    if business_id:
        return business_id

    campaigns = [ya_api.normalize_campaign(row) for row in ya_api.get_campaigns(api_key)]
    ids = {c["business_id"] for c in campaigns if c["business_id"]}
    if len(ids) > 1:
        logger.warning(
            "Яндекс %s: у ключа несколько кабинетов %s — беру первый, "
            "пропишите нужный в secrets/yandex_tokens.json",
            _store_label(store_slug), sorted(ids),
        )
    return next(iter(sorted(ids)), None)


def sync_store(store_slug: str) -> dict:
    """Обновляет каталог Яндекса одного магазина. Возвращает отчёт для UI."""
    api_key = ya_tokens.get_api_key(store_slug)

    business_id = resolve_business_id(store_slug, api_key)
    if not business_id:
        return {"total": 0, "added": 0, "updated": 0, "removed": 0, "no_barcode": 0}

    raw = ya_api.get_catalog(api_key, business_id)

    items = []
    no_barcode = 0

    for row in raw:
        product = ya_api.normalize_catalog_item(row)
        article = product["article"]
        if not article:
            continue  # без артикула продавца карточку не с чем связать

        if not product["barcode"]:
            no_barcode += 1

        items.append({
            "article": article,
            "barcode": product["barcode"],
            "name": product["name"],
            "mp_sku": product["market_sku"],
            "mp_product_id": None,
            "mp_updated_at": product["updated_at"],
            "is_service": False,
        })

    with db.WRITE_LOCK:
        result = db.replace_catalog(store_slug, MARKETPLACE, items, _now())

    report = {"total": len(items), "no_barcode": no_barcode, **result}
    logger.info("Каталог Яндекса %s: %s", _store_label(store_slug), report)
    return report


def sync_all() -> dict:
    """Обновляет каталоги Яндекса по всем магазинам с ключами."""
    report: dict = {}

    for slug in STORES:
        if not ya_tokens.has_credentials(slug):
            continue
        try:
            report[slug] = {"ok": True, **sync_store(slug)}
            db.record_sync_health(slug, MARKETPLACE, "catalog", True, None, _now())
        except ya_api.YandexApiError as e:
            logger.error("Яндекс %s: каталог не выгружен — %s", _store_label(slug), e.friendly)
            report[slug] = {"ok": False, "error": e.friendly}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, e.friendly, _now())
        except Exception as e:
            logger.error("Яндекс %s: каталог не выгружен — %s: %s",
                         _store_label(slug), type(e).__name__, e)
            logger.debug("Подробности ошибки каталога Яндекса %s", slug, exc_info=True)
            report[slug] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False,
                                  f"{type(e).__name__}: {e}", _now())

    return report
