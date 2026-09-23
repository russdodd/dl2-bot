from dl2model import shipping
from dl2model import constants as C
import pytest


def test_distance():
    assert shipping.distance("Boston", "New York") == 74
    # symmetric
    assert shipping.distance("New York", "Boston") == 74
    # same city -> 0
    assert shipping.distance("Boston", "Boston") == 0


def test_cost_boston_ny():
    assert shipping.shipping_cost("Boston", "New York", 100, "Sharp Ship", 3) == 370
    assert shipping.shipping_cost("Boston", "New York", 100, "Quicker Shipper", 3) == 74
    # overnight (1-day) = 4x the 3-day
    assert shipping.shipping_cost("Boston", "New York", 100, "Quicker Shipper", 1) == 296


def test_cost_single_divide_at_end():
    # 1 unit at the cheapest rate rounds down to 0 (per-unit < $1), proving the
    # single divide-at-the-end: a per-unit-first approach would round differently.
    assert shipping.shipping_cost("Boston", "New York", 1, "Quicker Shipper", 3) == 0
    # 2-day is exactly 2x the 3-day cost
    three = shipping.shipping_cost("Boston", "New York", 100, "Sharp Ship", 3)
    two = shipping.shipping_cost("Boston", "New York", 100, "Sharp Ship", 2)
    assert two == 2 * three


def test_per_unit_rates():
    assert shipping.per_unit_rate("Boston", "New York", "Sharp Ship", 3) == pytest.approx(3.70)
    assert shipping.per_unit_rate("Boston", "New York", "Quicker Shipper", 3) == pytest.approx(0.74)
    assert shipping.per_unit_rate("Boston", "New York", "Courier Stan", 3) == pytest.approx(1.48)
    assert shipping.per_unit_rate("Boston", "New York", "International Couriers", 3) == pytest.approx(7.40)
    # overnight is 4x the 3-day per-unit rate
    assert shipping.per_unit_rate("Boston", "New York", "Sharp Ship", 1) == pytest.approx(4 * 3.70)


def test_success_prob():
    assert shipping.success_prob("Quicker Shipper") == 0.50
    assert shipping.success_prob("Courier Stan") == 0.75
    assert shipping.success_prob("Sharp Ship") == 0.90
    assert shipping.success_prob("International Couriers") == 0.99


def test_expected_transit():
    assert shipping.expected_transit_days(3) == 3.75
    assert shipping.expected_transit_days(2) == 2.50
    assert shipping.expected_transit_days(1) == 1.25


def test_ev_all_or_nothing():
    # 100 units worth 1000 each via International (0.99), cost subtracted once
    ev = shipping.expected_ship_value("Boston", "New York", 100, 1000, "International Couriers", 3)
    cost = shipping.shipping_cost("Boston", "New York", 100, "International Couriers", 3)
    assert ev == pytest.approx(0.99 * 100 * 1000 - cost)


def test_ev_can_be_negative():
    # Worthless goods -> EV is just the (negative) sunk fee, fee counted once.
    cost = shipping.shipping_cost("Boston", "New York", 100, "Sharp Ship", 3)
    ev = shipping.expected_ship_value("Boston", "New York", 100, 0, "Sharp Ship", 3)
    assert ev == pytest.approx(-cost)
