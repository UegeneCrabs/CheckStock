from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app.agent_access import AgentCredential, create_credential, resolve_credential
from app.dto.identity import Role, SectionAccessLevel, SectionName, UserId
from app.main import create_app
from app.stores import STORES
from app.web.routers import agent_analytics as api

pytestmark = pytest.mark.integration


def test_profit_uses_website_report_and_keeps_orders_with_partial_margin(agent_client, monkeypatch):
    from app.web.routers import agent_full

    client, *_ = agent_client
    rows = [dict(article=str(i), name="Product", manager=None, orders_count=i,
                 orders_amount=i * 100, cancel_count=1, cancel_amount=10,
                 net_orders_count=i - 1, net_orders_amount=i * 100 - 10,
                 advertising_spend=5, drr=17.3, ctr=4.2, margin=42, roi=12, margin_complete=False,
                 margin_missing_days=["2026-09-09"]) for i in range(1, 62)]
    loader = AsyncMock(return_value={"rows": rows, "period_from": "2026-09-09",
                                     "period_to": "2026-09-09"})
    monkeypatch.setattr(agent_full, "_unit_economics_1c_unit_profit_report_data", loader)
    monkeypatch.setattr(agent_full.reports, "economic_filter", lambda rows, *args: rows)
    response = client.get(f"{api.PREFIX}/profit", params={"store": "rimili",
                          "date_from": "2026-09-09", "date_to": "2026-09-09",
                          "sort_by": "orders_amount", "limit": 1})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_rows"] == 61
    assert data["rows"][0]["article"] == "61"
    assert data["rows"][0]["orders_amount"] == 6100
    assert data["rows"][0]["net_orders_amount"] == 6090
    assert data["rows"][0]["margin"] is None
    assert data["rows"][0]["report_margin"] == 42
    assert data["rows"][0]["report_roi"] == 12
    assert data["rows"][0]["drr"] == 17.3
    assert data["rows"][0]["ctr"] == 4.2
    assert data["context"]["field_labels"]["drr"] == "ДРР с выкупом, %"
    assert data["totals"]["orders_amount"] == sum(i * 100 for i in range(1, 62))
    assert data["context"]["report"] == "unit-profit"
    request = loader.call_args.args[0]
    assert request.query_params["store"] == "rimili"
    assert request.query_params["date_from"] == "2026-09-09"


def test_article_store_lookup(agent_client, monkeypatch, user_factory):
    from app import agent_reports
    from app.web.routers import agent_full
    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(role=Role.ADMIN, stores=("rimili", "tris"))
    monkeypatch.setattr(agent_full, "scope_pairs", lambda user: [("rimili", "WB"), ("tris", "WB"), ("rimili", "OZON")])
    catalogs = {("rimili", "WB"): [{"article": "123 / S"}, {"article": "123 / M"}],
                ("tris", "WB"): [{"article": "123"}], ("rimili", "OZON"): [{"article": "1234"}]}
    loader = Mock(side_effect=lambda store, mp: catalogs[(store, mp)])
    monkeypatch.setattr(agent_reports, "catalog", loader)
    url = f"{api.PREFIX}/article-stores"
    body = client.get(url, params={"article": " 123 "}).json()
    assert body["status"] == "ambiguous" and len(body["matches"]) == 2
    assert {m["store"] for m in body["matches"]} == {"rimili", "tris"}
    assert client.get(url, params={"article": "12"}).json()["status"] == "not_found"
    body = client.get(url, params={"article": "123 / S", "marketplace": "WB"}).json()
    assert body["status"] == "found" and body["matches"][0]["store"] == "rimili"
    assert client.get(url, params={"article": "123", "marketplace": "OZON"}).json()["status"] == "not_found"
    monkeypatch.setattr(agent_full, "scope_pairs", lambda user: [("rimili", "WB")])
    loader.reset_mock()
    assert client.get(url, params={"article": "123"}).json()["status"] == "found"
    loader.assert_called_once_with("rimili", "WB")
    assert client.get(url, params={"article": " "}).status_code == 422
    assert client.get(url).status_code == 422


def test_article_lookup_economic_only_manager_scope(agent_client, monkeypatch, user_factory):
    from app import agent_reports
    from app.web.routers import agent_full
    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(role=Role.USER, stores=("rimili",))
    monkeypatch.setattr(agent_full, "scope_pairs", lambda user: [("rimili", "WB")])
    monkeypatch.setattr(agent_full, "permitted", lambda user, section, store, mp: section == SectionName.UNIT_ECONOMICS_1C)
    monkeypatch.setattr(agent_reports, "economic_filter", lambda rows, user, store: [])
    loader = Mock(return_value=[{"article": "123"}])
    monkeypatch.setattr(agent_reports, "catalog", loader)
    body = client.get(f"{api.PREFIX}/article-stores", params={"article": "123"}).json()
    assert body["matches"] == [] and body["status"] == "not_found"
    loader.assert_not_called()


def test_calculator_requires_article_and_respects_scope(agent_client, database_path, user_factory):
    client, identity, *_ = agent_client
    assert client.get(f"{api.PREFIX}/profit-calculator", params={"store": "rimili"}).status_code == 422
    identity.get_user.return_value = user_factory(user_id=7, role=Role.USER, stores=("rimili",))
    assert client.get(f"{api.PREFIX}/profit-calculator", params={"store": "rimili", "article": "unknown"}).status_code == 404
    assert client.get(f"{api.PREFIX}/profit-calculator", params={"store": "tris", "article": "unknown"}).status_code == 403
    assert client.get(f"{api.PREFIX}/profit-calculator", params={"store": "rimili", "article": "unknown", "marketplace": "OZON"}).status_code == 422


def test_calculator_uses_product_and_target_price(agent_client, monkeypatch):
    from fastapi.responses import JSONResponse

    from app.web.routers import agent_full, unit_economics
    client, *_ = agent_client
    loader = AsyncMock(return_value=JSONResponse({"product": {"article": "a", "store_slug": "rimili", "price": {"current": 100}}}))
    monkeypatch.setattr(unit_economics, "sales_unit_economics_1c", loader)
    monkeypatch.setattr(agent_full, "target_price_data", AsyncMock(return_value={"rows": [{"store_slug": "rimili", "article": "a", "target_price": 120}]}))
    response = client.get(f"{api.PREFIX}/profit-calculator", params={"store": "rimili", "article": "a"})
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["results"]["target_price"] == 120
    assert row["results"]["roi"] is None
    assert row["inputs"]["retail"] == 100
    assert loader.call_args.args[0].query_params["article"] == "a"


def test_calculator_auto_store_and_ambiguity(agent_client, monkeypatch):
    from fastapi.responses import JSONResponse

    from app.web.routers import agent_full, unit_economics
    client, *_ = agent_client
    match = agent_full.ArticleStoreMatch(store="tris", marketplace="WB", article="218036121")
    lookup = AsyncMock(return_value=agent_full.ArticleLookupResponse(
        article=match.article, status="found", matches=[match], warnings=[]))
    monkeypatch.setattr(agent_full, "article_stores", lookup)
    loader = AsyncMock(return_value=JSONResponse({"product": {"article": match.article, "store_slug": "tris", "price": {"current": 100}}}))
    monkeypatch.setattr(unit_economics, "sales_unit_economics_1c", loader)
    monkeypatch.setattr(agent_full, "target_price_data", AsyncMock(return_value={"rows": []}))
    url = f"{api.PREFIX}/profit-calculator"
    response = client.get(url, params={"article": match.article})
    assert response.status_code == 200, response.text
    assert response.json()["store"] == "tris"
    assert loader.call_args.args[0].query_params["store"] == "tris"
    loader.reset_mock()
    lookup.return_value = agent_full.ArticleLookupResponse(article=match.article, status="ambiguous",
        matches=[match, match.model_copy(update={"store": "rimili"})], warnings=[])
    body = client.get(url, params={"article": match.article}).json()
    assert body["store"] is None and body["rows"] == []
    assert len(body["context"]["store_resolution"]["matches"]) == 2
    loader.assert_not_called()
    lookup.return_value = agent_full.ArticleLookupResponse(article=match.article, status="not_found", matches=[], warnings=[])
    assert client.get(url, params={"article": match.article}).json()["context"]["store_resolution"]["status"] == "not_found"
    loader.assert_not_called()
    lookup.reset_mock()
    assert client.get(url, params={"article": match.article, "store": "tris"}).status_code == 200
    lookup.assert_not_called()


def test_shared_card_does_not_expose_other_managers(agent_client, database_path, monkeypatch, user_factory):
    from app import agent_reports
    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(user_id=7, role=Role.USER, stores=("rimili",))
    monkeypatch.setattr(agent_reports.db, "get_unit_economics_1c_product_reference_rows", lambda stores: [
        {"article": "123 / S", "manager": "User 7"},
        {"article": "123 / M", "manager": "User 8"},
        {"article": "456 / S", "manager": "User 7"}])
    monkeypatch.setattr(agent_reports, "advertising", lambda q: [
        {"article": "123", "spend": 1000, "impressions": 100, "clicks": 10},
        {"article": "456", "spend": 20, "impressions": 100, "clicks": 2}])
    body = client.get(f"{api.PREFIX}/advertising", params=params()).json()
    assert [r["article"] for r in body["rows"]] == ["456"]
    assert body["totals"]["spend"] == 20


def test_catalog_does_not_mix_marketplaces(agent_client, database_path):
    from app.repositories.core import get_connection
    client, *_ = agent_client
    connection = get_connection()
    try:
        connection.execute("INSERT INTO stock_items(store_slug,marketplace,article,barcode,name) VALUES ('rimili','WB','wb-item','wb-code','WB item'),('rimili','OZON','oz-item','oz-code','Ozon item')")
        connection.commit()
    finally:
        connection.close()
    response = client.get(f"{api.PREFIX}/products", params={"store": "rimili", "marketplace": "OZON"})
    assert response.status_code == 200
    assert [r["article"] for r in response.json()["rows"]] == ["oz-item"]
    response = client.get(f"{api.PREFIX}/product-details", params={"store": "rimili", "article": "oz-item"})
    assert response.status_code == 404


def test_store_case_is_normalized_before_access_check(agent_client, database_path, user_factory):
    from app.web.routers.agent_full import ReportQuery

    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(role=Role.ADMIN, stores=("rimili",))
    assert ReportQuery(store=" RIMILI ").store == "rimili"
    assert ReportQuery().store is None
    assert api.LossQuery(store=" RIMILI ", date_from="2026-09-01", date_to="2026-09-07").store == "rimili"
    url = f"{api.PREFIX}/products"
    assert client.get(url, params={"store": "RIMILI"}).json()["store"] == "rimili"
    assert client.get(url, params={"store": "TRIS"}).status_code == 403
    assert client.get(url, params={"store": "UNKNOWN"}).status_code == 403


def test_action_schema_only_advertises_supported_filters(agent_client):
    from app.web.routers.agent_full import PERIOD_REPORTS

    client, *_ = agent_client
    paths = client.get(f"{api.PREFIX}/openapi.json").json()["paths"]
    params = {p["name"] for p in paths[f"{api.PREFIX}/products"]["get"]["parameters"]}
    assert "search" in params and "date_from" not in params
    assert {p["name"] for p in paths[f"{api.PREFIX}/data-status"]["get"]["parameters"]} == {"store", "marketplace"}
    calculator_params = {p["name"]: p for p in paths[f"{api.PREFIX}/profit-calculator"]["get"]["parameters"]}
    assert set(calculator_params) == {"store", "marketplace", "article"}
    assert calculator_params["article"]["required"] is True
    assert calculator_params["article"]["schema"]["type"] == "string"
    rnp_params = {p["name"]: p for p in paths[f"{api.PREFIX}/rnp"]["get"]["parameters"]}
    assert rnp_params["limit"]["schema"]["maximum"] == 20
    assert client.get(f"{api.PREFIX}/rnp", params={"store": "rimili", "month": "2026-09", "limit": 21}).status_code == 422
    for report in PERIOD_REPORTS:
        parameters = {p["name"]: p for p in paths[f"{api.PREFIX}/{report}"]["get"]["parameters"]}
        for field in ("date_from", "date_to"):
            assert parameters[field]["required"] is True
            assert parameters[field]["schema"]["type"] == "string"
            assert parameters[field]["schema"]["format"] == "date"
        assert client.get(f"{api.PREFIX}/{report}", params={"store": "rimili"}).status_code == 422
    for path in paths.values():
        for parameter in path["get"].get("parameters", []):
            assert "anyOf" not in parameter["schema"]
            assert parameter["schema"].get("type") != "null"
    assert client.get(f"{api.PREFIX}/profit-calculator", params={"store": "rimili", "article": "856546716", "sort_by": "unit_profit"}).status_code == 422


def test_capabilities_includes_loss_report_only_with_economic_scope(agent_client, user_factory):
    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(role=Role.ADMIN, stores=("rimili",))
    reports = client.get(f"{api.PREFIX}/capabilities").json()["reports"]
    loss = next(report for report in reports if report["report"] == "loss-products")
    assert loss["scopes"] == [{"store": "rimili", "marketplace": "WB"}]
    assert set(loss["filters"]) == {"store", "article", "date_from", "date_to", "limit"}
    identity.get_user.return_value = user_factory(role=Role.USER, stores=("rimili",)).model_copy(
        update={"section_access": {**{s: SectionAccessLevel.NONE for s in SectionName},
                                   SectionName.STOCK: SectionAccessLevel.READ}}
    )
    reports = client.get(f"{api.PREFIX}/capabilities").json()["reports"]
    assert all(report["report"] != "loss-products" for report in reports)


@pytest.mark.parametrize("report", ["product-newness", "product-reputation", "product-tags", "current-economics", "products", "sales", "funnel", "advertising", "stocks", "stock-value",
    "stock-operations", "prices", "costs", "profit", "target-prices", "rnp", "decisions", "data-status"])
def test_full_reports_real_empty_database(agent_client, database_path, user_factory, report):
    from app.web.routers.agent_full import PERIOD_REPORTS
    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory()
    query = {"store": "rimili"}
    if report in PERIOD_REPORTS:
        query.update(date_from="2026-09-01", date_to="2026-09-07")
    if report == "rnp":
        query["month"] = "2026-09"
    response = client.get(f"{api.PREFIX}/{report}", params=query)
    assert response.status_code == 200, response.text
    assert "password" not in response.text and "raw_json" not in response.text


def test_stock_only_user_cannot_read_costs(agent_client, database_path, user_factory):
    client, identity, *_ = agent_client
    user = user_factory(role=Role.ADMIN, stores=("rimili",))
    identity.get_user.return_value = user.model_copy(update={"section_access": {
        section: SectionAccessLevel.READ if section == SectionName.STOCK else SectionAccessLevel.NONE
        for section in SectionName}})
    assert client.get(f"{api.PREFIX}/stocks", params={"store": "rimili"}).status_code == 200
    for path in ("costs", "prices", "advertising", "profit", "stock-value", "current-economics", "product-tags", "product-newness", "product-reputation"):
        query = {"store": "rimili"}
        if path in ("advertising", "profit"):
            query.update(date_from="2026-09-01", date_to="2026-09-07")
        assert client.get(f"{api.PREFIX}/{path}", params=query).status_code == 403
    for path in ("products", "stocks", "data-status"):
        assert client.get(f"{api.PREFIX}/{path}", params={"store": "tris"}).status_code == 403


def test_report_aggregates_before_pagination(agent_client, database_path, monkeypatch):
    from app import agent_reports
    client, *_ = agent_client
    monkeypatch.setattr(agent_reports, "advertising", lambda q: [
        {"article": "a", "day": "2026-09-01", "spend": 10, "impressions": 100, "clicks": 2},
        {"article": "a", "day": "2026-09-02", "spend": 15, "impressions": 100, "clicks": 3},
        {"article": "b", "day": "2026-09-01", "spend": 20, "impressions": 100, "clicks": 1}])
    response = client.get(f"{api.PREFIX}/advertising", params=params(limit=1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"][0]["article"] == "a"
    assert body["rows"][0]["spend"] == 25
    assert body["totals"]["spend"] == 45
    assert body["next_offset"] == 1
    assert body["total_rows"] == 2


def test_economic_manager_scope_before_totals(agent_client, database_path, monkeypatch, user_factory):
    from app import agent_reports
    client, identity, *_ = agent_client
    identity.get_user.return_value = user_factory(user_id=7, role=Role.USER, stores=("rimili",))
    monkeypatch.setattr(agent_reports.db, "get_unit_economics_1c_product_reference_rows", lambda stores: [
        {"article": "a", "manager": "User 7", "purchase_price": 50},
        {"article": "b", "manager": "User 8", "purchase_price": 100}])
    body = client.get(f"{api.PREFIX}/costs", params={"store": "rimili"}).json()
    assert [r["article"] for r in body["rows"]] == ["a"]
    body = client.get(f"{api.PREFIX}/costs", params={"store": "rimili", "manager": "User 8"}).json()
    assert body["rows"] == []


def test_warehouse_stock_is_not_double_counted(agent_client, database_path, monkeypatch):
    from app import agent_reports
    from app.repositories.core import get_connection
    client, *_ = agent_client
    conn = get_connection()
    try:
        conn.execute("INSERT INTO mp_stock(store_slug,article,marketplace,scheme,quantity) VALUES ('rimili','a','WB','fbo',10)")
        conn.execute("INSERT INTO mp_warehouse_stock(store_slug,article,marketplace,scheme,warehouse,quantity) VALUES ('rimili','a','WB','fbo','one',4),('rimili','a','WB','fbo','two',6)")
        conn.execute("INSERT INTO mp_warehouse_stock(store_slug,article,marketplace,scheme,warehouse,quantity) VALUES ('tris','a','WB','fbo','private',20),('rimili','a','OZON','fbo','other',30)")
        conn.commit()
    finally:
        conn.close()
    body = client.get(f"{api.PREFIX}/stocks", params={"store": "rimili"}).json()
    assert body["totals"]["quantity"] == 10
    assert [(row["warehouse"], row["quantity"]) for row in body["rows"][0]["warehouse_breakdown"]] == [("one", 4), ("two", 6)]
    monkeypatch.setattr(agent_reports, "catalog", lambda *args: [{"article": "a", "name": "Item"}])
    card = client.get(f"{api.PREFIX}/product-details", params={"store": "rimili", "article": "a"}).json()
    assert card["context"]["stock"][0]["warehouse_breakdown"] == body["rows"][0]["warehouse_breakdown"]
    body = client.get(f"{api.PREFIX}/stocks", params={"store": "rimili", "warehouse": "one"}).json()
    assert body["totals"]["quantity"] == 4


def test_supplies_are_read_only_cached_and_scope_checked(agent_client, database_path, monkeypatch, user_factory):
    from app.web.routers import agent_full
    client, identity, *_ = agent_client
    agent_full.SUPPLY_CACHE.clear()
    loader = Mock(return_value={"supplies": [{"supply_id": 1, "status": "planned"}], "errors": [], "fetched_at": "2026-09-09"})
    monkeypatch.setattr(agent_full.supply_planning, "load_wb_planned_supplies", loader)
    for _ in range(2):
        assert client.get(f"{api.PREFIX}/supplies", params=params()).status_code == 200
    assert loader.call_count == 1
    identity.get_user.return_value = user_factory(role=Role.ADMIN, stores=("tris",))
    assert client.get(f"{api.PREFIX}/supplies", params=params()).status_code == 403
    assert loader.call_count == 1


@pytest.mark.parametrize("report, changes", [("stocks", {"refresh": True}), ("sales", {"date_from": "2099-01-01", "date_to": "2099-01-02"}), ("prices", {"marketplace": "OZON"}), ("products", {"limit": 101}), ("products", {"date_from": "2026-09-01"})])
def test_full_report_rejects_unsupported_filters(agent_client, report, changes):
    client, *_ = agent_client
    assert client.get(f"{api.PREFIX}/{report}", params={"store": "rimili", **changes}).status_code == 422


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


def params(**changes):
    return {"date_from": "2026-08-01", "date_to": "2026-08-07", "store": next(iter(STORES)), **changes}


def row(article, margin, complete=True):
    return {
        "store_slug": next(iter(STORES)),
        "article": article,
        "name": article,
        "margin": margin,
        "margin_complete": complete,
        "orders_count": 10,
    }


def test_access_rechecks_user_and_revocation(agent_client):
    client, identity, path, token, record = agent_client
    assert client.get(f"{api.PREFIX}/stores").status_code == 200
    identity.get_user.assert_called_with(UserId(7))
    identity.get_user.return_value = identity.get_user.return_value.model_copy(update={"is_active": False})
    assert client.get(f"{api.PREFIX}/stores").status_code == 401
    path.write_text("[]")
    assert resolve_credential(token, path) is None
    assert client.get(f"{api.PREFIX}/stores").status_code == 401


def test_keys_fail_closed(agent_client):
    client, _, path, token, record = agent_client
    assert resolve_credential(token + "x", path) is None
    expired = record.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    path.write_bytes(TypeAdapter(list[AgentCredential]).dump_json([expired]))
    assert client.get(f"{api.PREFIX}/stores").status_code == 401
    path.write_text("invalid json")
    assert client.get(f"{api.PREFIX}/stores").status_code == 401
    assert resolve_credential(token + "x", path) is None


def test_cookies_do_not_authenticate_agent_and_keys_do_not_authenticate_site(agent_client):
    client, identity, _, _, _ = agent_client
    identity.user_for_token.return_value = None
    assert (
        client.get(
            "/api/unit-economics-1c/reports/unit-profit", headers={"Accept": "application/json"}
        ).status_code
        == 401
    )
    client.headers.pop("Authorization")
    client.cookies.set("paketa_session", "browser-session")
    assert client.get(f"{api.PREFIX}/stores", follow_redirects=False).status_code == 401
    assert client.get(f"{api.PREFIX}/openapi.json").status_code == 200


def test_section_and_store_permissions(agent_client, user_factory, monkeypatch):
    client, identity, _, _, _ = agent_client
    identity.get_user.return_value = user_factory(role=Role.ADMIN).model_copy(
        update={"section_access": {SectionName.UNIT_ECONOMICS_1C: SectionAccessLevel.NONE}}
    )
    assert client.get(f"{api.PREFIX}/stores").status_code == 200
    assert client.get(f"{api.PREFIX}/loss-products", params=params()).status_code == 403
    identity.get_user.return_value = user_factory(role=Role.ADMIN, stores=(next(iter(STORES)),))
    loader = AsyncMock()
    monkeypatch.setattr(api, "_unit_economics_1c_unit_profit_report_data", loader)
    assert client.get(f"{api.PREFIX}/loss-products", params=params(store="forbidden")).status_code == 403
    loader.assert_not_called()


def test_top_uses_all_rows_excludes_missing_and_keeps_zero(agent_client, monkeypatch):
    client, _, _, _, _ = agent_client
    loader = AsyncMock(
        return_value={
            "rows": [
                row("zero", 0),
                row("small", -5),
                row("missing", None),
                row("partial", -9999, False),
                row("worst", -100),
                row("nan", float("nan")),
            ]
        }
    )
    monkeypatch.setattr(api, "_unit_economics_1c_unit_profit_report_data", loader)
    response = client.get(f"{api.PREFIX}/loss-products", params=params(limit=1))
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["article"] for item in body["rows"]] == ["worst"]
    assert body["total_loss_products"] == 2
    assert body["excluded_incomplete_products"] == 3
    assert body["total_products_checked"] == 6
    assert body["rows"][0]["advertising_spend_rub"] is None
    assert body["warnings"]
    assert response.headers["cache-control"] == "no-store"
    forwarded = loader.call_args.args[0]
    assert "limit" not in forwarded.query_params
    assert forwarded.state.user.id == 7


@pytest.mark.parametrize(
    "changes",
    [
        {"limit": 101},
        {"date_from": "invalid"},
        {"date_from": "2026-08-08"},
        {"date_to": "2099-01-01"},
        {"date_from": "2026-01-01"},
        {"marketplace": "Ozon"},
        {"manager": "someone"},
    ],
)
def test_invalid_filters_do_not_load_report(agent_client, monkeypatch, changes):
    client, _, _, _, _ = agent_client
    loader = AsyncMock()
    monkeypatch.setattr(api, "_unit_economics_1c_unit_profit_report_data", loader)
    assert client.get(f"{api.PREFIX}/loss-products", params=params(**changes)).status_code == 422
    loader.assert_not_called()


def test_read_only_schema(agent_client):
    client, _, _, _, _ = agent_client
    assert client.post(f"{api.PREFIX}/stores").status_code == 405
    schema = client.get(f"{api.PREFIX}/openapi.json").json()
    from app.web.routers.agent_full import SPECS
    expected = set(SPECS) | {"stores", "loss-products", "capabilities", "data-status", "article-stores"}
    assert set(schema["paths"]) == {f"{api.PREFIX}/{name}" for name in expected}
    for path in schema["paths"].values():
        assert set(path) == {"get"}
        assert path["get"]["security"] == [{"EmployeeApiKey": []}]
        assert len(path["get"].get("description", "")) <= 300
    assert schema["components"]["securitySchemes"]["EmployeeApiKey"]["scheme"] == "bearer"


def test_real_report_with_empty_local_database(agent_client, database_path):
    client, _, _, _, _ = agent_client
    response = client.get(f"{api.PREFIX}/loss-products", params=params())
    assert response.status_code == 200, response.text
    assert response.json()["rows"] == []


def test_key_cli_issue_and_revoke(tmp_path, monkeypatch, user_factory):
    from app.agent_access import read_credentials
    from scripts import agent_key

    path = tmp_path / "keys.json"
    plaintext = tmp_path / "private.txt"
    monkeypatch.setattr(
        agent_key, "settings", agent_key.settings.model_copy(update={"agent_tokens_path": path})
    )
    identity = Mock()
    identity.get_user.return_value = user_factory(user_id=7)
    monkeypatch.setattr(agent_key, "ApplicationContainer", lambda: SimpleNamespace(identity=identity))
    monkeypatch.setattr("sys.argv", ["agent_key", "issue", "--user-id", "7", "--output", str(plaintext)])
    agent_key.main()
    token = plaintext.read_text()
    assert token not in path.read_text()
    record = resolve_credential(token, path)
    assert record.user_id == 7
    monkeypatch.setattr("sys.argv", ["agent_key", "revoke", record.key_id])
    agent_key.main()
    assert read_credentials(path) == []
    assert resolve_credential(token, path) is None


def test_key_cli_does_not_overwrite_output(tmp_path, monkeypatch, user_factory):
    from scripts import agent_key

    path = tmp_path / "keys.json"
    plaintext = tmp_path / "private.txt"
    plaintext.write_text("existing")
    monkeypatch.setattr(
        agent_key, "settings", agent_key.settings.model_copy(update={"agent_tokens_path": path})
    )
    identity = Mock()
    identity.get_user.return_value = user_factory(user_id=7)
    monkeypatch.setattr(agent_key, "ApplicationContainer", lambda: SimpleNamespace(identity=identity))
    monkeypatch.setattr("sys.argv", ["agent_key", "issue", "--user-id", "7", "--output", str(plaintext)])
    with pytest.raises(FileExistsError):
        agent_key.main()
    assert plaintext.read_text() == "existing"
    assert not path.exists()
    assert not path.with_suffix(".lock").exists()


@pytest.mark.parametrize("days", [7, 180])
def test_web_key_management_ownership_and_csrf(agent_client, monkeypatch, days):
    from app.web.routers import agent_management

    client, identity, path, _, original = agent_client
    monkeypatch.setattr(
        agent_management, "settings", agent_management.settings.model_copy(update={"agent_tokens_path": path})
    )
    identity.user_for_token.return_value = identity.get_user.return_value
    client.cookies.set("paketa_session", "a" * 40)
    client.headers["Accept"] = "application/json"
    payload = {"name": "Мой аналитик", "days": days}
    assert client.post("/api/ai-agents/keys", json=payload).status_code == 403
    client.headers["X-Agent-Management"] = "1"
    assert (
        client.post(
            "/api/ai-agents/keys", json=payload, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    for invalid_days in (0, 181):
        assert client.post(
            "/api/ai-agents/keys", json={**payload, "days": invalid_days}
        ).status_code == 422
    before = datetime.now(UTC)
    result = client.post("/api/ai-agents/keys", json=payload)
    assert result.status_code == 201, result.text
    assert result.headers["cache-control"] == "no-store"
    created = result.json()
    assert before + timedelta(days=days) <= datetime.fromisoformat(created["expires_at"]) <= (
        datetime.now(UTC) + timedelta(days=days)
    )
    assert resolve_credential(created["token"], path).user_id == 7
    listing = client.get("/api/ai-agents/keys").json()
    assert "token" not in str(listing) and "token_hash" not in str(listing)
    assert any(item["name"] == payload["name"] for item in listing["keys"])
    identity.user_for_token.return_value = identity.get_user.return_value.model_copy(update={"id": 8})
    assert client.get("/api/ai-agents/keys").json()["keys"] == []
    assert client.delete("/api/ai-agents/keys/" + created["id"]).status_code == 404
    identity.user_for_token.return_value = identity.get_user.return_value
    assert client.delete("/api/ai-agents/keys/" + created["id"]).status_code == 204
    assert resolve_credential(created["token"], path) is None
    assert client.delete("/api/ai-agents/keys/" + created["id"]).status_code == 404


def test_management_page_and_button(agent_client, monkeypatch, database_path):
    from app.web.routers import agent_management

    client, identity, path, _, _ = agent_client
    monkeypatch.setattr(
        agent_management, "settings", agent_management.settings.model_copy(update={"agent_tokens_path": path})
    )
    identity.user_for_token.return_value = identity.get_user.return_value
    client.cookies.set("paketa_session", "a" * 40)
    page = client.get("/ai-agents")
    assert page.status_code == 200, page.text
    assert 'href="/ai-agents"' in page.text
    assert "Создать API-ключ" in page.text
    assert "token_hash" not in page.text
    identity.user_for_token.return_value = identity.get_user.return_value.model_copy(
        update={"role": Role.USER, "section_access": {s: SectionAccessLevel.NONE for s in SectionName}}
    )
    assert client.get("/ai-agents").status_code == 403


@pytest.mark.parametrize("report", ["product-newness", "product-reputation"])
def test_product_attributes_preserve_source_and_scope(agent_client, monkeypatch, report):
    from fastapi.responses import JSONResponse

    from app.web.routers import unit_economics

    client, *_ = agent_client
    products = [
        {"store_slug": "rimili", "article": "a", "is_new": True, "sales_days": 10, "rating": 4.8, "reviews_count": 14390},
        {"store_slug": "rimili", "article": "b", "is_new": False, "sales_days": None, "rating": None, "reviews_count": None},
        {"store_slug": "tris", "article": "private", "is_new": True, "sales_days": 1, "rating": 5, "reviews_count": 99999},
    ]
    monkeypatch.setattr(unit_economics, "sales_unit_economics_1c", AsyncMock(return_value=JSONResponse({"products": products})))
    sort = "sales_days" if report == "product-newness" else "rating"
    response = client.get(f"{api.PREFIX}/{report}", params={"store": "rimili", "sort_by": sort})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [r["article"] for r in body["rows"]] == ["a", "b"]
    assert body["totals"] == {} and body["rows"][1][sort] is None
    if report == "product-newness":
        assert body["rows"][1]["age_known"] is False
        for flag, article in [("true", "a"), ("false", "b")]:
            result = client.get(f"{api.PREFIX}/{report}", params={"store": "rimili", "is_new": flag}).json()
            assert [r["article"] for r in result["rows"]] == [article]
    else:
        assert body["rows"][0]["reviews_count"] == 14390


def test_stock_summary_matches_columns_and_keeps_unknowns(agent_client, monkeypatch):
    from app import agent_reports
    from app.web import stock_rendering

    client, *_ = agent_client
    monkeypatch.setattr(stock_rendering, "schemes_for", lambda *a: [("fbs", "FBS"), ("rfbs", "rFBS"), ("fbo", "FBO")])
    monkeypatch.setattr(agent_reports.db, "get_stock_items", lambda *a: [{"article": "a", "fbs_stock": 90, "rfbs_stock": None, "fbo_stock": 7}])
    monkeypatch.setattr(agent_reports.db, "get_ff_available_totals", lambda *a: {"a": 3})
    monkeypatch.setattr(agent_reports.db, "get_ff_transit_totals", lambda *a: {"a": 2})
    monkeypatch.setattr(agent_reports.db, "get_mp_stock_by_warehouse", lambda *a: {"a": 4})
    response = client.get(f"{api.PREFIX}/stocks", params={"store": "rimili", "view": "summary", "fulfillment": "Selected"})
    assert response.status_code == 200, response.text
    body = response.json()
    row = body["rows"][0]
    assert row["total"] == 16 and row["fbs_stock"] == 4 and row["fbo_stock"] == 7
    assert row["rfbs_stock"] is None and row["missing_components"] == ["rfbs_stock"]
    assert body["totals"]["total"] == 16
    assert client.get(f"{api.PREFIX}/stocks", params={"store": "rimili", "view": "summary", "scheme": "fbo"}).status_code == 422


def test_current_economics_uses_table_not_calculator(agent_client, monkeypatch):
    from fastapi.responses import JSONResponse

    from app.web.routers import unit_economics
    client, *_ = agent_client
    product = {"store_slug": "rimili", "article": "a", "name": "Item",
               "current_economics": {"margin": 594.0, "roi": 50.8, "period_to": "2026-09-09"},
               "price": {"current": 100, "with_spp": 63.68},
               "results": {"margin": 592.23, "roi": 50.65}}
    loader = AsyncMock(return_value=JSONResponse({"product": product}))
    monkeypatch.setattr(unit_economics, "sales_unit_economics_1c", loader)
    response = client.get(f"{api.PREFIX}/current-economics", params={"store": "RIMILI", "article": "a"})
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["margin_per_unit_rub"] == 594.0 and row["roi_percent"] == 50.8
    assert row["spp_percent"] == pytest.approx(36.32)
    assert response.json()["totals"] == {}
    assert loader.call_args.args[0].query_params["store"] == "rimili"


def test_current_economics_filters_scopes_and_preserves_unknowns(agent_client, monkeypatch):
    from fastapi.responses import JSONResponse

    from app import agent_reports
    from app.web.routers import unit_economics
    client, *_ = agent_client
    products = [{"store_slug": store, "article": article, "current_economics": {"margin": margin, "roi": margin},
                 "price": {"current": 0, "with_spp": None}}
                for store, article, margin in [("rimili", "a", None), ("rimili", "b", 0), ("tris", "private", 999)]]
    monkeypatch.setattr(unit_economics, "sales_unit_economics_1c", AsyncMock(return_value=JSONResponse({"products": products})))
    response = client.get(f"{api.PREFIX}/current-economics", params={"store": "rimili", "sort_by": "roi_percent", "limit": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"][0]["article"] == "b" and body["rows"][0]["roi_percent"] == 0
    assert body["rows"][0]["spp_percent"] is None
    assert body["total_rows"] == 2 and body["next_offset"] == 1 and body["totals"] == {}
    monkeypatch.setattr(agent_reports, "economic_filter", lambda *args: [])
    assert client.get(f"{api.PREFIX}/current-economics", params={"store": "rimili"}).json()["rows"] == []


@pytest.mark.parametrize("column,value", [("goal_week", "0"), ("goal_day", "5"), ("status", "good"),
                                        ("ends", "W46 2026"), ("code", "a"), ("fact", "367"), ("plan", "399")])
def test_product_tags_each_column(agent_client, monkeypatch, column, value):
    from fastapi.responses import JSONResponse

    from app.web.routers import unit_economics
    client, *_ = agent_client
    tag = {"goal_week": 0, "goal_day": 5, "status": "GOOD", "ends": "W46 2026", "code": "A", "fact": 367, "plan": 399}
    products = [{"store_slug": "rimili", "article": "a", "tag_data": tag},
                {"store_slug": "rimili", "article": "b", "tag_data": {}},
                {"store_slug": "tris", "article": "private", "tag_data": tag}]
    monkeypatch.setattr(unit_economics, "sales_unit_economics_1c", AsyncMock(return_value=JSONResponse({"products": products})))
    body = client.get(f"{api.PREFIX}/product-tags", params={"store": "rimili", "tag_column": column, "tag_value": value}).json()
    assert body["total_rows"] == 1 and body["rows"][0]["article"] == "a"
    assert {k: body["rows"][0][k] for k in tag} == tag
    assert body["totals"] == {} and body["context"]["period_verified"] is False
    body = client.get(f"{api.PREFIX}/product-tags", params={"store": "rimili", "sort_by": column, "limit": 1}).json()
    assert body["rows"][0]["article"] == "a" and body["total_rows"] == 2 and body["next_offset"] == 1


def test_tag_filters_reject_invalid_and_preserve_null():
    from app.agent_reports import filter_tags
    from app.web.routers.agent_full import ReportQuery
    rows = [{"goal_week": None}, {"goal_week": 0}, {"goal_week": 4}]
    assert filter_tags(rows, ReportQuery(tag_column="goal_week", tag_value="0", tag_operator="lte")) == [{"goal_week": 0}]
    assert filter_tags(rows, ReportQuery(tag_column="goal_week", tag_value="1", tag_operator="gte")) == [{"goal_week": 4}]
    for params in [{"tag_column": "fact"}, {"tag_value": "1"}, {"tag_column": "fact", "tag_value": "NaN"},
                   {"tag_column": "status", "tag_value": "GOOD", "tag_operator": "gte"}]:
        with pytest.raises(ValueError):
            filter_tags(rows, ReportQuery(**params))
