from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app import background, db, inbound_supplies, sync_settings
from app.application.inbound_supplies import InboundSourceError, InboundSupplyService
from app.dto.identity import AccessProfile, MarketplaceAccessScope, Role, SectionAccessLevel, SectionName
from app.dto.inbound_supplies import InboundItem, InboundSupply
from app.infrastructure.database import database_for_path
from app.infrastructure.inbound_repository import SqlAlchemyInboundRepository
from app.infrastructure.orm import FulfillmentStockRecord, InboundSupplySnapshotRecord, MarketplaceStockRecord
from app.ozon import inbound as ozon
from app.stores import STORES
from app.wb import inbound as wb
from app.web import middleware
from app.yandex import inbound as yandex

NOW = datetime(2026, 9, 11, 10, tzinfo=UTC)


def supply(key="1", stage="transit", **kwargs):
    return InboundSupply(
        key=key,
        supply_id=key,
        stage=stage,
        status="IN_TRANSIT",
        status_label="В пути",
        items=(InboundItem(article="sku", barcode="000123", quantity=100),),
        **kwargs,
    )


def wb_good(**updates):
    return {
        "nmID": 100,
        "barcode": "000123",
        "vendorCode": "my-article",
        "quantity": 100,
        "acceptedQuantity": 0,
        "readyForSaleQuantity": 0,
        **updates,
    }


@pytest.mark.parametrize(
    ("status", "transit", "expected"),
    [
        (2, None, "planned"),
        (3, None, "planned"),
        (4, None, "acceptance"),
        (6, 9, "transit"),
        (6, None, "acceptance"),
    ],
)
def test_wb_distinguishes_plans_transit_and_acceptance(status, transit, expected):
    row = wb.normalize(
        {"supplyID": 1, "preorderID": 2}, {"statusID": status, "transitWarehouseID": transit}, [wb_good()]
    )
    assert row.stage == expected
    assert row.key == "preorder:2"
    assert row.items[0].barcode == "000123"


def test_wb_accepted_at_sc_can_still_be_awaiting_placement():
    row = wb.normalize(
        {"supplyID": 1}, {"statusID": 5}, [wb_good(acceptedQuantity=100, readyForSaleQuantity=60)]
    )
    assert row.stage == "placement"
    assert row.remaining_quantity == 40
    assert "СЦ" in row.note


def test_transit_acceptance_does_not_zero_the_goods_still_travelling():
    row = supply().model_copy(
        update={"items": (InboundItem(article="sku", quantity=100, accepted_quantity=100),)}
    )
    assert row.remaining_quantity == 100
    assert row.model_copy(update={"stage": "acceptance"}).remaining_quantity == 0


def test_wb_closed_shortage_does_not_stay_in_transit():
    row = wb.normalize(
        {"supplyID": 1}, {"statusID": 5}, [wb_good(acceptedQuantity=95, readyForSaleQuantity=95)]
    )
    assert row.stage == "discrepancy"
    assert row.items[0].shortage_quantity == 5
    assert row.remaining_quantity is None


def test_wb_uses_preorder_id_for_drafts_and_reads_all_goods(monkeypatch):
    request = Mock(
        side_effect=[
            [{"preorderID": 22, "supplyID": None, "statusID": 2}],
            {"statusID": 2},
            [wb_good()],
        ]
    )
    monkeypatch.setattr(wb.api, "_request", request)
    result = wb.load("token")
    assert len(result) == 1
    assert request.call_args_list[1].args[1].endswith("/supplies/22")
    assert request.call_args_list[1].kwargs["params"] == {"isPreorderID": "true"}
    assert request.call_args_list[2].kwargs["params"]["isPreorderID"] == "true"


def test_wb_refetches_old_open_supply_outside_listing(monkeypatch):
    old = supply(key="supply:123", order_id="").model_copy(update={"supply_id": "123"})
    request = Mock(
        side_effect=[[], {"statusID": 5}, [wb_good(acceptedQuantity=100, readyForSaleQuantity=100)]]
    )
    monkeypatch.setattr(wb.api, "_request", request)
    result = wb.load("token", (old,))
    assert result[0].stage == "completed"


def test_wb_refuses_truncated_listing(monkeypatch):
    monkeypatch.setattr(wb, "MAX_PAGES", 1)
    monkeypatch.setattr(wb.api, "_request", Mock(return_value=[{"supplyID": i + 1} for i in range(1000)]))
    with pytest.raises(InboundSourceError, match="не полностью"):
        wb.list_supplies("token")


def test_wb_refuses_empty_composition_for_nonempty_supply():
    with pytest.raises(InboundSourceError, match="пустой состав"):
        wb.normalize({"supplyID": 1}, {"statusID": 6, "quantity": 5}, [])


def ozon_good(**updates):
    return {
        "sku": 11,
        "offer_id": "oz-article",
        "barcode": "000123",
        "name": "Товар",
        "quantity": 100,
        **updates,
    }


def ozon_order(**updates):
    return {
        "order_id": 10,
        "order_number": "ORDER10",
        "state": "READY_TO_SUPPLY",
        "created_date": NOW.isoformat(),
        **updates,
    }


def test_ozon_uses_each_supply_state_and_bundle(monkeypatch):
    calls = []

    def request(path, client_id, key, payload):
        calls.append((path, payload))
        if path.endswith("/list"):
            return {"order_ids": [10], "last_id": "10"}
        if path.endswith("/get"):
            return {
                "orders": [
                    ozon_order(
                        supplies=[
                            {"supply_id": 101, "bundle_id": "b1", "state": "IN_TRANSIT"},
                            {"supply_id": 102, "bundle_id": "b2", "state": "READY_TO_SUPPLY"},
                        ]
                    )
                ]
            }
        return {
            "items": [ozon_good(quantity=20 if payload["bundle_ids"] == ["b1"] else 30)],
            "has_next": False,
        }

    monkeypatch.setattr(ozon.api, "request", request)
    result = ozon.load("client", "key")
    assert [(row.stage, row.quantity) for row in result] == [("transit", 20), ("planned", 30)]
    assert set(calls[0][1]["filter"]["states"]) == set(ozon.STATES)
    assert [payload["bundle_ids"] for path, payload in calls if path.endswith("/bundle")] == [["b1"], ["b2"]]
    assert all(row.accepted_quantity is None for row in result)


def test_ozon_reads_bundle_pagination_and_rejects_loop(monkeypatch):
    request = Mock(
        side_effect=[
            {"items": [ozon_good(sku=1)], "has_next": True, "last_id": "one"},
            {"items": [ozon_good(sku=2)], "has_next": False},
        ]
    )
    monkeypatch.setattr(ozon.api, "request", request)
    assert len(ozon.bundle_items("client", "key", "bundle")) == 2
    assert request.call_args_list[1].args[3]["last_id"] == "one"
    request.side_effect = None
    request.return_value = {"items": [ozon_good()], "has_next": True, "last_id": "same"}
    with pytest.raises(InboundSourceError, match="повторил"):
        ozon.bundle_items("client", "key", "bundle")


def test_ozon_fact_and_shortage_come_from_acts():
    acts = {"supply_acts": [{"items": [{"sku_info": {"sku": 11}, "fact_quantity": 95}]}]}
    row = ozon.normalize(ozon_order(), {"supply_id": 101, "state": "COMPLETED"}, [ozon_good()], acts)
    assert row.stage == "discrepancy"
    assert row.accepted_quantity == 95
    assert row.items[0].shortage_quantity == 5


def test_ozon_does_not_add_duplicate_or_conflicting_acts():
    acts = {
        "supply_acts": [
            {"items": [{"sku_info": {"sku": 11}, "fact_quantity": value}]} for value in [90, 100, 100]
        ]
    }
    quantities, _, warning = ozon.act_quantities(acts)
    assert quantities == {"11": None}
    assert warning


@pytest.mark.parametrize("status", [403, 429, 503])
def test_ozon_act_failure_keeps_composition_and_stops_further_act_requests(monkeypatch, status):
    calls = []

    def request(path, client_id, key, payload):
        calls.append((path, payload))
        if path.endswith("/list"):
            return {"order_ids": [10]}
        if path.endswith("/act/product/get"):
            assert isinstance(payload["supply_id"], int)
            raise ozon.api.OzonApiError(status)
        if path.endswith("/get"):
            return {
                "orders": [
                    ozon_order(
                        supplies=[
                            {
                                "supply_id": value,
                                "bundle_id": str(value),
                                "state": "ACCEPTANCE_AT_STORAGE_WAREHOUSE",
                            }
                            for value in (101, 102)
                        ]
                    )
                ]
            }
        return {"items": [ozon_good()], "has_next": False}

    monkeypatch.setattr(ozon.api, "request", request)
    result = ozon.load("client", "key")
    assert len(result) == 2
    assert all(row.quantity == 100 and row.accepted_quantity is None and row.warning for row in result)
    assert sum(path.endswith("/act/product/get") for path, _ in calls) == 1


def test_ozon_supply_methods_share_one_rate_limit_bucket(monkeypatch):
    monkeypatch.setattr(ozon.api, "_interval", {})
    monkeypatch.setattr(ozon.api, "_calm_streak", {})
    base = ozon.api.THROTTLED_PATHS["supply-orders"]
    assert ozon.api._note_rate_limit("/v3/supply-order/list") == base * 2
    assert ozon.api._note_rate_limit("/v1/supply-order/act/product/get") == base * 4
    assert set(ozon.api._interval) == {"supply-orders"}


@pytest.mark.parametrize(
    ("status", "stage"),
    [
        ("ACCEPTED_BY_WAREHOUSE_SYSTEM", "planned"),
        ("ARRIVED_TO_XDOC_SERVICE", "transit"),
        ("SHIPPED_TO_SERVICE", "transit"),
        ("WAREHOUSE_HANDLING", "acceptance"),
        ("TRANSIT_MOVING", "unknown"),
    ],
)
def test_yandex_status_semantics(status, stage):
    row = yandex.normalize(
        {"id": 9},
        {"id": {"id": 1}, "status": status},
        [
            {
                "offerId": "article",
                "counters": {"planCount": 100, "factCount": 30},
            }
        ],
    )
    assert row.stage == stage
    assert row.items[0].accepted_quantity == 30
    assert row.key == "9:1"


def test_yandex_multiple_fby_campaigns_and_children_without_double_count(monkeypatch):
    requests = []
    parent = {
        "id": {"id": 10},
        "type": "SUPPLY",
        "subtype": "VIRTUAL_DISTRIBUTION_CENTER",
        "status": "SHIPPED_TO_SERVICE",
        "transitLocation": {"name": "Транзит"},
        "childrenLinks": [{"type": "VIRTUAL_DISTRIBUTION", "id": {"id": 11}}],
    }
    child = {
        "id": {"id": 11},
        "type": "SUPPLY",
        "subtype": "VIRTUAL_DISTRIBUTION_CENTER_CHILD",
        "status": "SHIPPED_TO_SERVICE",
        "parentLink": {"type": "VIRTUAL_DISTRIBUTION", "id": {"id": 10}},
    }
    movement = {
        "id": {"id": 12},
        "type": "SUPPLY",
        "subtype": "MOVEMENT_SUPPLY",
        "status": "SHIPPED_TO_SERVICE",
    }

    def request(path, key, payload, params):
        requests.append((path, payload))
        if path.endswith("/items"):
            assert payload == {"requestId": 11}
            return {"items": [{"offerId": "sku", "counters": {"planCount": 50, "factCount": 0}}]}
        return {"requests": [parent, child, movement]}

    monkeypatch.setattr(yandex.api, "request", request)
    result = yandex.load(
        "key", [{"id": 1, "scheme": "fby"}, {"id": 2, "scheme": "fbs"}, {"id": 3, "scheme": "fby"}]
    )
    assert [row.key for row in result] == ["1:11", "3:11"]
    assert sum(row.quantity for row in result) == 100
    assert result[0].parent_key == "1:10"
    assert result[0].transit_warehouse == "Транзит"
    assert all("/campaigns/2/" not in path for path, _ in requests)


def test_yandex_refuses_incomplete_parent_children(monkeypatch):
    monkeypatch.setattr(
        yandex.api,
        "request",
        Mock(
            return_value={
                "requests": [
                    {
                        "id": {"id": 1},
                        "type": "SUPPLY",
                        "subtype": "VIRTUAL_DISTRIBUTION_CENTER",
                        "childrenLinks": [{"type": "VIRTUAL_DISTRIBUTION", "id": {"id": 2}}],
                    }
                ]
            }
        ),
    )
    with pytest.raises(InboundSourceError, match="дочерние"):
        yandex.load("key", [{"id": 1, "scheme": "fby"}])


def test_yandex_closed_child_replaces_old_open_parent_even_without_parent_details(monkeypatch):
    old = supply("1:10", campaign_id="1").model_copy(update={"supply_id": "10"})
    request = Mock(
        side_effect=[
            {
                "requests": [
                    {
                        "id": {"id": 11},
                        "type": "SUPPLY",
                        "subtype": "VIRTUAL_DISTRIBUTION_CENTER_CHILD",
                        "status": "FINISHED",
                        "updatedAt": "2020-01-01T00:00:00Z",
                        "parentLink": {"type": "VIRTUAL_DISTRIBUTION", "id": {"id": 10}},
                    }
                ]
            },
            {"requests": []},
            {"items": [{"offerId": "sku", "counters": {"planCount": 100, "factCount": 100}}]},
        ]
    )
    monkeypatch.setattr(yandex.api, "request", request)
    result = yandex.load("key", [{"id": 1, "scheme": "fby"}], (old,))
    assert len(result) == 1
    assert result[0].parent_key == "1:10"
    assert result[0].stage == "completed"


def test_yandex_pagination_requires_advancing_cursor(monkeypatch):
    monkeypatch.setattr(
        yandex.api,
        "request",
        Mock(return_value={"items": [{"offerId": "sku"}], "paging": {"nextPageToken": "same"}}),
    )
    with pytest.raises(InboundSourceError, match="повторил"):
        yandex.paged("key", "/items", "items", {})


@pytest.fixture
def repository(database_path):
    return SqlAlchemyInboundRepository(database_for_path(database_path).session_factory)


def test_repository_preserves_stock_and_other_stores_on_success_and_failure(repository, database_path):
    db.replace_catalog(
        "rimili", "WB", [{"article": "100 / M", "barcode": "000123", "name": "Название"}], NOW.isoformat()
    )
    db.upsert_ff_stock("rimili", "100 / M", "ФФ", 20, NOW.isoformat(), "WB")
    db.upsert_mp_stock("rimili", "100 / M", "WB", "fbo", 30, NOW.isoformat())
    service = InboundSupplyService(repository, lambda target, previous: (supply(),), clock=lambda: NOW)
    targets = (("rimili", "WB"), ("toyka", "WB"), ("rimili", "OZON"))
    result = service.sync(targets)
    assert len(result) == 3
    assert all(item["ok"] for item in result.values())
    reports = service.report(targets)
    assert reports[0].supplies[0].items[0].article == "100 / M"
    assert reports[0].supplies[0].items[0].name == "Название"
    assert reports[1].supplies[0].items[0].name == ""
    service.clock = lambda: NOW + timedelta(minutes=2)
    service.loader = Mock(side_effect=InboundSourceError("Нет доступа к поставкам"))
    service.sync((targets[0],))
    after = service.report(targets)
    assert after[0].status == "error"
    assert after[0].supplies == reports[0].supplies
    assert after[0].last_success == NOW.isoformat()
    assert after[1:] == reports[1:]
    with database_for_path(database_path).session_factory() as session:
        assert session.scalar(select(FulfillmentStockRecord.quantity)) == 20
        assert session.scalar(select(MarketplaceStockRecord.quantity)) == 30


def test_repository_lease_prevents_overlap_and_old_writer(repository):
    target = ("rimili", "WB")
    assert repository.claim(target, "old", NOW)
    assert not repository.claim(target, "second", NOW + timedelta(minutes=1))
    later = NOW + timedelta(hours=3)
    assert repository.claim(target, "new", later)
    assert not repository.finish(target, "old", later, supplies=(supply("old"),))
    assert repository.finish(target, "new", later, supplies=(supply("new"),))
    assert repository.read((target,))[0].supplies[0].key == "new"
    assert not repository.claim(target, "repeat", later + timedelta(seconds=20))


def test_service_retains_missing_supply_as_unconfirmed_and_replaces_parent(repository):
    target = ("rimili", "YANDEX MARKET")
    service = InboundSupplyService(
        repository, lambda target, previous: (supply("parent"), supply("missing")), clock=lambda: NOW
    )
    service.sync((target,))
    service.clock = lambda: NOW + timedelta(minutes=2)
    service.loader = lambda target, previous: (supply("child", parent_key="parent"),)
    service.sync((target,))
    report = service.report((target,))[0]
    by_key = {item.key: item for item in report.supplies}
    assert set(by_key) == {"child", "missing"}
    assert by_key["missing"].unavailable
    assert by_key["missing"].remaining_quantity is None
    assert by_key["missing"].checked_at == NOW.isoformat()


def test_service_isolates_target_errors_and_never_leaks_unexpected_exception(repository):
    def loader(target, previous):
        if target[0] == "rimili":
            raise RuntimeError("secret-api-key")
        return (supply(),)

    service = InboundSupplyService(repository, loader, clock=lambda: NOW)
    result = service.sync((("rimili", "WB"), ("toyka", "OZON")))
    assert not result["rimili / WB"]["ok"]
    assert "secret" not in result["rimili / WB"]["error"]
    assert result["toyka / OZON"]["ok"]


def test_sync_all_visits_every_configured_store_and_marketplace(monkeypatch):
    service = Mock()
    monkeypatch.setattr(inbound_supplies, "build_service", lambda: service)
    monkeypatch.setattr(sync_settings, "enabled_stores", lambda name, marketplace: tuple(STORES))
    inbound_supplies.sync_all()
    targets = service.sync.call_args.args[0]
    assert set(targets) == {(slug, mp) for slug in STORES for mp in inbound_supplies.MARKETPLACES}
    jobs = {job.name: job for job in background._jobs(__import__("asyncio").Event())}
    assert jobs["inbound_supplies_sync"].next_delay() == 1800


def sign_in(client, application, user):
    application.state.container.identity.user_for_token = lambda token: user
    client.cookies.set(middleware.auth.SESSION_COOKIE, "x" * 32)


def test_routes_are_scoped_by_store_and_marketplace(client, application, user_factory):
    user = user_factory(role=Role.USER).model_copy(
        update={
            "access_profile": AccessProfile.MARKETPLACE_MANAGER,
            "access_scopes": (MarketplaceAccessScope(store_slug="toyka", marketplace="OZON"),),
        }
    )
    sign_in(client, application, user)
    page = client.get("/stock/inbound")
    assert page.status_code == 200, page.text
    assert "data-inbound-page" in page.text
    assert '<option value="toyka">TOYKA</option>' in page.text
    assert '<option value="rimili">' not in page.text
    data = client.get("/stock/inbound/data").json()
    assert [(row["store_slug"], row["marketplace"]) for row in data["targets"]] == [("toyka", "OZON")]
    assert client.get("/stock/inbound/data?store=rimili").status_code == 403
    assert client.get("/stock/inbound/data?store=toyka&mp=WB").status_code == 403
    assert client.post("/stock/inbound/sync", json={"store": "rimili"}).status_code == 403


def test_refresh_and_readonly_access(client, application, user_factory, database_path):
    service = application.state.container.inbound_supplies
    service.loader = lambda target, previous: (supply(),)
    sign_in(client, application, user_factory())
    response = client.post("/stock/inbound/sync", json={"store": "toyka", "marketplace": "OZON"})
    assert response.status_code == 202, response.text
    assert response.json()["started"] == 1
    listing = client.get("/stock/inbound/data?store=toyka&mp=OZON")
    assert listing.status_code == 200
    assert listing.json()["targets"][0]["supplies"][0]["quantity"] == 100
    assert listing.headers["cache-control"] == "no-store"
    with database_for_path(database_path).session_factory() as session:
        assert len(session.scalars(select(InboundSupplySnapshotRecord)).all()) == 1
    user = user_factory(role=Role.USER).model_copy(
        update={
            "section_access": {SectionName.STOCK: SectionAccessLevel.READ},
        }
    )
    sign_in(client, application, user)
    assert client.get("/stock/inbound").status_code == 200
    assert "data-inbound-refresh hidden" in client.get("/stock/inbound").text
    assert client.post("/stock/inbound/sync", json={}).status_code == 403
