import logging
from datetime import UTC, datetime

from app import db
from app.ozon import api as ozon_api
from app.ozon import tokens as ozon_tokens
from app.stores import STORES

logger = logging.getLogger(__name__)

MARKETPLACE = "OZON"


SERVICE_HINTS = ("инструкция", "вкладыш", "наклейка", "листовка")


INTERNAL_BARCODE_PREFIX = "OZN"


def _store_label(store_slug: str) -> str:
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _is_service(offer_id: str) -> bool:
    text = offer_id.casefold()
    return any(hint in text for hint in SERVICE_HINTS)


def _pick_barcode(barcodes: list[str]) -> str:

    for barcode in barcodes:
        if not barcode.upper().startswith(INTERNAL_BARCODE_PREFIX):
            return barcode
    return ""


def sync_store(store_slug: str) -> dict:

    ozon_api.set_store_context(_store_label(store_slug))
    client_id, api_key = ozon_tokens.get_credentials(store_slug)

    listing = ozon_api.get_product_list(client_id, api_key)
    if not listing:
        return {"total": 0, "added": 0, "updated": 0, "removed": 0, "service": 0, "no_barcode": 0}

    product_ids = [
        row.get("product_id") or row.get("id") for row in listing if row.get("product_id") or row.get("id")
    ]
    raw = ozon_api.get_product_info(client_id, api_key, product_ids)

    items = []
    service = 0
    no_barcode = 0

    for row in raw:
        product = ozon_api.normalize_product(row)
        article = product["offer_id"]
        if not article:
            continue

        is_service = _is_service(article)
        service += int(is_service)

        barcode = _pick_barcode(product["barcodes"])
        if not barcode and not is_service:
            no_barcode += 1

        items.append(
            {
                "article": article,
                "barcode": barcode,
                "name": product["name"],
                "mp_sku": product["sku"],
                "mp_product_id": product["product_id"],
                "mp_updated_at": product["updated_at"],
                "image_url": product["image_url"],
                "is_service": is_service,
            }
        )

    with db.WRITE_LOCK:
        result = db.replace_catalog(store_slug, MARKETPLACE, items, _now())

    report = {
        "total": len(items),
        "service": service,
        "no_barcode": no_barcode,
        **result,
    }
    logger.info("Каталог Ozon %s: %s", _store_label(store_slug), report)
    ozon_api.clear_store_context()
    return report


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict:

    report: dict = {}
    targets = tuple(STORES) if store_slugs is None else store_slugs

    for slug in targets:
        if not ozon_tokens.has_credentials(slug):
            continue
        try:
            report[slug] = {"ok": True, **sync_store(slug)}
            db.record_sync_health(slug, MARKETPLACE, "catalog", True, None, _now())
        except ozon_api.OzonApiError as e:
            logger.error("Ozon %s: каталог не выгружен — %s", _store_label(slug), e.friendly)
            report[slug] = {"ok": False, "error": e.friendly}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, e.friendly, _now())
        except Exception as e:
            logger.exception(
                "Ozon %s: каталог не выгружен — %s: %s",
                _store_label(slug),
                type(e).__name__,
                e,
            )
            report[slug] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, f"{type(e).__name__}: {e}", _now())

    return report
