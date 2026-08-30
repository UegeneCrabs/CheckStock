from datetime import UTC, datetime

from app import auth, db
from app.access_control import (
    ActionPermission,
    accessible_marketplaces,
    has_action_permission,
    has_scope,
)
from app.dto.identity import (
    AccessProfile,
    LoginQuery,
    MarketplaceAccessScope,
    Role,
    SectionAccessLevel,
    SectionName,
    User,
)
from app.section_access import access_level


def _user(
    user_id: int,
    *,
    role: Role = Role.USER,
    profile: AccessProfile | None = AccessProfile.MARKETPLACE_MANAGER,
    scopes: tuple[MarketplaceAccessScope, ...] = (),
) -> User:
    return User(
        id=user_id,
        full_name=f"User {user_id}",
        google_email=f"user{user_id}@example.test",
        login=f"user{user_id}",
        password_hash="hash",
        role=role,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        store_slugs=tuple(dict.fromkeys(scope.store_slug for scope in scopes)),
        access_profile=profile,
        access_scopes=scopes,
    )


def test_marketplace_manager_is_limited_to_exact_store_marketplace_scope(database_path):
    manager = _user(
        10,
        scopes=(MarketplaceAccessScope(store_slug="rimili", marketplace="OZON"),),
    )

    assert accessible_marketplaces(manager, "rimili") == ("OZON",)
    assert has_scope(manager, "rimili", "OZON")
    assert not has_scope(manager, "rimili", "WB")
    assert has_action_permission(
        manager,
        ActionPermission.SALES_VIEW,
        store_slug="rimili",
        marketplace="OZON",
    )
    assert not has_action_permission(
        manager,
        ActionPermission.SALES_VIEW,
        store_slug="rimili",
        marketplace="WB",
    )
    assert access_level(manager, SectionName.EPHEMERIDES) is SectionAccessLevel.NONE
    assert access_level(manager, SectionName.SUPPLY) is SectionAccessLevel.NONE
    assert has_action_permission(
        manager,
        ActionPermission.STOCK_TRANSFER_RECEIVE,
        store_slug="rimili",
        marketplace="OZON",
    )
    assert not has_action_permission(
        manager,
        ActionPermission.STOCK_TRANSFER_CANCEL,
        store_slug="rimili",
        marketplace="OZON",
    )

    senior = manager.model_copy(
        update={"access_profile": AccessProfile.SENIOR_MARKETPLACE_MANAGER}
    )
    assert has_action_permission(
        senior,
        ActionPermission.STOCK_TRANSFER_CANCEL,
        store_slug="rimili",
        marketplace="OZON",
    )
    legacy = manager.model_copy(update={"access_profile": None})
    assert not has_action_permission(
        legacy,
        ActionPermission.STOCK_TRANSFER_CANCEL,
        store_slug="rimili",
        marketplace="OZON",
    )


def test_access_request_creates_seven_day_grant_and_can_be_revoked(database_path):
    requester_id = db.create_user(
        "Manager",
        "manager@example.test",
        "manager",
        "hash",
        "user",
        "2026-08-27T00:00:00+00:00",
        ["rimili"],
    )
    approver_id = db.create_user(
        "Superadmin",
        "admin@example.test",
        "superadmin",
        "hash",
        "superadmin",
        "2026-08-27T00:00:00+00:00",
        ["rimili"],
    )
    created = db.create_access_request(
        user_id=requester_id,
        permission=ActionPermission.STOCK_TRANSFER_CROSS_MARKETPLACE.value,
        store_slug="rimili",
        source_marketplace="OZON",
        target_marketplace="WB",
        reason="Поставка на WB",
        duration_days=30,
    )
    duplicate = db.create_access_request(
        user_id=requester_id,
        permission=ActionPermission.STOCK_TRANSFER_CROSS_MARKETPLACE.value,
        store_slug="rimili",
        source_marketplace="OZON",
        target_marketplace="WB",
        reason="Повтор",
    )
    assert duplicate["id"] == created["id"]

    approved = db.decide_access_request(
        created["id"],
        approved=True,
        decided_by_user_id=approver_id,
    )
    assert approved is not None
    assert approved["status"] == "approved"
    assert approved["duration_days"] == 7
    assert db.has_valid_access_grant(
        requester_id,
        ActionPermission.STOCK_TRANSFER_CROSS_MARKETPLACE.value,
        "rimili",
        "OZON",
        "WB",
    )
    assert db.revoke_access_grant(approved["grant_id"], revoked_by_user_id=approver_id)
    assert not db.has_valid_access_grant(
        requester_id,
        ActionPermission.STOCK_TRANSFER_CROSS_MARKETPLACE.value,
        "rimili",
        "OZON",
        "WB",
    )


def test_scoped_stock_overview_does_not_include_another_marketplace(database_path):
    db.replace_catalog(
        "rimili",
        "WB",
        [{"article": "WB-1", "barcode": "1", "name": "WB item"}],
        "2026-08-27T00:00:00+00:00",
    )
    db.replace_catalog(
        "rimili",
        "OZON",
        [{"article": "OZ-1", "barcode": "2", "name": "Ozon item"}],
        "2026-08-27T00:00:00+00:00",
    )
    db.upsert_mp_stock(
        "rimili", "WB-1", "WB", "fbs", 100, "2026-08-27T00:00:00+00:00"
    )
    db.upsert_mp_stock(
        "rimili", "OZ-1", "OZON", "fbs", 3, "2026-08-27T00:00:00+00:00"
    )

    overview = db.get_stock_overview((("rimili", "OZON"),))
    assert overview["rimili"]["sku_count"] == 1
    assert overview["rimili"]["marketplaces"] == ["OZON"]
    assert overview["rimili"]["total_stock"] == 3


def test_cross_marketplace_transfer_creates_request_before_stock_changes(
    client,
    application,
    monkeypatch,
):
    manager_id = db.create_user(
        "Ozon manager",
        "ozon@example.test",
        "ozon-manager",
        "hash",
        "user",
        "2026-08-27T00:00:00+00:00",
        ["rimili"],
    )
    manager = _user(
        manager_id,
        scopes=(MarketplaceAccessScope(store_slug="rimili", marketplace="OZON"),),
    )
    monkeypatch.setattr(
        application.state.container.identity,
        "user_for_token",
        lambda _token: manager,
    )
    client.cookies.set(auth.SESSION_COOKIE, "test-session-token")

    response = client.post(
        "/stock/rimili/transfer",
        data={
            "from_fulfillment": "FF",
            "from_marketplace": "OZON",
            "to_fulfillment": "FF",
            "to_marketplace": "WB",
            "note": "Нужно передать товар на WB",
            "items": '[{"article":"A-1","quantity":1}]',
        },
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 403
    assert response.json()["approval_required"] is True
    requests = db.list_access_requests("pending")
    assert len(requests) == 1
    assert requests[0]["permission"] == ActionPermission.STOCK_TRANSFER_CROSS_MARKETPLACE.value
    assert requests[0]["source_marketplace"] == "OZON"
    assert requests[0]["target_marketplace"] == "WB"
    assert db.get_store_operations("rimili") == []


def test_superadmin_can_create_marketplace_manager_with_scoped_access(
    client,
    application,
    monkeypatch,
):
    superadmin = _user(1, role=Role.SUPERADMIN, profile=None)
    monkeypatch.setattr(
        application.state.container.identity,
        "user_for_token",
        lambda _token: superadmin,
    )
    client.cookies.set(auth.SESSION_COOKIE, "test-session-token")

    admin_page = client.get("/admin")
    assert admin_page.status_code == 200
    assert "Логин: латинские буквы" in admin_page.text

    invalid_response = client.post(
        "/admin/users",
        data={
            "full_name": "Test User",
            "google_email": "test@example.test",
            "login": "фффф",
            "password": "Strong-password-123",
            "role": "user",
            "access_profile": "marketplace_manager",
            "stores": "rimili",
            "marketplaces": "OZON",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert invalid_response.status_code == 400
    assert invalid_response.json()["field"] == "login"
    assert invalid_response.json()["error"].startswith("Логин:")

    response = client.post(
        "/admin/users",
        data={
            "full_name": "Ozon Manager",
            "google_email": "ozon.manager@example.test",
            "login": "ozon.manager",
            "password": "Strong-password-123",
            "role": "user",
            "access_profile": "marketplace_manager",
            "stores": ["rimili", "tris"],
            "marketplaces": "OZON",
        },
        headers={"X-Requested-With": "fetch"},
    )

    assert response.status_code == 200
    created = application.state.container.identity.get_user_by_login(
        LoginQuery(login="ozon.manager")
    )
    assert created is not None
    assert created.access_profile is AccessProfile.MARKETPLACE_MANAGER
    assert {(scope.store_slug, scope.marketplace) for scope in created.access_scopes} == {
        ("rimili", "OZON"),
        ("tris", "OZON"),
    }
