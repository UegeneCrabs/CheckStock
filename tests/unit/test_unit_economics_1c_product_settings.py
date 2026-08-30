from app.unit_economics_1c import (
    calculate_delivery_with_returns,
    calculate_paid_acceptance_cost,
)


def test_paid_acceptance_cost_uses_volume_steps_and_coefficient() -> None:
    assert calculate_paid_acceptance_cost(0.99, 2) == 0
    assert calculate_paid_acceptance_cost(1, 2) == 3.4
    assert calculate_paid_acceptance_cost(1.01, 2) == 6.8
    assert calculate_paid_acceptance_cost(2, 1.5) == 5.1


def test_delivery_with_returns_converts_percent_to_ratio() -> None:
    assert calculate_delivery_with_returns(100, 80, 50, 10) == 140
    assert calculate_delivery_with_returns(100, 100, 50, 10) == 110
    assert calculate_delivery_with_returns(100, 0, 50, 10) == 260
