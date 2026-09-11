from datetime import date

import pytest

from app.dto.yandex_economics import EconomicsValues
from app.repositories import unit_economics_yandex as metrics
from app.repositories import yandex_economics as repository
from app.yandex import economics, economics_api
from app.yandex.economics_calculation import aggregate, calculate


def inputs():
    return {
        "seller_price": 1000,
        "buyer_price": 800,
        "purchase_price": 300,
        "fulfillment_cost": 20,
        "commission_percent": 20,
        "payment_acceptance": 1,
        "payment_transfer_percent": 2,
        "delivery_cost": 50,
        "return_cost": 30,
        "storage_per_day": 1,
        "storage_days": 10,
        "transit_cost": 5,
        "other_percent": 1,
        "other_cost": 4,
        "tax_percent": 5,
        "capital_percent": 0,
        "turnover_days": 20,
        "loss_percent": 0,
        "disposal_cost": 0,
        "disposal_percent": 0,
        "buyout_percent": 80,
        "plan_drr": 10,
        "advertising_mode": "plan",
        "tax_base": "buyer",
        "frequency": "WEEKLY",
        "payment_delay_weeks": 0,
    }


def test_profit_allocates_return_and_advertising_once():
    result = calculate(inputs())
    # YM sheet: return cost * (1-buyout), without another division by buyout.
    assert result["margin"] == 234
    assert result["roi"] == 78
    assert result["costs"]["returns"] == 6
    actual = calculate({**inputs(), "advertising_mode": "actual"}, advertising_spend=800, orders_count=10)
    assert actual["margin"] == result["margin"]


def test_missing_zero_negative_profit_and_advertising_without_orders():
    assert calculate({**inputs(), "purchase_price": None})["margin"] is None
    free = calculate({**inputs(), "purchase_price": 0})
    assert free["margin"] is not None and free["roi"] is None
    assert calculate({**inputs(), "purchase_price": 2000})["margin"] < 0
    result = calculate({**inputs(), "advertising_mode": "actual"}, advertising_spend=10, orders_count=0)
    assert result["margin"] is None and "orders_count" in result["missing"]
    with pytest.raises(ValueError):
        EconomicsValues(purchase_price=float("nan"))


def test_override_zero_reset_conflict_and_immutable_day(database_path):
    repository.save_source("tris", "A", "initial:FBY", inputs())
    repository.save_settings("tris", "A", "FBY", {"purchase_price": 0}, 0, "tester")
    assert economics.effective("tris", "A", "FBY")["values"]["purchase_price"] == 0
    reset_preview = economics.effective("tris", "A", "FBY", scenario={"purchase_price": None})
    assert reset_preview["values"]["purchase_price"] == 300
    with pytest.raises(ValueError):
        repository.save_settings("tris", "A", "FBY", {"purchase_price": 40}, 0, "other user")
    repository.save_settings("tris", "A", "FBY", {"purchase_price": None}, 1, "tester")
    assert economics.effective("tris", "A", "FBY")["values"]["purchase_price"] == 300
    repository.save_day("tris", "A", "FBY", "2026-09-01", {"profit": 100})
    repository.save_day("tris", "A", "FBY", "2026-09-01", {"profit": 999})
    assert repository.history("tris", "2026-09-01", "2026-09-01")[0]["data"]["profit"] == 100
    assert len(repository.audit("tris", "A")) == 2
    assert repository.settings("tris", "A", "FBS")["revision"] == 0


def test_period_weights_costs_and_reports_missing_days():
    days = [
        {"day": "2026-09-01", "profit": 100, "purchase_value": 100},
        {"day": "2026-09-02", "profit": -50, "purchase_value": 900},
    ]
    result = aggregate(days, ["2026-09-01", "2026-09-02", "2026-09-03"])
    assert result["margin"] == 50 and result["roi"] == 5
    assert result["coverage"]["missing_dates"] == ["2026-09-03"]


def test_quotes_are_price_specific_and_manual_fees_win(database_path, monkeypatch):
    values = {**inputs(), "category_id": 10, "length": 10, "width": 10, "height": 10, "weight": 1}
    repository.save_source("tris", "A", "initial:FBY", values)
    monkeypatch.setattr(economics_api.tokens, "get_api_key", lambda store: "test")
    monkeypatch.setattr(economics_api.tokens, "get_campaigns", lambda store: [])
    monkeypatch.setattr(
        economics_api.api,
        "request",
        lambda *args, **kwargs: {
            "offers": [
                {
                    "tariffs": [
                        {"type": "FEE", "amount": 100, "currency": "RUR"},
                        {"type": "MIDDLE_MILE", "amount": 70, "currency": "RUR"},
                    ]
                }
            ]
        },
    )
    economics_api.quote("tris", "A", "FBY")
    state = economics.effective("tris", "A", "FBY")
    assert state["tariff"]["valid"] and state["values"]["commission_percent"] == 10
    assert not economics.effective("tris", "A", "FBY", scenario={"seller_price": 1200})["tariff"]["valid"]
    repository.save_settings("tris", "A", "FBY", {"commission_percent": 30}, 0, "tester")
    assert economics.effective("tris", "A", "FBY")["values"]["commission_percent"] == 30


def test_closure_requires_historical_inputs_and_retains_zero_order_ads(database_path):
    day = "2026-09-01"
    metrics.save_daily("tris", "orders", [], day, day, repository.now())
    metrics.save_daily(
        "tris", "advertising", [{"article": "A", "day": day, "spend": 50}], day, day, repository.now()
    )
    assert economics.close_days(("tris",), today=date(2026, 9, 2))["closed"] == 0
    repository.save_source(
        "tris",
        "A",
        "day-input:" + day + ":FBY",
        {"values": inputs(), "origins": {}, "version": 2, "basis": "today_observations"},
    )
    economics.close_days(("tris",), today=date(2026, 9, 2))
    row = repository.history("tris", day, day)[0]["data"]
    assert row["profit"] == -50 and row["purchase_value"] == 0


def test_advertising_is_allocated_between_models_without_duplication():
    orders = [
        {
            "article": "A",
            "orders_count": 10,
            "schemes": {"FBY": {"orders_count": 7}, "FBS": {"orders_count": 3}},
        }
    ]
    ads = [{"article": "A", "spend": 100}]
    assert economics.allocate_today(ads, orders, "A", "FBY") == (70, 7)
    assert economics.allocate_today(ads, orders, "A", "FBS") == (30, 3)
    assert economics.allocate_today(ads, [{"article": "A", "orders_count": 10}], "A", "FBY") == (None, None)


def test_captures_active_products_even_without_a_catalog_row(database_path):
    from datetime import datetime

    from app.domain import MOSCOW_TIMEZONE
    from app.repositories.yandex_assortment import active_articles

    article = sorted(active_articles("tris"))[0]
    repository.save_source("tris", article, "initial:FBY", inputs())
    prepare_today("tris", article)
    today = datetime.now(MOSCOW_TIMEZONE).date()
    result = economics.capture_today(("tris",), today=today)
    assert result["captured"] == 1
    repository.save_settings("tris", article, "FBY", {"purchase_price": 999}, 0, "test")
    captured = repository.sources("tris")[(article, "day-input:" + today.isoformat() + ":FBY")]
    assert captured["values"]["values"]["purchase_price"] == 300


def test_token_alias_keeps_the_canonical_store_key(tmp_path, monkeypatch):
    from app.yandex import tokens

    file = tmp_path / "tokens.json"
    file.write_text('{"sokolof": {"api_key": "test-alias"}}')
    monkeypatch.setattr(tokens, "SECRETS_PATH", file)
    assert tokens.get_api_key("sokoloff") == "test-alias"
    assert tokens.stores_with_credentials() == ["sokoloff"]


def prepare_today(store, article):
    from app.repositories import yandex_storefront

    target = {"store_slug": store, "article": article}
    yandex_storefront.save_target(target)
    yandex_storefront.seller_price(target, 1000)
    yandex_storefront.record(target, {"status": "ok", "buyer_price": 800})
    values = economics.effective(store, article, "FBY")["values"]
    components = {
        key: values[key]
        for key in ("commission_percent", "payment_acceptance", "payment_transfer_percent", "delivery_cost")
    }
    repository.save_source(
        store,
        article,
        "tariff:FBY",
        {"signature": economics.tariff_signature(values, "FBY"), "components": components},
    )


def test_current_never_uses_seed_prices_plan_drr_or_yesterdays_observations(database_path):
    from datetime import datetime, timedelta

    from app.domain import MOSCOW_TIMEZONE
    from app.repositories.core import get_connection

    today = datetime.now(MOSCOW_TIMEZONE).date()
    repository.save_source("tris", "A", "initial:FBY", inputs())
    state = economics.detail("tris", "A", advertising=(None, None), today=today)
    assert state["values"]["seller_price"] is None and state["values"]["advertising_mode"] == "actual"
    assert state["result"]["margin"] is None
    planned = economics.detail("tris", "A", mode="calculator", today=today)
    assert planned["result"]["margin"] == 234
    prepare_today("tris", "A")
    missing_ads = economics.detail("tris", "A", advertising=(None, None), today=today)
    assert missing_ads["result"]["margin"] is None and "advertising" in missing_ads["result"]["missing"]
    current = economics.detail(
        "tris",
        "A",
        advertising=(800, 10),
        today=today,
        scenario={"advertising_mode": "plan", "plan_drr": 0, "seller_price": 9999},
    )
    assert current["values"]["seller_price"] == 1000
    assert current["result"]["margin"] == 234
    yesterday = (datetime.now(MOSCOW_TIMEZONE) - timedelta(days=1)).isoformat()
    with get_connection() as conn:
        conn.execute(
            "UPDATE yandex_storefront_prices SET price_checked_at=? WHERE store_slug=? AND article=?",
            (yesterday, "tris", "A"),
        )
        conn.commit()
    stale = economics.detail("tris", "A", advertising=(800, 10), today=today)
    assert stale["values"]["buyer_price"] is None and stale["result"]["margin"] is None


def test_reference_row_matches_ym_sheet_ai3_bc3_and_aj3():
    # Original sheet row 3 (no API overrides and no rounding of input purchase price).
    values = {
        **inputs(),
        "seller_price": 5029,
        "buyer_price": 2831,
        "purchase_price": 1219.965465,
        "fulfillment_cost": 43.3545,
        "commission_percent": 44.5,
        "payment_acceptance": 0.12,
        "payment_transfer_percent": 1.6,
        "delivery_cost": 342.295,
        "return_cost": 105.845,
        "storage_per_day": 0,
        "storage_days": 30,
        "transit_cost": 5.8,
        "other_percent": 1,
        "other_cost": 0,
        "tax_percent": 8,
        "capital_percent": 18,
        "turnover_days": 50,
        "loss_percent": 1,
        "disposal_cost": 39,
        "buyout_percent": 85,
        "plan_drr": 9,
    }
    result = calculate(values)
    assert result["margin"] == 267.62
    assert result["roi"] == 21.94
    assert result["margin_percent"] == 9.45
