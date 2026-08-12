import logging
from datetime import UTC, datetime

from app import db
from app.stores import STORES
from app.wb import api as wb_api
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

MARKETPLACE = "WB"
STALE_TAG = "Старье"
STALE_TAG_STORES = frozenset({"rimili", "tris", "toyka", "rockkiddo"})


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


def _normalize_tag_name(value: object) -> str:

    return " ".join(str(value or "").split()).casefold().replace("ё", "е")


def card_has_tag(card: dict, tag_name: str) -> bool:

    expected = _normalize_tag_name(tag_name)
    for tag in card.get("tags") or []:
        name = tag.get("name") if isinstance(tag, dict) else tag
        if _normalize_tag_name(name) == expected:
            return True
    return False


def tagged_nm_ids(cards: list[dict], tag_name: str) -> set[str]:

    return {
        str(card.get("nmID") or "").strip()
        for card in cards
        if card_has_tag(card, tag_name) and str(card.get("nmID") or "").strip()
    }


def articles_for_nm_ids(articles: set[str], nm_ids: set[str]) -> set[str]:

    return {article for article in articles if article.partition(" / ")[0].strip() in nm_ids}


def build_items(cards: list[dict], excluded_tag: str | None = None) -> tuple[list[dict], dict]:

    items: list[dict] = []
    stats = {
        "cards": len(cards),
        "no_article": 0,
        "no_barcode": 0,
        "multi_size": 0,
        "excluded_tag": 0,
    }

    for card in cards:
        if excluded_tag and card_has_tag(card, excluded_tag):
            stats["excluded_tag"] += 1
            continue

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
    excluded_tag = STALE_TAG if store_slug in STALE_TAG_STORES else None
    excluded_nm_ids = tagged_nm_ids(cards, excluded_tag) if excluded_tag else set()
    items, stats = build_items(cards, excluded_tag=excluded_tag)

    if not items and not excluded_nm_ids:
        logger.warning("Каталог WB %s: карточек не пришло, каталог не трогаем", _store_label(store_slug))
        return {"total": 0, "added": 0, "updated": 0, "removed": 0, "kept": 0, **stats}

    if not apply:
        return {"total": len(items), "dry_run": True, **stats}

    with db.WRITE_LOCK:
        existing_articles = {
            row["article"] for row in db.get_catalog_items(store_slug, MARKETPLACE, include_service=True)
        }
        force_remove_articles = articles_for_nm_ids(existing_articles, excluded_nm_ids)
        result = db.replace_catalog(
            store_slug,
            MARKETPLACE,
            items,
            _now(),
            force_remove_articles=force_remove_articles,
        )

    report = {"total": len(items), **stats, **result}
    logger.info("Каталог WB %s: %s", _store_label(store_slug), report)
    return report


def sync_all() -> dict:

    report: dict = {}

    for slug in STORES:
        if not wb_tokens.has_token(slug):
            continue
        try:
            report[slug] = {"ok": True, **sync_store(slug)}
            db.record_sync_health(slug, MARKETPLACE, "catalog", True, None, _now())
        except Exception as e:
            message = _error_message(e)
            logger.exception("WB %s: каталог не выгружен — %s", _store_label(slug), message)
            report[slug] = {"ok": False, "error": message}
            db.record_sync_health(slug, MARKETPLACE, "catalog", False, message, _now())

    return report
