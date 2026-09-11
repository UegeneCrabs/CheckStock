from unittest.mock import patch

from fastapi.responses import Response
from fastapi.testclient import TestClient
from starlette.datastructures import FormData

from app import auth, db
from app.access_control import scope_pairs
from app.dto.identity import AccessProfile, MarketplaceAccessScope, Role, UserId
from app.dto.identity import SectionAccessLevel as L
from app.dto.identity import SectionName as S
from app.section_access import (
    SECTION_GROUPS,
    SECTION_PATHS,
    access_level,
    has_access,
    landing_path,
    section_for_path,
)
from app.web.routers import admin
from app.web.routers.agent_full import report_permitted


def test_tab_overrides_are_independent_and_keep_legacy_defaults(user_factory):
    user = user_factory(role=Role.USER).model_copy(update={'section_access': {
        S.STOCK: L.READ, S.UNIT_ECONOMICS_1C: L.NONE,
        S.STOCK_SUPPLIES: L.WRITE, S.REPORT_TARGET_PRICE: L.READ,
    }})
    assert access_level(user, S.STOCK_BALANCES) is L.READ
    assert access_level(user, S.STOCK_SUPPLIES) is L.WRITE
    assert access_level(user, S.UNIT_ECONOMICS_WB) is L.NONE
    assert access_level(user, S.REPORT_TARGET_PRICE) is L.READ
    assert not auth.can_edit_stock(user)
    user = user.model_copy(update={'section_access': {S.STOCK: L.NONE, S.UNIT_ECONOMICS_1C: L.NONE, S.REPORT_TARGET_PRICE: L.READ}})
    assert landing_path(user) == SECTION_PATHS[S.REPORT_TARGET_PRICE]
    assert report_permitted(user, 'target-prices', 'rimili', 'WB')
    assert not report_permitted(user, 'prices', 'rimili', 'WB')


def test_job_and_marketplace_caps_apply_to_overrides(user_factory):
    user = user_factory(role=Role.USER).model_copy(update={
        'access_profile': AccessProfile.SENIOR_MARKETPLACE_MANAGER,
        'access_scopes': (MarketplaceAccessScope(store_slug='rimili', marketplace='WB'),),
        'section_access': {S.STOCK: L.NONE, S.UNIT_ECONOMICS_1C: L.NONE, S.UNIT_ECONOMICS_WB: L.WRITE},
    })
    assert access_level(user, S.STOCK_BALANCES) is L.WRITE
    assert access_level(user, S.UNIT_ECONOMICS_WB) is L.READ
    assert access_level(user, S.UNIT_ECONOMICS_YANDEX) is L.NONE
    assert access_level(user, S.ADMIN_USERS) is L.NONE
    manual = user.model_copy(update={'access_profile': None, 'section_access': {S.UNIT_ECONOMICS_YANDEX: L.WRITE}})
    assert not has_access(manual, S.UNIT_ECONOMICS_YANDEX)
    assert scope_pairs(manual) == (('rimili', 'WB'),)


def test_every_tab_is_checked_before_handler_execution(application, user_factory):
    paths = {section: SECTION_PATHS[section] + '/_permission_probe' for _, group in SECTION_GROUPS for section in group}
    paths[S.STOCK_BALANCES] = '/stock/rimili/_permission_probe'
    paths[S.STOCK_OPERATIONS] = '/stock/rimili/operations/_permission_probe'
    reached = []

    async def probe():
        reached.append(True)
        return Response(status_code=204)

    for path in paths.values():
        application.add_api_route(path, probe, methods=['GET', 'POST'])
    user = user_factory(role=Role.ADMIN)
    with TestClient(application) as client, patch.object(application.state.container.identity, 'user_for_token') as lookup:
        client.cookies.set(auth.SESSION_COOKIE, 'tab-permissions')
        for section, path in paths.items():
            assert section_for_path(path) is section
            reached.clear()
            lookup.return_value = user.model_copy(update={'section_access': {section: L.NONE}})
            assert client.get(path, headers={'accept': 'application/json'}).status_code == 403, section
            assert client.post(path).status_code == 403, section
            assert not reached
            lookup.return_value = user.model_copy(update={'section_access': {section: L.READ}})
            if has_access(lookup.return_value, section):
                assert client.get(path).status_code == 204, section
                reached.clear()
                assert client.post(path).status_code == 403, section
                assert not reached


def test_report_and_yandex_api_paths_use_their_own_permissions():
    cases = {
        '/api/unit-economics-1c/reports/target-price.xlsx': S.REPORT_TARGET_PRICE,
        '/api/unit-economics-1c/reports/target-price/targets': S.REPORT_TARGET_PRICE,
        '/api/unit-economics-1c/reports/unit-profit.xlsx': S.REPORT_UNIT_PROFIT,
        '/api/unit-economics-1c/yandex-market/calculate/price': S.UNIT_ECONOMICS_YANDEX,
        '/stock/planning/wb/manual/1': S.STOCK_SUPPLIES,
        '/api/admin/integrations/status': S.ADMIN_INTEGRATIONS,
    }
    for path, section in cases.items():
        assert section_for_path(path) is section


def test_partial_save_and_restore_inheritance_persist_without_changing_siblings(client, monkeypatch, user_factory):
    monkeypatch.setattr(client.app.state.container.identity, 'user_for_token', lambda _: user_factory())
    client.cookies.set(auth.SESSION_COOKIE, 'tab-permissions')
    db.create_user('Admin', 'admin@test', 'admin', 'hash', 'superadmin', '2026-08-12T10:00:00+00:00', ['rimili'])
    target = db.create_user('Target', 'tabs@test', 'tabs', 'hash', 'user', '2026-08-12T10:00:00+00:00', ['rimili'])
    endpoint = f'/admin/users/{target}/sections'
    for payload in ({'stock': 'read', 'unit_economics_1c': 'none'}, {'stock_supplies': 'write', 'report_target_price': 'read'}):
        response = client.post(endpoint, data=payload)
        assert response.status_code == 200, response.text
    saved = client.app.state.container.identity.get_user(UserId(target))
    assert access_level(saved, S.STOCK_BALANCES) is L.READ
    assert access_level(saved, S.STOCK_SUPPLIES) is L.WRITE
    assert access_level(saved, S.REPORT_TARGET_PRICE) is L.READ
    assert client.post(endpoint, data={'stock_supplies': ''}).status_code == 200
    saved = client.app.state.container.identity.get_user(UserId(target))
    assert S.STOCK_SUPPLIES not in saved.section_access
    assert access_level(saved, S.STOCK_SUPPLIES) is L.READ
    assert saved.section_access[S.REPORT_TARGET_PRICE] is L.READ
    assert client.post(endpoint, data={'admin_users': 'write'}).status_code == 400
    assert client.post(endpoint, data={'admin_integrations': 'write'}).status_code == 400


def test_manual_scope_is_saved_and_store_changes_keep_marketplace_limits(client, monkeypatch, user_factory):
    monkeypatch.setattr(client.app.state.container.identity, 'user_for_token', lambda _: user_factory())
    client.cookies.set(auth.SESSION_COOKIE, 'tab-permissions')
    db.create_user('Admin', 'admin@test', 'admin', 'hash', 'superadmin', '2026-08-12T10:00:00+00:00', ['rimili'])
    target = db.create_user('Target', 'scope@test', 'scope', 'hash', 'user', '2026-08-12T10:00:00+00:00', ['rimili'])
    response = client.post(f'/admin/users/{target}/access-policy', data={'access_profile': '', 'scope_version': '2', 'stores': 'rimili', 'marketplaces': 'OZON'})
    assert response.status_code == 200, response.text
    saved = client.app.state.container.identity.get_user(UserId(target))
    assert saved.access_profile is None
    assert scope_pairs(saved) == (('rimili', 'OZON'),)
    assert not has_access(saved, S.UNIT_ECONOMICS_WB)
    assert client.post(f'/admin/users/{target}/stores', data={'stores': 'tris'}).status_code == 200
    saved = client.app.state.container.identity.get_user(UserId(target))
    assert scope_pairs(saved) == (('tris', 'OZON'),)


def test_manual_empty_marketplace_selection_is_not_expanded_to_all(user_factory):
    result = admin._access_policy_from_form(user_factory(), FormData({'access_profile': '', 'scope_version': '2', 'stores': 'rimili'}))
    assert result[3]
    legacy = admin._access_policy_from_form(user_factory(), FormData({'access_profile': '', 'stores': 'rimili'}))
    assert legacy[3] is None
    assert len(legacy[2]) > 1


def test_scoped_admin_cannot_assign_or_manage_outside_their_marketplace(user_factory):
    actor = user_factory(role=Role.ADMIN, stores=('rimili',)).model_copy(update={'access_scopes': (MarketplaceAccessScope(store_slug='rimili', marketplace='WB'),)})
    target = user_factory(user_id=2, role=Role.USER, stores=('rimili',))
    assert not admin.can_manage_user(actor, target)
    result = admin._access_policy_from_form(actor, FormData({'stores': 'rimili', 'marketplaces': 'OZON', 'scope_version': '2'}))
    assert result[3]
