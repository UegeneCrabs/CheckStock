from fastapi import HTTPException, Request

from app import auth
from app.access_control import (
    accessible_marketplaces as policy_accessible_marketplaces,
)
from app.access_control import accessible_stores as policy_accessible_stores
from app.access_control import has_scope
from app.dto.identity import User, coerce_user
from app.dto.stores import StoreAccessContext, StoreCollection, StoreItem
from app.stores import STORES


def accessible_store_slugs(user: User | None) -> tuple[str, ...]:
    user = coerce_user(user)
    if user is None:
        return ()
    if auth.has_role(user, "superadmin"):
        return tuple(STORES)
    if user.access_profile is not None:
        return policy_accessible_stores(user)
    allowed = set(user.store_slugs or tuple(STORES))
    return tuple(slug for slug in STORES if slug in allowed)


def accessible_store_items(user: User | None) -> StoreCollection:
    allowed = set(accessible_store_slugs(user))
    return StoreCollection(
        tuple(StoreItem(slug=slug, store=store) for slug, store in STORES.items() if slug in allowed)
    )


def has_store_access(user: User | None, store_slug: str) -> bool:
    return store_slug.lower() in set(accessible_store_slugs(user))


def accessible_marketplaces(user: User | None, store_slug: str | None = None) -> tuple[str, ...]:
    return policy_accessible_marketplaces(user, store_slug)


def has_marketplace_access(user: User | None, store_slug: str, marketplace: str) -> bool:
    return has_scope(user, store_slug, marketplace)


def require_store_access(request: Request, slug: str) -> StoreAccessContext:
    store_slug = slug.lower()
    store = STORES.get(store_slug)
    if store is None:
        raise HTTPException(status_code=404, detail="Магазин не найден")
    if not has_store_access(request.state.user, store_slug):
        raise HTTPException(status_code=403, detail="Нет доступа к этому магазину")
    return StoreAccessContext(slug=store_slug, store=store)


def first_accessible_store(user: User | None) -> str | None:
    slugs = accessible_store_slugs(user)
    return slugs[0] if slugs else None
