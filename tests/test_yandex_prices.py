from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dto.identity import Role, SectionAccessLevel, SectionName, User
from app.dto.yandex_prices import PricePreview
from app.repositories import yandex_storefront
from app.web.routers import yandex_economics, yandex_prices
from app.yandex import api, prices

SKU = "Лампы/H4-Y7D"
BASE = "/api/unit-economics-1c/yandex-market/prices"


@pytest.fixture
def market(monkeypatch):
    fixture = SimpleNamespace(
        current={"value": 3000, "currencyId": "RUR", "discountBase": 10000, "minimumForBestseller": 2100},
        requests=[],
        writes=[],
        saved=[],
        quarantined=False,
        apply_immediately=True,
        read_error=None,
        write_error=None,
    )

    def request(path, key, payload=None, params=None):
        assert key == "test-only"
        assert path.startswith("/v2/businesses/777/")
        fixture.requests.append((path, deepcopy(payload)))
        if path.endswith("/updates"):
            fixture.writes.append(deepcopy(payload))
            if fixture.write_error:
                raise fixture.write_error
            if fixture.apply_immediately:
                fixture.current = deepcopy(payload["offers"][0]["price"])
            return {"status": "OK"}
        assert payload == {"offerIds": [SKU]}
        if fixture.writes and fixture.read_error:
            raise fixture.read_error
        if path.endswith("/price-quarantine"):
            return {"offers": [{"offerId": SKU}] if fixture.quarantined else []}
        return {"offers": [{"offerId": SKU, "price": deepcopy(fixture.current)}]}

    monkeypatch.setattr(api, "request", request)
    monkeypatch.setattr(prices.tokens, "get_api_key", lambda store: "test-only")
    monkeypatch.setattr(prices, "resolve_business_id", lambda store, key: 777)
    monkeypatch.setattr(prices, "POLL_SECONDS", 0)
    monkeypatch.setattr(prices, "POLL_ATTEMPTS", 2)
    monkeypatch.setattr(prices, "save_confirmed_price", lambda plan: fixture.saved.append(deepcopy(plan)))
    monkeypatch.setattr(yandex_prices.db, "log_action", lambda *args: None)
    return fixture


def test_preview_read_only_and_submit_preserves_metadata(market):
    plan = prices.preview("tris", SKU, 3200.125)
    assert not market.writes
    assert plan["price"] == {
        "value": 3200.13,
        "currencyId": "RUR",
        "discountBase": 10000,
        "minimumForBestseller": 2100,
    }
    sent = []
    assert prices.apply(plan, lambda: sent.append(True))["status"] == "success"
    assert sent == [True]
    assert market.writes == [{"offers": [{"offerId": SKU, "price": plan["price"]}]}]
    assert len(market.saved) == 1


@pytest.mark.parametrize(
    "field,value", [("value", 3100), ("discountBase", 11000), ("minimumForBestseller", 2200)]
)
def test_stale_confirmation_does_not_overwrite_other_changes(market, field, value):
    plan = prices.preview("tris", SKU, 3200)
    market.current[field] = value
    with pytest.raises(ValueError, match="уже изменились"):
        prices.apply(plan, lambda: None)
    assert not market.writes


def test_changed_business_does_not_send(market, monkeypatch):
    plan = prices.preview("tris", SKU, 3200)
    monkeypatch.setattr(prices, "resolve_business_id", lambda store, key: 999)
    with pytest.raises(ValueError, match="Привязка"):
        prices.apply(plan, lambda: None)
    assert not market.writes


@pytest.mark.parametrize("quarantine,status", [(True, "quarantined"), (False, "unconfirmed")])
def test_accepted_is_not_applied(market, quarantine, status):
    plan = prices.preview("tris", SKU, 3200)
    market.apply_immediately = False
    market.quarantined = quarantine
    assert prices.apply(plan, lambda: None)["status"] == status
    assert not market.saved


def test_matching_price_in_quarantine_is_not_success(market):
    plan = prices.preview("tris", SKU, 3200)
    market.quarantined = True
    assert prices.apply(plan, lambda: None)["status"] == "quarantined"
    assert not market.saved


def test_api_rejection_never_updates_database(market):
    plan = prices.preview("tris", SKU, 3200)
    market.write_error = api.YandexApiError(403, "pricing required")
    with pytest.raises(api.YandexApiError):
        prices.apply(plan, lambda: pytest.fail("Not accepted"))
    assert not market.saved


def test_polling_temporary_error_does_not_claim_success(market):
    plan = prices.preview("tris", SKU, 3200)
    market.read_error = api.YandexApiError(429)
    assert prices.apply(plan, lambda: None)["status"] == "unconfirmed"
    assert not market.saved


def test_ambiguous_send_error_requires_verification(market):
    plan = prices.preview("tris", SKU, 3200)
    market.write_error = api.YandexApiError(None, "timeout", retryable=True)
    with pytest.raises(ValueError, match="проверьте цену"):
        prices.apply(plan, lambda: pytest.fail("Not confirmed"))
    assert not market.saved


def test_invalid_discount_does_not_remove_crossed_price(market):
    with pytest.raises(ValueError, match="зачёркнутой"):
        prices.preview("tris", SKU, 9900)
    assert not market.writes


def test_non_ruble_currency_is_rejected(market):
    market.current["currencyId"] = "KZT"
    with pytest.raises(ValueError, match="рублях"):
        prices.preview("tris", SKU, 3200)


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), 1_000_000_001])
def test_invalid_input(price):
    with pytest.raises(ValueError):
        PricePreview(store_slug="tris", article=SKU, seller_price=price)


@pytest.fixture
def site(market, monkeypatch):
    users = {
        index: User(
            id=index,
            full_name=f"User {index}",
            login=f"user{index}",
            role=Role.USER,
            created_at=datetime.now(UTC),
            store_slugs=(store,),
            section_access={SectionName.UNIT_ECONOMICS_YANDEX: access},
        )
        for index, store, access in (
            (1, "tris", SectionAccessLevel.WRITE),
            (2, "tris", SectionAccessLevel.READ),
            (3, "gogol", SectionAccessLevel.WRITE),
            (4, "tris", SectionAccessLevel.WRITE),
        )
    }
    monkeypatch.setattr(yandex_economics.yandex_assortment, "active_articles", lambda store: {SKU})
    queue = []
    monkeypatch.setattr(yandex_prices, "EXECUTOR", SimpleNamespace(submit=lambda *args: queue.append(args)))
    app = FastAPI()

    @app.middleware("http")
    async def user(request, call_next):
        request.state.user = users[int(request.headers.get("X-Test-User", "1"))]
        return await call_next(request)

    app.include_router(yandex_prices.router)
    with TestClient(app) as client:
        yield client, queue


def preview_id(client, price=3200):
    response = client.post(
        BASE + "/preview", json={"store_slug": "tris", "article": SKU, "seller_price": price}
    )
    assert response.status_code == 200, response.text
    return response.json()["preview_id"]


def test_confirm_job_poll_and_idempotent_retries(site, market):
    client, queue = site
    token = preview_id(client)
    assert market.writes == []
    payload = {"preview_id": token}
    assert client.post(BASE, json=payload).status_code == 202
    assert client.post(BASE, json=payload).json()["job_id"] == token
    assert len(queue) == 1
    assert client.get(BASE + "/jobs/" + token).json()["status"] == "queued"
    queue[0][0](*queue[0][1:])
    job = client.get(BASE + "/jobs/" + token).json()
    assert job["status"] == "success"
    assert job["result"]["article"] == SKU
    assert client.post(BASE, json=payload).status_code == 202
    assert len(market.writes) == 1
    assert len(queue) == 1


@pytest.mark.parametrize("user", [2, 3])
def test_scope_and_read_only_denied_before_api(site, market, user):
    client, _ = site
    response = client.post(
        BASE + "/preview",
        headers={"X-Test-User": str(user)},
        json={"store_slug": "tris", "article": SKU, "seller_price": 3200},
    )
    assert response.status_code == 403
    assert market.requests == []


def test_article_outside_catalog_denied_before_api(site, market):
    client, _ = site
    response = client.post(
        BASE + "/preview", json={"store_slug": "tris", "article": "unknown", "seller_price": 3200}
    )
    assert response.status_code == 404
    assert market.requests == []


def test_missing_key_returns_actionable_error(site, monkeypatch):
    client, _ = site

    def missing_key(store):
        raise KeyError("Нет ключа Яндекс Маркета")

    monkeypatch.setattr(prices.tokens, "get_api_key", missing_key)
    response = client.post(
        BASE + "/preview", json={"store_slug": "tris", "article": SKU, "seller_price": 3200}
    )
    assert response.status_code == 422
    assert "ключа" in response.json()["detail"]


def test_other_user_cannot_confirm_or_read_job(site):
    client, _ = site
    token = preview_id(client)
    assert client.post(BASE, json={"preview_id": token}, headers={"X-Test-User": "4"}).status_code == 404
    client.post(BASE, json={"preview_id": token})
    assert client.get(BASE + "/jobs/" + token, headers={"X-Test-User": "4"}).status_code == 404


def test_one_active_job_per_business_offer(site):
    client, queue = site
    first, second = preview_id(client), preview_id(client, 3300)
    assert client.post(BASE, json={"preview_id": first}).status_code == 202
    assert client.post(BASE, json={"preview_id": second}).status_code == 409
    assert len(queue) == 1


def test_expired_preview_and_payload_tampering(site):
    client, queue = site
    token = preview_id(client)
    assert client.post(BASE, json={"preview_id": token, "seller_price": 1}).status_code == 422
    client.app.state.yandex_price_jobs[token]["created"] -= yandex_prices.PREVIEW_TTL + 1
    assert client.post(BASE, json={"preview_id": token}).status_code == 404
    assert not queue


def test_save_changes_only_seller_price_and_keeps_storefront_observation(monkeypatch, tmp_path):
    # A separate SQLite file exercises the real repository; never open the project's DB.
    import sqlite3

    from app.repositories.core import database_for_path

    path = tmp_path / "prices.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE yandex_storefront_prices (store_slug TEXT, article TEXT, seller_price REAL, "
            "seller_checked_at TEXT, buyer_price REAL, pay_price REAL, target_json TEXT)"
        )
        conn.execute(
            "INSERT INTO yandex_storefront_prices VALUES (?, ?, 3000, 'old', 2500, 2400, ?)",
            ("tris", SKU, '{"card_id": "123"}'),
        )
    monkeypatch.setattr(yandex_storefront, "get_connection", database_for_path(path).connect)
    monkeypatch.setattr(prices.economics, "capture_today", lambda *args, **kwargs: None)
    prices.save_confirmed_price({"store_slug": "tris", "article": SKU, "price": {"value": 3200}})
    row = yandex_storefront.get_prices("tris")[SKU]
    assert row["seller_price"] == 3200
    assert row["seller_checked_at"] != "old"
    assert (row["buyer_price"], row["pay_price"], row["target_json"]) == (2500, 2400, '{"card_id": "123"}')
