import logging
from datetime import UTC, datetime

from app import db
from app.core.stores import STORES
from app.yandex import api as ya_api
from app.yandex import tokens as ya_tokens
from app.yandex.accounts import resolve_business_id

logger = logging.getLogger(__name__)

MARKETPLACE = "YANDEX MARKET"


def _store_label(store_slug: str) -> str:
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def sync_store(store_slug: str) -> dict:

    api_key = ya_tokens.get_api_key(store_slug)

    business_id = resolve_business_id(store_slug, api_key)
    raw = ya_api.get_catalog(api_key, business_id)
    archived = ya_api.get_catalog(api_key, business_id, archived=True)

    items = []
    no_barcode = 0

    for row in raw:
        product = ya_api.normalize_catalog_item(row)
        article = product["article"]
        if not article:
            continue

        if not product["barcode"]:
            no_barcode += 1

        items.append(
            {
                "article": article,
                "barcode": product["barcode"],
                "barcodes": product.get("barcodes", [product["barcode"]]),
                "name": product["name"],
                "mp_sku": product["market_sku"],
                "mp_product_id": None,
                "mp_updated_at": product["updated_at"],
                "image_url": product["image_url"],
                "is_service": False,
            }
        )

    with db.WRITE_LOCK:
        result = db.replace_catalog(store_slug, MARKETPLACE, items, _now())

    from app.repositories import unit_economics_yandex

    now = _now()
    unit_economics_yandex.save_snapshot(
        store_slug,
        "archive",
        [
            {"article": item["article"]}
            for row in archived
            if (item := ya_api.normalize_catalog_item(row))["article"]
        ],
        now[:10],
        now[:10],
        now,
    )

    from app.repositories import yandex_economics
    from app.yandex.economics_api import catalog_values

    for row in raw:
        article = str((row.get("offer") or {}).get("offerId") or "")
        if article:
            yandex_economics.save_source(store_slug, article, "catalog", catalog_values(row))

    report = {"total": len(items), "archived": len(archived), "no_barcode": no_barcode, **result}
    logger.debug("Каталог Яндекса %s: %s", _store_label(store_slug), report)
    return report


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict:

    report: dict = {}
    targets = tuple(STORES) if store_slugs is None else store_slugs

    for slug in targets:
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
            logger.exception(
                "Яндекс %s: каталог не выгружен — %s: %s",
                _store_label(slug),
                type(e).__name__,
                e,
            )
            report[slug] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, f"{type(e).__name__}: {e}", _now())

    return report
