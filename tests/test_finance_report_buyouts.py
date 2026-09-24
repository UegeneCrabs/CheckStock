from app.web.routers.finance_reports import _buyout_count_from_sources, _calculate_derived_values


def test_buyout_count_prefers_exact_financial_export() -> None:
    assert _buyout_count_from_sources(118, 114) == 118


def test_buyout_count_uses_delivery_estimate_without_financial_export() -> None:
    assert _buyout_count_from_sources(None, 119) == 119


def test_buyout_count_is_empty_without_any_source() -> None:
    assert _buyout_count_from_sources(None, None) is None


def test_marginal_profit_uses_receipt_cost_and_buyer_turnover() -> None:
    values = {
        "bank_receipt": 526_693.2,
        "sold_cost_of_goods": 343_648.7,
        "buyout_buyer_turnover": 813_379,
    }

    _calculate_derived_values(values)

    assert values["marginal_profit"] == 183_044.5
    assert values["marginal_profit_percent"] == 22.5
