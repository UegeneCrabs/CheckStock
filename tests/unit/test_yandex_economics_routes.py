from app.dto.identity import Role
from app.repositories import yandex_economics as repository
from app.repositories.yandex_assortment import active_articles
from app.web.middleware import auth
from app.yandex import economics, economics_api


def test_integration_settings_inheritance_reset_and_scope(client, application, user_factory, monkeypatch):
    monkeypatch.setattr(application.state.container.identity, "user_for_token", lambda _: user_factory())
    monkeypatch.setattr(economics, "capture_today", lambda *args, **kwargs: None)
    client.cookies.set(auth.SESSION_COOKIE, "test")
    article = sorted(active_articles("tris"))[0]
    path = "/api/unit-economics-1c/yandex-market/settings/tris"
    product_path = path + "?article=" + article
    headers = {"X-Requested-With": "fetch"}
    assert article in client.get(path).json()["articles"]
    assert client.put(path, headers=headers, json={"revision": 0, "values": {"tax_percent": 6}}).status_code == 200
    state = client.get(product_path).json()
    assert state["values"]["tax_percent"] == 6 and state["origins"]["tax_percent"] == "Настройки кабинета"
    saved = client.put(product_path, headers=headers, json={"revision": 0, "values": {"purchase_price": 125, "tax_percent": 0}})
    assert saved.status_code == 200, saved.text
    assert saved.json()["values"]["purchase_price"] == 125
    assert economics.detail("tris", article)["values"]["purchase_price"] == 125
    assert client.get(product_path).json()["values"]["tax_percent"] == 0
    assert client.get(product_path + "&scheme=FBS").json()["overrides"] == {}
    assert client.put(product_path, headers=headers, json={"revision": 0, "values": {"tax_percent": 7}}).status_code == 409
    reset = client.put(product_path, headers=headers, json={"revision": 1, "values": {"tax_percent": None}})
    assert reset.json()["values"]["tax_percent"] == 6
    assert len(repository.audit("tris", article)) == 2
    assert client.put(path, headers=headers, json={"revision": 1, "values": {"purchase_price": 10}}).status_code == 422
    assert client.put(product_path, headers=headers, json={"revision": 2, "values": {"seller_price": 10}}).status_code == 422
    assert client.get(path + "?article=unknown-sku").status_code == 404
    assert client.get(path.replace("tris", "missing-store")).status_code == 404
    monkeypatch.setattr(application.state.container.identity, "user_for_token", lambda _: user_factory(role=Role.USER))
    assert client.get(product_path).status_code == 403
    assert client.put(product_path, headers=headers, json={"revision": 2, "values": {"purchase_price": 99}}).status_code == 403


def test_save_preview_history_and_scope(client, application, user_factory, monkeypatch):
    user = user_factory()
    monkeypatch.setattr(application.state.container.identity, "user_for_token", lambda _: user)
    client.cookies.set(auth.SESSION_COOKIE, "test")
    article = sorted(active_articles("tris"))[0]
    root = "/api/unit-economics-1c/yandex-market/"
    path = "tris/" + article
    headers = {"X-Requested-With": "fetch"}
    response = client.put(
        root + "economics/" + path,
        headers=headers,
        json={"scheme": "FBY", "revision": 0, "values": {"purchase_price": 123}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["economics"]["values"]["purchase_price"] == 123
    preview = client.post(
        root + "calculate/" + path, headers=headers, json={"scheme": "FBY", "values": {"purchase_price": 0}}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["economics"]["values"]["purchase_price"] == 0
    assert repository.settings("tris", article, "FBY")["values"]["purchase_price"] == 123
    assert (
        client.put(
            root + "economics/" + path,
            headers=headers,
            json={"scheme": "FBY", "revision": 0, "values": {"purchase_price": 50}},
        ).status_code
        == 409
    )
    assert (
        client.put(
            root + "economics/" + path,
            headers=headers,
            json={"scheme": "FBY", "revision": 1, "values": {"purchase_price": -1}},
        ).status_code
        == 422
    )
    history = client.get(root + "economics-history/" + path).json()
    assert len(history["audit"]) == 1 and history["history"] == []
    assert client.get(root + "economics/tris/not-an-active-sku").status_code == 404
    user = user_factory(role=Role.USER, stores=("rimili",))
    assert client.get(root + "economics/" + path).status_code == 403


def test_tariff_refresh_preserves_saved_and_scenario_priority(client, application, user_factory, monkeypatch):
    monkeypatch.setattr(application.state.container.identity, "user_for_token", lambda _: user_factory())
    client.cookies.set(auth.SESSION_COOKIE, "test")
    article = sorted(active_articles("tris"))[0]
    repository.save_settings("tris", article, "FBY", {"commission_percent": 20}, 0, "test")
    monkeypatch.setattr(
        economics_api,
        "quote",
        lambda *args, **kwargs: {"components": {"commission_percent": 40}, "services": []},
    )
    path = "/api/unit-economics-1c/yandex-market/calculate/tris/" + article
    for scenario, expected in (
        ({}, 20),
        ({"commission_percent": 10}, 10),
        ({"commission_percent": None}, 40),
    ):
        response = client.post(
            path,
            headers={"X-Requested-With": "fetch"},
            json={"scheme": "FBY", "values": scenario, "refresh_tariffs": True},
        )
        assert response.status_code == 200, response.text
        assert response.json()["economics"]["values"]["commission_percent"] == expected
