from datetime import datetime
from unittest import mock

from app import db, supply_planning
from app.domain import MOSCOW_TIMEZONE
from app.dto.identity import Role
from app.wb import api as wb_api
from app.web import middleware


def _sign_in(client, application, user_factory, monkeypatch):
    user = user_factory()
    monkeypatch.setattr(application.state.container.identity, "user_for_token", lambda token: user)
    client.cookies.set(middleware.auth.SESSION_COOKIE, "x" * 32)
    return user


def test_fbw_supplies_api_filters_and_paginates() -> None:
    pages = [
        [{"supplyID": 1}, {"supplyID": 2}],
        [{"supplyID": 3}],
    ]
    with mock.patch.object(wb_api, "_request", side_effect=pages) as request:
        rows = wb_api.get_fbw_supplies(
            "token",
            status_ids=(2,),
            date_from="2026-08-20",
            date_to="2026-11-20",
            page_limit=2,
        )

    assert [row["supplyID"] for row in rows] == [1, 2, 3]
    assert request.call_args_list[0].args[:2] == (
        "POST",
        f"{wb_api.SUPPLIES_BASE}/api/v1/supplies",
    )
    assert request.call_args_list[0].kwargs["json_body"] == {
        "dates": [
            {
                "from": "2026-08-20",
                "till": "2026-11-20",
                "type": "supplyDate",
            }
        ],
        "statusIDs": [2],
    }
    assert request.call_args_list[1].kwargs["params"]["offset"] == 2


def test_wb_planned_supplies_are_sorted_and_marked_urgent() -> None:
    now = datetime(2026, 8, 20, 10, 0, tzinfo=MOSCOW_TIMEZONE)
    rows = [
        {
            "supplyID": 2,
            "statusID": 2,
            "supplyDate": "2026-08-23T12:00:00+03:00",
            "boxTypeID": 5,
        },
        {
            "supplyID": 1,
            "statusID": 2,
            "supplyDate": "2026-08-21T12:00:00+03:00",
            "boxTypeID": 2,
            "isBoxOnPallet": True,
        },
        {
            "supplyID": 99,
            "statusID": 5,
            "supplyDate": "2026-08-20T12:00:00+03:00",
        },
        {
            "supplyID": 102,
            "statusID": 2,
            "supplyDate": "2026-05-20T00:00:00+03:00",
            "boxTypeID": 1,
        },
        {
            "supplyID": 100,
            "statusID": 2,
            "supplyDate": "2024-05-28T00:00:00+03:00",
        },
        {
            "supplyID": 101,
            "statusID": 2,
            "supplyDate": "2026-12-01T00:00:00+03:00",
        },
    ]
    with (
        mock.patch.object(supply_planning.wb_tokens, "has_token", return_value=True),
        mock.patch.object(supply_planning.wb_tokens, "get_token", return_value="token"),
        mock.patch.object(
            supply_planning.wb_api, "get_fbw_supplies", return_value=rows
        ) as get_supplies,
    ):
        report = supply_planning.load_wb_planned_supplies(("rimili",), now=now)

    assert [row["supply_id"] for row in report["supplies"]] == [102, 1, 2]
    assert report["supplies"][0]["is_urgent"] is True
    assert report["supplies"][1]["is_urgent"] is True
    assert report["supplies"][2]["is_urgent"] is False
    assert report["supplies"][1]["supply_type"] == "Поштучная паллета"
    assert report["supplies"][2]["supply_type"] == "Монопаллеты"
    assert report["date_from"] == "2026-05-20"
    assert report["date_to"] == "2026-11-20"
    get_supplies.assert_called_once_with(
        "token",
        status_ids=(2,),
        date_from="2026-05-20",
        date_to="2026-11-20",
    )


def test_manual_supply_routes_persist_sort_update_and_delete(
    client,
    application,
    user_factory,
    monkeypatch,
) -> None:
    _sign_in(client, application, user_factory, monkeypatch)

    later = client.post(
        "/stock/planning/manual",
        json={
            "store_slug": "rimili",
            "delivery_at": "2026-08-23T12:00",
            "origin": "ФФ Подольск",
            "destination": "Коледино",
            "supply_type": "Короба",
            "ready": False,
        },
    )
    earlier = client.post(
        "/stock/planning/manual",
        json={
            "store_slug": "tris",
            "delivery_at": "2026-08-21T09:30",
            "origin": "ФФ Чехов",
            "destination": "Электросталь",
            "supply_type": "Монопаллета",
            "ready": False,
        },
    )
    assert later.status_code == 201, later.text
    assert earlier.status_code == 201, earlier.text

    rows = client.get("/stock/planning/manual").json()["supplies"]
    assert [row["destination"] for row in rows] == ["Электросталь", "Коледино"]
    assert [row["store_name"] for row in rows] == ["TRIS", "RIMILI"]
    completed_id = rows[0]["id"]
    active_id = rows[1]["id"]

    ready = client.patch(
        f"/stock/planning/manual/{completed_id}/ready",
        json={"ready": True},
    )
    assert ready.status_code == 200, ready.text
    assert ready.json()["supply"]["ready"] is True
    active_rows = client.get("/stock/planning/manual").json()["supplies"]
    assert [row["id"] for row in active_rows] == [active_id]
    assert db.get_manual_supply(completed_id)["ready"] is True

    updated = client.put(
        f"/stock/planning/manual/{active_id}",
        json={
            "store_slug": "tris",
            "delivery_at": "2026-08-22T11:00",
            "origin": "ФФ Чехов",
            "destination": "Тула",
            "supply_type": "Короба",
            "ready": False,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["supply"]["destination"] == "Тула"

    assert updated.json()["supply"]["store_name"] == "TRIS"

    deleted = client.delete(f"/stock/planning/manual/{active_id}")
    assert deleted.status_code == 200, deleted.text
    assert client.get("/stock/planning/manual").json()["supplies"] == []
    assert db.get_manual_supply(completed_id) is not None


def test_manual_supply_rejects_unavailable_cabinet(
    client,
    application,
    user_factory,
    monkeypatch,
) -> None:
    user = user_factory(role=Role.USER, stores=("rimili",))
    monkeypatch.setattr(
        application.state.container.identity,
        "user_for_token",
        lambda token: user,
    )
    client.cookies.set(middleware.auth.SESSION_COOKIE, "x" * 32)

    response = client.post(
        "/stock/planning/manual",
        json={
            "store_slug": "tris",
            "delivery_at": "2026-08-23T12:00",
            "origin": "ФФ Подольск",
            "destination": "Коледино",
            "supply_type": "Короба",
            "ready": False,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Нет доступа к этому кабинету"


def test_supplies_render_on_a_separate_stock_page(
    client,
    application,
    user_factory,
    monkeypatch,
) -> None:
    _sign_in(client, application, user_factory, monkeypatch)

    stock = client.get("/stock")
    supplies = client.get("/stock/supplies")

    assert stock.status_code == 200, stock.text
    assert supplies.status_code == 200, supplies.text
    assert "data-supply-planner" not in stock.text
    assert "data-supply-planner" in supplies.text
    assert 'data-wb-date-from' in supplies.text
    assert 'data-wb-date-to' in supplies.text
    assert 'data-wb-date-sort' in supplies.text
    assert 'select name="store_slug"' in supplies.text
    assert '<option value="rimili">RIMILI</option>' in supplies.text
    assert '<a class="nav-subitem active" href="/stock/supplies">Поставки</a>' in supplies.text


def test_wb_supply_route_rejects_dates_outside_three_month_window(
    client,
    application,
    user_factory,
    monkeypatch,
) -> None:
    _sign_in(client, application, user_factory, monkeypatch)

    response = client.get(
        "/stock/planning/wb",
        params={"date_from": "2024-04-01", "date_to": "2024-05-31"},
    )

    assert response.status_code == 400
    assert "3 месяцев" in response.json()["detail"]
