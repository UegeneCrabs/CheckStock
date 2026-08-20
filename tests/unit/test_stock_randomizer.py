from unittest import mock

from app import db
from app.dto.identity import Role
from app.web.routers import auth as auth_routes

NOW = "2026-08-20T09:00:00+03:00"
FULFILLMENT = "ФулСервис Подольск"


def _stock_two_articles() -> None:
    db.replace_catalog(
        "rimili",
        "WB",
        [
            {"article": "A-1", "barcode": "1001", "name": "Первый товар"},
            {"article": "B-2", "barcode": "1002", "name": "Второй товар"},
        ],
        NOW,
    )
    db.upsert_ff_stock("rimili", "A-1", FULFILLMENT, 7, NOW, "WB")
    db.upsert_ff_stock("rimili", "B-2", FULFILLMENT, 4, NOW, "WB")
    db.replace_mp_warehouse_stock(
        "rimili",
        "WB",
        "fbs",
        [
            ("A-1", FULFILLMENT, None, 3, NOW),
            ("B-2", FULFILLMENT, None, 2, NOW),
        ],
    )


def test_randomizer_uses_only_unused_articles_and_resets_next_month(database_path) -> None:
    _stock_two_articles()
    choose_first = mock.patch(
        "app.repositories.stock_randomizer.secrets.choice",
        side_effect=lambda values: values[0],
    )
    with choose_first:
        first = db.generate_stock_audit_sample(
            ("rimili",), FULFILLMENT, "2026-08", 1, "Tester", NOW
        )
        second = db.generate_stock_audit_sample(
            ("rimili",), FULFILLMENT, "2026-08", 1, "Tester", NOW
        )
        exhausted = db.generate_stock_audit_sample(
            ("rimili",), FULFILLMENT, "2026-08", 1, "Tester", NOW
        )
        next_month = db.generate_stock_audit_sample(
            ("rimili",), FULFILLMENT, "2026-09", 1, "Tester", NOW
        )

    assert first["items"][0]["article"] == "A-1"
    assert first["items"][0]["ff_quantity"] == 7
    assert first["items"][0]["fbs_quantity"] == 3
    assert second["items"][0]["article"] == "B-2"
    assert exhausted["items"][0]["article"] is None
    assert "уже попадали" in exhausted["items"][0]["message"]
    assert next_month["items"][0]["article"] == "A-1"

    state = db.get_stock_audit_state(("rimili",), FULFILLMENT, "2026-08")
    assert state["items"][0]["article"] == "B-2"
    assert state["items"][0]["used_count"] == 2
    assert state["items"][0]["remaining_count"] == 0


def test_randomizer_accepts_positive_stock_in_either_source(database_path) -> None:
    db.replace_catalog(
        "rimili",
        "WB",
        [
            {"article": "FF-ONLY", "barcode": "2001", "name": "Только ФФ"},
            {"article": "FBS-ONLY", "barcode": "2002", "name": "Только FBS"},
        ],
        NOW,
    )
    db.upsert_ff_stock("rimili", "FF-ONLY", FULFILLMENT, 2, NOW, "WB")
    db.replace_mp_warehouse_stock(
        "rimili",
        "WB",
        "fbs",
        [("FBS-ONLY", FULFILLMENT, None, 2, NOW)],
    )

    with mock.patch(
        "app.repositories.stock_randomizer.secrets.choice",
        side_effect=lambda values: values[0],
    ):
        first = db.generate_stock_audit_sample(
            ("rimili",), FULFILLMENT, "2026-08", 1, "Tester", NOW
        )
        second = db.generate_stock_audit_sample(
            ("rimili",), FULFILLMENT, "2026-08", 1, "Tester", NOW
        )

    assert first["items"][0]["article"] == "FBS-ONLY"
    assert first["items"][0]["ff_quantity"] == 0
    assert first["items"][0]["fbs_quantity"] == 2
    assert second["items"][0]["article"] == "FF-ONLY"
    assert second["items"][0]["ff_quantity"] == 2
    assert second["items"][0]["fbs_quantity"] == 0


def test_randomizer_does_not_repeat_an_article_between_stores(database_path) -> None:
    for store_slug in ("rimili", "tris"):
        db.replace_catalog(
            store_slug,
            "WB",
            [{"article": "SHARED", "barcode": f"{store_slug}-1", "name": "Общий артикул"}],
            NOW,
        )
        db.upsert_ff_stock(store_slug, "SHARED", FULFILLMENT, 2, NOW, "WB")
        db.replace_mp_warehouse_stock(
            store_slug,
            "WB",
            "fbs",
            [("SHARED", FULFILLMENT, None, 2, NOW)],
        )

    result = db.generate_stock_audit_sample(
        ("rimili", "tris"), FULFILLMENT, "2026-08", 1, "Tester", NOW
    )

    assert result["items"][0]["article"] == "SHARED"
    assert result["items"][1]["article"] is None


def test_randomizer_page_and_generation_respect_store_access(client, user_factory) -> None:
    _stock_two_articles()
    user = user_factory(role=Role.USER, stores=("rimili",))
    client.cookies.set(auth_routes.auth.SESSION_COOKIE, "test-session")

    with mock.patch.object(client.app.state.container.identity, "user_for_token", return_value=user):
        page = client.get("/stock/randomizer", params={"ff": FULFILLMENT})
        response = client.post(
            "/stock/randomizer/generate",
            json={"fulfillment": FULFILLMENT},
            headers={"X-Requested-With": "fetch"},
        )
        invalid = client.post(
            "/stock/randomizer/generate",
            json={"fulfillment": "Неизвестный ФФ"},
            headers={"X-Requested-With": "fetch"},
        )

    assert page.status_code == 200
    assert "Рандомайзер" in page.text
    assert 'data-store="rimili"' in page.text
    assert 'data-store="tris"' not in page.text
    assert response.status_code == 200
    assert response.json()["generated_count"] == 1
    assert [item["store_slug"] for item in response.json()["items"]] == ["rimili"]
    assert invalid.status_code == 400


def test_randomizer_generation_is_blocked_for_read_only_stock_access(client, user_factory) -> None:
    user = user_factory(role=Role.USER, stores=("rimili",), can_edit_stock=False)
    client.cookies.set(auth_routes.auth.SESSION_COOKIE, "test-session")

    with mock.patch.object(client.app.state.container.identity, "user_for_token", return_value=user):
        page = client.get("/stock/randomizer")
        response = client.post(
            "/stock/randomizer/generate",
            json={"fulfillment": FULFILLMENT},
            headers={"X-Requested-With": "fetch"},
        )

    assert page.status_code == 200
    assert 'data-access-level="read"' in page.text
    assert response.status_code == 403
    assert response.json()["error"] == "Раздел доступен только для просмотра"
