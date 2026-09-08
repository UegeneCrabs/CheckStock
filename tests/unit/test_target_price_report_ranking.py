"""Target-report ordering, tag export and scoped single-product reads."""

from datetime import date
from io import BytesIO

import openpyxl
import pytest

from app import db
from app import unit_economics_1c as economics
from app import unit_economics_1c_target_prices as pricing
from app.dto.identity import Role
from app.web import middleware


@pytest.fixture
def report_data(client, application, monkeypatch, user_factory):
    user = user_factory()
    monkeypatch.setattr(application.state.container.identity, "user_for_token", lambda token: user)
    client.cookies.set(middleware.auth.SESSION_COOKIE, "report-test")
    products = [
        {"article": "20", "name": "Высокая цена"},
        {"article": "10 / vendor+", "name": "Высокий оборот"},
        {"article": "30", "name": "Без заказов"},
    ]
    monkeypatch.setattr(db, "get_stock_items", lambda slug, marketplace: products if slug == "rimili" else [])
    monkeypatch.setattr(db, "get_unit_economics_1c_product_reference_rows", lambda stores: [
        {"store_slug": "rimili", "article": "10 / vendor+", "abc_code": " a ", "manager": "User 1"},
        {"store_slug": "rimili", "article": "20", "abc_code": "D", "manager": "Другой менеджер"},
    ])
    monkeypatch.setattr(pricing, "closed_week", lambda today=None: (date(2026, 8, 27), date(2026, 9, 2)))
    source_orders = [
        {"article": "10", "day": "2026-08-27", "orders_amount": 1000},
        {"article": "10", "day": "2026-09-02", "orders_amount": 9000},
        {"article": "20", "day": "2026-08-27", "orders_amount": 3000},
        {"article": "20", "day": "2026-08-26", "orders_amount": 100000},
        {"article": "20", "day": "2026-09-03", "orders_amount": 100000},
    ]
    periods = []

    def daily_orders(stores, start, end):
        periods.append((start, end))
        return [
            {"store_slug": "rimili", "orders_count": 1, **row}
            for row in source_orders if "rimili" in stores and start <= row["day"] <= end
        ]

    monkeypatch.setattr(db, "get_unit_economics_1c_funnel_daily_order_rows", daily_orders)
    monkeypatch.setattr(db, "get_unit_economics_1c_daily_advertising", lambda *args: [
        {"store_slug": "rimili", "nm_id": article, "day": "2026-08-27", "spend": 100}
        for article in ("10", "20")
    ])
    # Preserve the calculation period metrics independently of the full turnover
    # ranking: the larger product has no ad coverage for its second order day.
    period_metrics = {}
    for article, amount in (("10", 1000), ("20", 3000)):
        metrics = economics.empty_product_metrics(period_days=7, today=date(2026, 9, 2))
        metrics.update({"orders_amount": amount, "orders_count": 1, "buyout_percent": 80})
        period_metrics[("rimili", article)] = metrics
    monkeypatch.setattr(economics, "load_product_metrics", lambda *args, **kwargs: period_metrics)
    return client, periods


def test_target_report_ranks_full_closed_week_turnover_and_exposes_tag_code(report_data):
    client, periods = report_data
    response = client.get("/api/unit-economics-1c/reports/target-price", params={
        "store": "rimili", "date_to": "2026-09-03",
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert (payload["period_from"], payload["period_to"]) == ("2026-08-27", "2026-09-02")
    assert set(periods) == {("2026-08-27", "2026-09-02")}
    rows = payload["rows"]
    assert [row["article"] for row in rows] == ["10 / vendor+", "20", "30"]
    assert [row["orders_amount"] for row in rows] == [10000, 3000, 0]
    assert [row["weekly"]["orders_amount"] for row in rows] == [1000, 3000, 0]
    assert [row["code"] for row in rows] == ["A", "D", ""]


def test_single_product_query_keeps_report_values_and_scope(report_data, application, monkeypatch, user_factory):
    client, _ = report_data
    path = "/api/unit-economics-1c/reports/target-price"
    full = client.get(path, params={"store": "rimili"}).json()["rows"]
    selected = client.get(path, params={"store": "rimili", "article": "10 / vendor+"})
    assert selected.status_code == 200, selected.text
    assert selected.json()["rows"] == [full[0]]
    assert client.get(path, params={"store": "rimili", "article": "missing"}).json()["rows"] == []

    user = user_factory(role=Role.USER, stores=("rimili",))
    monkeypatch.setattr(application.state.container.identity, "user_for_token", lambda token: user)
    forbidden_store = client.get(path, params={"store": "tris", "article": "10 / vendor+"})
    assert forbidden_store.status_code == 403
    forbidden_product = client.get(path, params={"store": "rimili", "article": "20"})
    assert forbidden_product.status_code == 200
    assert forbidden_product.json()["rows"] == []
    own = client.get(path, params={"store": "rimili", "article": "10 / vendor+"})
    assert own.status_code == 200, own.text
    assert [row["article"] for row in own.json()["rows"]] == ["10 / vendor+"]


def test_target_export_places_code_after_store_without_turnover(report_data):
    client, _ = report_data
    response = client.post("/api/unit-economics-1c/reports/target-price.xlsx", json={"rows": [{
        "store_slug": "rimili", "store_name": "RIMILI", "article": "10 / vendor+", "name": "Товар",
        "code": "A", "current_price": 1234, "current_drr": 0, "current_roi": 10,
        "target_price": 1567, "target_drr": 8, "target_roi": 20,
    }]})
    assert response.status_code == 200, response.text
    sheet = openpyxl.load_workbook(BytesIO(response.content), data_only=True).active
    headers = [cell.value for cell in sheet[1]]
    assert headers[2:4] == ["Магазин", "Код"]
    assert len(headers) == 10
    assert not any("оборот" in header.lower() or header == "ТО" for header in headers)
    assert sheet["D2"].value == "A"
    assert sheet["D2"].number_format == "General"
    assert sheet["E2"].value == 1234
    assert sheet["F2"].value == 0
    assert sheet["J2"].value == 20
    assert sheet.auto_filter.ref == "A1:J2"
