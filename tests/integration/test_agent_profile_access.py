from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.agents.access import AgentCredential, create_credential
from app.dto.identity import Role
from app.main import create_app
from app.web.routers import agent_analytics as api


@pytest.mark.parametrize("profile", ["senior_marketplace_manager", "marketplace_lead", "store_manager"])
def test_profile_economics_access_includes_other_managers(
    agent_client, database_path, monkeypatch, user_factory, profile
):
    from fastapi.responses import JSONResponse

    from app.dto.identity import AccessProfile, MarketplaceAccessScope
    from app.web.routers import agent_full, unit_economics

    client, identity, *_ = agent_client
    user = user_factory(role=Role.USER, stores=("rimili",)).model_copy(
        update={
            "access_profile": AccessProfile(profile),
            "access_scopes": (MarketplaceAccessScope(store_slug="rimili", marketplace="WB"),),
        }
    )
    identity.get_user.return_value = user
    references = [
        {"store_slug": "rimili", "article": "a", "manager": "Other Person", "purchase_price": 50},
        {"store_slug": "rimili", "article": "b", "manager": "", "purchase_price": 30},
    ]
    monkeypatch.setattr(
        agent_full.db, "get_unit_economics_1c_product_reference_rows", lambda *args: references
    )
    products = [
        {"store_slug": "rimili", "article": a, "name": a, "current_economics": {"roi": 10}, "price": {}}
        for a in ("a", "b")
    ]
    monkeypatch.setattr(
        unit_economics,
        "sales_unit_economics_1c",
        AsyncMock(return_value=JSONResponse({"products": products})),
    )
    response = client.get(f"{api.PREFIX}/current-economics", params={"store": "rimili", "limit": 1})
    assert response.status_code == 200, response.text
    assert response.json()["total_rows"] == 2
    assert response.json()["next_offset"] == 1
    body = client.get(f"{api.PREFIX}/costs", params={"store": "rimili"}).json()
    assert {r["article"] for r in body["rows"]} == {"a", "b"}
    body = client.get(f"{api.PREFIX}/costs", params={"store": "rimili", "manager": "Other Person"}).json()
    assert [r["article"] for r in body["rows"]] == ["a"]
    assert client.get(f"{api.PREFIX}/costs", params={"store": "tris"}).status_code == 200
    assert (
        client.get(f"{api.PREFIX}/stocks", params={"store": "rimili", "marketplace": "OZON"}).status_code
        == 200
    )
    monkeypatch.setattr(unit_economics.db, "get_stock_items", lambda *args: products)
    options = unit_economics._unit_profit_report_filter_options(("rimili",), user)
    assert options["manager_scope"]["restricted"] is False
    assert {r["article"] for r in options["filters"]["articles"]} == {"a", "b"}


@pytest.mark.parametrize("profile", ["marketplace_manager", "procurement"])
def test_profile_without_website_economics_has_agent_access(
    agent_client, database_path, user_factory, profile
):
    from app.dto.identity import AccessProfile, MarketplaceAccessScope

    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(role=Role.USER).model_copy(
        update={
            "access_profile": AccessProfile(profile),
            "access_scopes": (MarketplaceAccessScope(store_slug="rimili", marketplace="WB"),),
        }
    )
    assert client.get(f"{api.PREFIX}/costs", params={"store": "rimili"}).status_code == 200


def test_campaign_ctr_filter_pagination_and_manager_scope(
    agent_client, database_path, monkeypatch, user_factory
):
    from app.economics.wb import campaigns as advertising_campaigns
    from app.web.routers import agent_full

    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(user_id=7, role=Role.USER, stores=("rimili",))
    monkeypatch.setattr(
        advertising_campaigns,
        "report",
        lambda query: (
            [
                {
                    "article": "1",
                    "campaign_id": "10",
                    "ctr_percent": 3,
                    "spend": 2,
                    "impressions": 100,
                    "clicks": 3,
                },
                {
                    "article": "2",
                    "campaign_id": "20",
                    "ctr_percent": 1,
                    "spend": 999,
                    "impressions": 100,
                    "clicks": 1,
                },
                {
                    "article": "3",
                    "campaign_id": "30",
                    "ctr_percent": 5,
                    "spend": 3,
                    "impressions": 100,
                    "clicks": 5,
                },
                {
                    "article": "4",
                    "campaign_id": "40",
                    "ctr_percent": None,
                    "spend": None,
                    "impressions": None,
                    "clicks": None,
                },
            ],
            {"available": True},
        ),
    )
    monkeypatch.setattr(
        agent_full.reports, "references", lambda *args: [{"article": a} for a in ("1", "3", "4")]
    )
    monkeypatch.setattr(
        agent_full.db,
        "get_unit_economics_1c_product_reference_rows",
        lambda *args: [{"article": str(a)} for a in range(1, 5)],
    )
    monkeypatch.setattr(
        agent_full.reports, "catalog", lambda *args: [{"article": "1", "name": "Товар из базы"}]
    )
    params = {
        "store": "rimili",
        "date_from": "2026-09-09",
        "date_to": "2026-09-09",
        "manager": "Selected Manager",
        "ctr_below": 5,
        "limit": 1,
        "sort_by": "ctr_percent",
    }
    response = client.get(f"{api.PREFIX}/advertising-campaigns", params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_rows"] == 1
    assert data["rows"][0]["name"] == "Товар из базы"
    assert data["totals"]["spend"] == 2
    assert (
        client.get(f"{api.PREFIX}/advertising-campaigns", params=params | {"store": "tris"}).status_code
        == 200
    )
    assert (
        client.get(f"{api.PREFIX}/advertising-campaigns", params=params | {"ctr_below": -1}).status_code
        == 422
    )


def test_agent_manager_filter_is_explicit(agent_client, database_path, monkeypatch, user_factory):
    from app.agents import reports as agent_reports

    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(user_id=7, role=Role.USER, stores=("rimili",))
    monkeypatch.setattr(
        agent_reports.db,
        "get_unit_economics_1c_product_reference_rows",
        lambda stores: [
            {"article": "a", "manager": "User 7", "purchase_price": 50},
            {"article": "b", "manager": "User 8", "purchase_price": 100},
        ],
    )
    body = client.get(f"{api.PREFIX}/costs", params={"store": "rimili"}).json()
    assert {r["article"] for r in body["rows"]} == {"a", "b"}
    body = client.get(f"{api.PREFIX}/costs", params={"store": "rimili", "manager": "User 8"}).json()
    assert [r["article"] for r in body["rows"]] == ["b"]


@pytest.fixture
def agent_client(tmp_path, monkeypatch, user_factory):
    token, record = create_credential(7, datetime.now(UTC) + timedelta(days=1))
    path = tmp_path / "keys.json"
    path.write_bytes(TypeAdapter(list[AgentCredential]).dump_json([record]))
    monkeypatch.setattr(api, "settings", api.settings.model_copy(update={"agent_tokens_path": path}))
    identity = Mock()
    identity.get_user.return_value = user_factory(user_id=7)
    app = create_app(SimpleNamespace(identity=identity))
    # No lifespan: never start external integrations during tests.
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {token}"
    return client, identity, path, token, record


@pytest.mark.parametrize(
    "profile",
    [
        None,
        "marketplace_manager",
        "procurement",
        "store_manager",
        "marketplace_lead",
        "senior_marketplace_manager",
    ],
)
def test_company_access_is_request_local(agent_client, database_path, monkeypatch, user_factory, profile):
    from app.agents import reports
    from app.core.stores import STORES
    from app.dto.identity import AccessProfile, MarketplaceAccessScope, SectionAccessLevel, SectionName

    client, identity, *_ = agent_client
    original = user_factory(role=Role.USER, stores=("rimili",)).model_copy(
        update={
            "access_profile": AccessProfile(profile) if profile else None,
            "access_scopes": (MarketplaceAccessScope(store_slug="rimili", marketplace="WB"),),
            "section_access": {section: SectionAccessLevel.NONE for section in SectionName},
        }
    )
    identity.get_user.return_value = original
    identity.user_for_token.return_value = original
    from app.access import auth

    client.cookies.set(auth.SESSION_COOKIE, "test-browser-session")
    before = original.model_dump()
    response = client.get(f"{api.PREFIX}/stores")
    assert response.status_code == 200
    assert {s["slug"] for s in response.json()} == set(STORES)
    monkeypatch.setattr(
        reports.db,
        "get_unit_economics_1c_product_reference_rows",
        lambda *args: [{"article": "a", "manager": "Someone else"}],
    )
    assert client.get(f"{api.PREFIX}/costs", params={"store": "tris"}).json()["total_rows"] == 1
    assert original.model_dump() == before
    assert (
        client.get(
            "/api/unit-economics-1c/reports/unit-profit", headers={"Accept": "application/json"}
        ).status_code
        == 403
    )
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)(f"{api.PREFIX}/stores").status_code == 405


@pytest.mark.parametrize("failure", ["missing", "invalid", "expired", "revoked", "inactive", "deleted"])
def test_company_access_still_requires_valid_employee_key(agent_client, user_factory, failure):
    client, identity, path, token, record = agent_client
    if failure == "missing":
        client.headers.pop("Authorization")
    elif failure == "invalid":
        client.headers["Authorization"] = "Bearer invalid"
    elif failure == "revoked":
        path.write_text("[]", encoding="utf-8")
    elif failure == "expired":
        record = record.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(days=1)})
        path.write_bytes(TypeAdapter(list[AgentCredential]).dump_json([record]))
    elif failure == "inactive":
        identity.get_user.return_value = user_factory(active=False)
    elif failure == "deleted":
        identity.get_user.return_value = None
    assert client.get(f"{api.PREFIX}/stores").status_code == 401
