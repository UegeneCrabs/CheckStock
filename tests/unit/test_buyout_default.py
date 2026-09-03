import json
from datetime import date

import pytest

from app import db
from app import unit_economics_1c as economics
from app import unit_economics_1c_history as history
from app.dto.unit_economics_1c import (
    UnitEconomics1CCabinetSettingsRequest,
    UnitEconomics1CProductSettings,
)
from app.wb import funnel_orders
from app.web.routers import unit_economics as routes

DAY = date(2026, 9, 2)
NOW = "2026-09-02T23:59:00+03:00"


def save_default(store, percent):
    return db.save_unit_economics_1c_cabinet_settings(
        store, UnitEconomics1CCabinetSettingsRequest(default_buyout_percent=percent),
        updated_at=NOW, updated_by_user_id=1, updated_by_name="Test",
    )


@pytest.mark.parametrize("raw, expected", [(0, 80), (None, 80), (52.5, 52.5), (100, 100)])
def test_effective_percentage_resolves_only_zero_or_missing(raw, expected):
    assert economics.resolve_buyout_percent(raw, 80) == expected


def test_loaded_metrics_use_cabinet_default_without_overwriting_wb(database_path):
    save_default("tris", 80)
    save_default("rimili", 60)
    for store in ("tris", "rimili"):
        funnel_orders._replace_day(store, DAY, [("123", "V-123", "Товар", 10, 10000)])
        funnel_orders._replace_product_metrics(store, DAY, DAY, [("123", 10, 10000, 0)])
        db.replace_unit_economics_1c_daily_advertising(
            store, DAY.isoformat(), DAY.isoformat(),
            [{"store_slug": store, "nm_id": "123", "day": DAY.isoformat(),
              "marketplace": "WB", "spend": 400, "synced_at": NOW}],
        )
    metrics = economics.load_product_metrics(("tris", "rimili"), period_days=1, today=DAY)
    tris = metrics[("tris", "123")]
    assert tris["raw_buyout_percent"] == 0
    assert tris["buyout_percent"] == 80
    assert tris["spend_per_order"] == 50
    assert tris["drr"] == tris["daily"][0]["drr"] == 5
    assert metrics[("rimili", "123")]["buyout_percent"] == 60
    assert all(row["buyout_percent"] == 0 for row in db.get_unit_economics_1c_funnel_product_metrics(("tris", "rimili")))
    save_default("tris", 50)
    changed = economics.load_product_metrics(("tris",), period_days=1, today=DAY)[("tris", "123")]
    assert changed["buyout_percent"] == 50
    assert changed["spend_per_order"] == 80


@pytest.mark.parametrize("raw", [0, None, 65])
def test_calculator_current_and_snapshot_use_the_same_effective_percentage(database_path, raw):
    cabinet = save_default("tris", 80)
    product_settings = UnitEconomics1CProductSettings(
        store_slug="tris", article="123", delivery_wb_rub=50, return_cost_rub=25,
    )
    metrics = {"buyout_percent": raw, "orders_count": 10, "orders_amount": 10000, "spend": 400}
    price = {"retail_price": 1000}
    reference = {"purchase_price": 300}
    product = routes._unit_economics_1c_mock_product(
        "tris", {"article": "123"}, price_snapshot=price, product_metrics=metrics,
        product_settings=product_settings, product_reference=reference,
        default_buyout_percent=80,
    )
    expected = raw or 80
    details = product["details"]
    assert details["buyout_percent"] == product["current_economics"]["buyout_percent"] == expected
    assert product["advertising"]["buyout_percent"] == expected
    assert product["advertising"]["buyout_default_applied"] is (not bool(raw))
    assert details["advertising_per_unit"] == round(400 / (10 * expected / 100), 2)
    assert details["delivery_with_returns"] == economics.calculate_delivery_with_returns(50, expected, 25, 0)
    row = history.calculate_snapshot_row(
        snapshot_day=DAY, store_slug="tris", article="123", price_snapshot=price,
        product_metrics=metrics, product_settings=product_settings,
        product_reference=reference, cabinet=cabinet, captured_at=NOW,
    )
    inputs = json.loads(row["inputs_json"])
    assert inputs["buyout_percent"] == expected
    assert inputs["raw_buyout_percent"] == raw
    assert inputs["default_buyout_percent"] == 80
    assert inputs["advertising_per_unit"] == details["advertising_per_unit"]
    db.save_unit_economics_1c_daily_margin_snapshots([row])
    save_default("tris", 50)
    saved = db.get_unit_economics_1c_daily_margin_snapshots(("tris",), DAY.isoformat(), DAY.isoformat())[0]
    assert history.snapshot_buyout_percent(saved) == expected
    historical = routes._report_historical_economics(
        date_from=DAY, date_to=DAY, daily_orders={DAY.isoformat(): metrics},
        margin_snapshots={DAY.isoformat(): saved}, live_day=date(2026, 9, 3),
        live_unit_margin=None, live_purchase_price=None, fallback_buyout_percent=50,
        daily_advertising={DAY.isoformat(): 400},
    )
    assert historical["orders"] == 10 * expected / 100
    assert historical["buyout_percent"] == expected
    assert historical["margin"] == round(saved["unit_margin"] * (10 * expected / 100) - 400, 2)
    daily = routes._report_daily_calculations(
        date_from=DAY, date_to=DAY, daily_orders={DAY.isoformat(): metrics},
        margin_snapshots={DAY.isoformat(): saved}, live_day=date(2026, 9, 3),
        live_snapshot=None, fallback_buyout_percent=50,
        daily_advertising={DAY.isoformat(): 400},
    )
    assert daily[0]["buyout_percent"] == expected


def test_new_product_without_metrics_uses_default():
    product = routes._unit_economics_1c_mock_product(
        "tris", {"article": "new"}, price_snapshot={"retail_price": 1000},
        default_buyout_percent=75,
    )
    assert product["details"]["buyout_percent"] == 75
    assert product["current_economics"]["buyout_percent"] == 75
    assert product["advertising"]["buyout_default_applied"]


def test_legacy_client_save_does_not_erase_default(database_path):
    save_default("tris", 80)
    db.save_unit_economics_1c_cabinet_settings(
        "tris", UnitEconomics1CCabinetSettingsRequest(buyout_period_days=21),
        updated_at=NOW, updated_by_user_id=1, updated_by_name="Test",
    )
    assert db.get_unit_economics_1c_cabinet_settings("tris").default_buyout_percent == 80
