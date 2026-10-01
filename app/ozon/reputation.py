"""Cached Ozon buyer review scores, grouped by marketplace SKU."""

import logging
from collections import defaultdict
from datetime import UTC, datetime

from app.ozon import api, tokens
from app.repositories.core import get_connection

logger = logging.getLogger(__name__)


def get_store(store_slug: str) -> dict[str, dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT sku, rating, reviews_count, synced_at FROM ozon_product_reputation WHERE store_slug=?",
            (store_slug,),
        ).fetchall()
    return {str(row["sku"]): dict(row) for row in rows}


def sync_store(store_slug: str) -> dict:
    from app import db

    client_id, api_key = tokens.get_credentials(store_slug)
    reviews = api.get_reviews(client_id, api_key)
    scores: dict[str, list[int]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    for review in reviews:
        sku = str(review.get("sku") or "").strip()
        if not sku:
            continue
        counts[sku] += 1
        try:
            rating = int(review.get("rating"))
        except (TypeError, ValueError):
            continue
        if 1 <= rating <= 5 and review.get("is_rating_participant", True):
            scores[sku].append(rating)

    catalog_skus = {
        str(row["mp_sku"])
        for row in db.get_catalog_items(store_slug, "OZON")
        if row.get("mp_sku")
    }

    now = datetime.now(UTC).isoformat()
    with get_connection() as conn:
        conn.execute("DELETE FROM ozon_product_reputation WHERE store_slug=?", (store_slug,))
        conn.executemany(
            "INSERT INTO ozon_product_reputation (store_slug, sku, rating, reviews_count, synced_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                (
                    store_slug,
                    sku,
                    round(sum(scores[sku]) / len(scores[sku]), 2) if scores[sku] else None,
                    counts[sku],
                    now,
                )
                for sku in catalog_skus | counts.keys()
            ),
        )
        conn.commit()
    return {"reviews": len(reviews), "products": len(catalog_skus)}


def sync_all(store_slugs: tuple[str, ...] | None = None) -> dict:
    from app import db
    from app.core.stores import STORES

    result = {}
    for slug in tuple(STORES) if store_slugs is None else store_slugs:
        if not tokens.has_credentials(slug):
            continue
        now = datetime.now(UTC).isoformat()
        try:
            result[slug] = {"ok": True, **sync_store(slug)}
            db.record_sync_health(slug, "OZON", "reputation", True, None, now)
        except api.OzonApiError as exc:
            logger.warning("Ozon %s: отзывы не загружены: %s", slug, exc.friendly)
            result[slug] = {"ok": False, "error": exc.friendly}
            db.record_sync_health(slug, "OZON", "reputation", False, exc.friendly, now)
        except Exception as exc:
            logger.exception("Ozon %s: отзывы не загружены", slug)
            result[slug] = {"ok": False, "error": str(exc)}
            db.record_sync_health(slug, "OZON", "reputation", False, str(exc), now)
    return result
