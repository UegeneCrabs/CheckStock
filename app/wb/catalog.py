import logging
from datetime import UTC, datetime

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

MARKETPLACE = "WB"


def _store_label(store_slug: str) -> str:
    store = STORES.get(store_slug) or {}
    return store.get("name") or store_slug.upper()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _error_message(e: Exception) -> str:
    if isinstance(e, wb_api.WBApiError):
        return e.friendly
    return f"непредвиденная ошибка ({type(e).__name__}): {e}"


def clean_name(vendor_code: str) -> str:

    return " ".join((vendor_code or "").replace("_", " ").split())


def build_items(cards: list[dict]) -> tuple[list[dict], dict]:

    items: list[dict] = []
    stats = {
        "cards": len(cards),
        "no_article": 0,
        "no_barcode": 0,
        "multi_size": 0,
    }

    for card in cards:
        product = wb_api.normalize_card(card)
        nm_id = product["nm_id"]

        if not nm_id:
            stats["no_article"] += 1
            continue

        sizes = product["sizes"]
        if not sizes:
            stats["no_barcode"] += 1
            continue

        if len(sizes) > 1:
            stats["multi_size"] += 1

        vendor_code = (product["vendor_code"] or "").strip()

        if vendor_code and not vendor_code.isdigit():
            name = clean_name(vendor_code)
        else:
            name = product["title"]

        for size in sizes:
            suffix = f" / {size['tech_size']}" if len(sizes) > 1 and size["tech_size"] else ""

            items.append(
                {
                    "article": f"{nm_id}{suffix}",
                    "barcode": size["barcode"],
                    "name": name,
                    "mp_sku": None,
                    "mp_product_id": None,
                    "mp_updated_at": product["updated_at"],
                    "image_url": product["image_url"],
                    "is_service": False,
                }
            )

    return items, stats


def sync_store(store_slug: str, apply: bool = True) -> dict:

    token = wb_tokens.get_token(store_slug)

    cards = wb_api.get_cards_list(token)
    items, stats = build_items(cards)

    if not items:
        logger.warning("Каталог WB %s: карточек не пришло, каталог не трогаем", _store_label(store_slug))
        return {"total": 0, "added": 0, "updated": 0, "removed": 0, "kept": 0, **stats}

    if not apply:
        return {"total": len(items), "dry_run": True, **stats}

    with db.WRITE_LOCK:
        result = db.replace_catalog(
            store_slug,
            MARKETPLACE,
            items,
            _now(),
        )

    report = {"total": len(items), **stats, **result}
    logger.debug("Каталог WB %s: %s", _store_label(store_slug), report)
    return report


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict:

    report: dict = {}
    targets = tuple(STORES) if store_slugs is None else store_slugs

    for slug in targets:
        if not wb_tokens.has_token(slug):
            continue
        try:
            report[slug] = {"ok": True, **sync_store(slug)}
            db.record_sync_health(slug, MARKETPLACE, "catalog", True, None, _now())
        except wb_api.WBApiError as e:
            logger.warning(
                "wb_catalog_sync_failed store=%s status=%s error=%s",
                _store_label(slug),
                e.status or "network",
                e.friendly,
            )
            report[slug] = {"ok": False, "error": e.friendly}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, e.friendly, _now())
        except Exception as e:
            message = _error_message(e)
            logger.exception("wb_catalog_sync_crashed store=%s error=%s", _store_label(slug), message)
            report[slug] = {"ok": False, "error": message}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, message, _now())

    return report
