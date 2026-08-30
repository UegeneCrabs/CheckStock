import logging
from datetime import UTC, datetime, timedelta

from app import db
from app.stores import STORES
from app.wb import tokens as wb_tokens

logger = logging.getLogger(__name__)

WARN_DAYS = 7


def _now() -> datetime:
    return datetime.now(UTC)


def refresh_token_info(store_slugs: tuple[str, ...] | None = None) -> int:

    now_iso = _now().isoformat()
    processed = 0

    with db.WRITE_LOCK:
        for slug in (tuple(STORES) if store_slugs is None else store_slugs):
            if slug not in STORES:
                continue
            if not wb_tokens.has_token(slug):
                continue
            expiry = wb_tokens.get_token_expiry(wb_tokens.get_token(slug))
            db.upsert_wb_token_info(slug, expiry.isoformat() if expiry else None, now_iso)
            processed += 1

    logger.info("Сроки действия ключей WB обновлены: %s магазинов", processed)
    return processed


def should_refresh(last_checked: str | None) -> bool:

    if not last_checked:
        return True
    try:
        checked = datetime.fromisoformat(last_checked)
    except ValueError:
        return True

    now = _now()
    return (now - checked) >= timedelta(days=1)


def get_warnings() -> list[dict]:

    now = _now()
    warnings = []

    for info in db.get_wb_token_infos():
        if not info["expires_at"]:
            continue
        try:
            expires = datetime.fromisoformat(info["expires_at"])
        except ValueError:
            continue

        days_left = (expires - now).days
        if days_left > WARN_DAYS:
            continue

        store = STORES.get(info["store_slug"], {})
        warnings.append(
            {
                "store": store.get("name", info["store_slug"]),
                "expires_at": info["expires_at"],
                "days_left": days_left,
                "expired": expires <= now,
            }
        )

    warnings.sort(key=lambda w: w["days_left"])
    return warnings
