from __future__ import annotations

from enum import StrEnum

from app.config import settings
from app.domain import MARKETPLACES
from app.dto.identity import AccessProfile, Role, User, coerce_user
from app.stores import STORES


class ActionPermission(StrEnum):
    SALES_VIEW = "sales.view"
    SALES_EXPORT = "sales.export"
    STOCK_BALANCE_VIEW = "stock.balance.view"
    STOCK_TOTAL_VIEW = "stock.total.view"
    STOCK_TOTAL_EXPORT = "stock.total.export"
    STOCK_RECEIVE = "stock.receive.create"
    STOCK_TRANSFER = "stock.transfer.create"
    STOCK_TRANSFER_RECEIVE = "stock.transfer.receive"
    STOCK_TRANSFER_CANCEL = "stock.transfer.cancel"
    STOCK_TRANSFER_CROSS_MARKETPLACE = "stock.transfer.cross_marketplace"
    STOCK_SHIPMENT = "stock.shipment.create"
    STOCK_WRITEOFF = "stock.writeoff.create"
    STOCK_OPERATIONS_VIEW = "stock.operations.view"
    STOCK_OPERATIONS_EXPORT = "stock.operations.export"
    UNIT_ECONOMICS_VIEW = "unit_economics.view"
    UNIT_ECONOMICS_EDIT = "unit_economics.edit"
    USERS_MANAGE_WITHIN_SCOPE = "users.manage_within_scope"
    ACCESS_REQUESTS_APPROVE = "access_requests.approve"


PROFILE_LABELS: dict[AccessProfile, str] = {
    AccessProfile.MARKETPLACE_MANAGER: "Менеджер маркетплейса",
    AccessProfile.SENIOR_MARKETPLACE_MANAGER: "Старший менеджер маркетплейса",
    AccessProfile.MARKETPLACE_LEAD: "Управляющий площадкой",
    AccessProfile.STORE_MANAGER: "Управляющий магазином",
    AccessProfile.PROCUREMENT: "Снабженец",
}


_BASE_MARKETPLACE_MANAGER = {
    ActionPermission.SALES_VIEW,
    ActionPermission.SALES_EXPORT,
    ActionPermission.STOCK_BALANCE_VIEW,
    ActionPermission.STOCK_TOTAL_VIEW,
    ActionPermission.STOCK_TOTAL_EXPORT,
    ActionPermission.STOCK_RECEIVE,
    ActionPermission.STOCK_TRANSFER,
    ActionPermission.STOCK_TRANSFER_RECEIVE,
    ActionPermission.STOCK_SHIPMENT,
    ActionPermission.STOCK_WRITEOFF,
    ActionPermission.STOCK_OPERATIONS_VIEW,
    ActionPermission.STOCK_OPERATIONS_EXPORT,
}

PROFILE_PERMISSIONS: dict[AccessProfile, frozenset[ActionPermission]] = {
    AccessProfile.MARKETPLACE_MANAGER: frozenset(_BASE_MARKETPLACE_MANAGER),
    AccessProfile.SENIOR_MARKETPLACE_MANAGER: frozenset(
        _BASE_MARKETPLACE_MANAGER
        | {
            ActionPermission.STOCK_TRANSFER_CANCEL,
            ActionPermission.UNIT_ECONOMICS_VIEW,
            ActionPermission.USERS_MANAGE_WITHIN_SCOPE,
        }
    ),
    AccessProfile.MARKETPLACE_LEAD: frozenset(
        _BASE_MARKETPLACE_MANAGER
        | {
            ActionPermission.UNIT_ECONOMICS_VIEW,
            ActionPermission.UNIT_ECONOMICS_EDIT,
            ActionPermission.USERS_MANAGE_WITHIN_SCOPE,
        }
    ),
    AccessProfile.STORE_MANAGER: frozenset(
        _BASE_MARKETPLACE_MANAGER
        | {
            ActionPermission.STOCK_TRANSFER_CROSS_MARKETPLACE,
            ActionPermission.UNIT_ECONOMICS_VIEW,
            ActionPermission.UNIT_ECONOMICS_EDIT,
            ActionPermission.USERS_MANAGE_WITHIN_SCOPE,
        }
    ),
    AccessProfile.PROCUREMENT: frozenset(
        {
            ActionPermission.STOCK_BALANCE_VIEW,
            ActionPermission.STOCK_TOTAL_VIEW,
            ActionPermission.STOCK_RECEIVE,
            ActionPermission.STOCK_TRANSFER,
            ActionPermission.STOCK_TRANSFER_RECEIVE,
            ActionPermission.STOCK_SHIPMENT,
            ActionPermission.STOCK_OPERATIONS_VIEW,
        }
    ),
}

EXPERIMENTAL_SECTIONS = frozenset(
    {"decision_center", "ephemerides", "rnp", "supply", "stock_overview"}
)


def is_experimental_owner(user: User | None) -> bool:
    normalized = coerce_user(user)
    if normalized is None:
        return False
    configured = {value.casefold() for value in settings.experimental_owner_logins}
    if configured:
        return normalized.login.casefold() in configured
    return normalized.role is Role.SUPERADMIN and normalized.id == 1


def profile_label(profile: AccessProfile | None, marketplaces: tuple[str, ...] = ()) -> str:
    if profile is None:
        return "Без должностного профиля"
    label = PROFILE_LABELS[profile]
    unique = tuple(dict.fromkeys(marketplaces))
    if profile in {
        AccessProfile.MARKETPLACE_MANAGER,
        AccessProfile.SENIOR_MARKETPLACE_MANAGER,
        AccessProfile.MARKETPLACE_LEAD,
    } and len(unique) == 1:
        return f"{label} · {unique[0]}"
    return label


def scope_pairs(user: User | None) -> tuple[tuple[str, str], ...]:
    normalized = coerce_user(user)
    if normalized is None:
        return ()
    if normalized.role is Role.SUPERADMIN:
        return tuple((store_slug, marketplace) for store_slug in STORES for marketplace in MARKETPLACES)
    if normalized.access_profile is not None:
        allowed = {
            (scope.store_slug.lower(), scope.marketplace.upper())
            for scope in normalized.access_scopes
            if scope.store_slug.lower() in STORES and scope.marketplace.upper() in MARKETPLACES
        }
        return tuple(
            (store_slug, marketplace)
            for store_slug in STORES
            for marketplace in MARKETPLACES
            if (store_slug, marketplace) in allowed
        )
    stores = normalized.store_slugs or tuple(STORES)
    return tuple(
        (store_slug, marketplace)
        for store_slug in STORES
        if store_slug in stores
        for marketplace in MARKETPLACES
    )


def accessible_marketplaces(user: User | None, store_slug: str | None = None) -> tuple[str, ...]:
    target_store = str(store_slug or "").strip().lower()
    allowed = {
        marketplace
        for scoped_store, marketplace in scope_pairs(user)
        if not target_store or scoped_store == target_store
    }
    return tuple(marketplace for marketplace in MARKETPLACES if marketplace in allowed)


def accessible_stores(user: User | None, marketplace: str | None = None) -> tuple[str, ...]:
    target_marketplace = str(marketplace or "").strip().upper()
    allowed = {
        store_slug
        for store_slug, scoped_marketplace in scope_pairs(user)
        if not target_marketplace or scoped_marketplace == target_marketplace
    }
    return tuple(store_slug for store_slug in STORES if store_slug in allowed)


def has_scope(user: User | None, store_slug: str, marketplace: str) -> bool:
    target = (store_slug.strip().lower(), marketplace.strip().upper())
    return target in set(scope_pairs(user))


def profile_has_permission(user: User | None, permission: ActionPermission) -> bool:
    normalized = coerce_user(user)
    if normalized is None:
        return False
    if normalized.role is Role.SUPERADMIN:
        return True
    if normalized.access_profile is None:
        return permission is not ActionPermission.STOCK_TRANSFER_CANCEL
    return permission in PROFILE_PERMISSIONS[normalized.access_profile]


def has_action_permission(
    user: User | None,
    permission: ActionPermission,
    *,
    store_slug: str,
    marketplace: str,
    target_marketplace: str | None = None,
) -> bool:
    normalized = coerce_user(user)
    if normalized is None:
        return False
    if normalized.role is Role.SUPERADMIN:
        return True
    store = store_slug.strip().lower()
    source = marketplace.strip().upper()
    target = str(target_marketplace or "").strip().upper() or None
    if profile_has_permission(normalized, permission) and has_scope(normalized, store, source):
        if permission is not ActionPermission.STOCK_TRANSFER_CROSS_MARKETPLACE:
            return True
        if target and has_scope(normalized, store, target):
            return True

    from app import db

    return db.has_valid_access_grant(
        normalized.id,
        permission.value,
        store,
        source,
        target,
    )


def can_request_scope(user: User | None, store_slug: str, source_marketplace: str) -> bool:
    normalized = coerce_user(user)
    return bool(
        normalized
        and normalized.access_profile is not None
        and has_scope(normalized, store_slug, source_marketplace)
    )
