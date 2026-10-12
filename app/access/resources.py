"""Configured resources available to every active account."""

from app.access.authentication import is_active_user
from app.core.domain import MARKETPLACES
from app.core.stores import STORES
from app.dto.identity import User


def store_marketplace_pairs(user: User | None) -> tuple[tuple[str, str], ...]:
    if not is_active_user(user):
        return ()
    return tuple((store, marketplace) for store in STORES for marketplace in MARKETPLACES)


def accessible_stores(user: User | None, marketplace: str | None = None) -> tuple[str, ...]:
    if not is_active_user(user) or (marketplace and marketplace.strip().upper() not in MARKETPLACES):
        return ()
    return tuple(STORES)


def accessible_marketplaces(user: User | None, store_slug: str | None = None) -> tuple[str, ...]:
    if not is_active_user(user) or (store_slug and store_slug.strip().lower() not in STORES):
        return ()
    return tuple(MARKETPLACES)


def has_store_marketplace(
    user: User | None,
    store_slug: str,
    marketplace: str,
    target_marketplace: str | None = None,
) -> bool:
    return bool(
        is_active_user(user)
        and store_slug.strip().lower() in STORES
        and marketplace.strip().upper() in MARKETPLACES
        and (not target_marketplace or target_marketplace.strip().upper() in MARKETPLACES)
    )
