"""Exact shipping cost + delivery/failure expected value.

cost = distance * rate * speed_mult * units // 1000  (single divide at the end).
Failure is all-or-nothing: goods + already-paid fee lost, no extra fine. Success
prob == the shipper's nominal 50/75/90/99. A 3-day grace window applies at the
destination. This replaces the empirically-measured flat per-unit rate store.
"""
from . import constants as C


def distance(origin: str, dest: str) -> int:
    """abs(CITY_POS[origin] - CITY_POS[dest])."""
    return abs(C.CITY_POS[origin] - C.CITY_POS[dest])


def shipping_cost(origin: str, dest: str, units: int, shipper: str, days: int) -> int:
    """distance * SHIPPERS[shipper][0] * SPEED_MULT[days] * units // 1000.
    Divide ONCE, at the end (do not round a per-unit rate first)."""
    rate = C.SHIPPERS[shipper][0]
    speed_mult = C.SPEED_MULT[days]
    # All operands are non-negative, so plain // matches the C truncation.
    return distance(origin, dest) * rate * speed_mult * units // C.SHIP_COST_DIVISOR


def per_unit_rate(origin: str, dest: str, shipper: str, days: int) -> float:
    """Unrounded conceptual $/unit = distance*rate*speed_mult/1000."""
    rate = C.SHIPPERS[shipper][0]
    speed_mult = C.SPEED_MULT[days]
    return distance(origin, dest) * rate * speed_mult / C.SHIP_COST_DIVISOR


def success_prob(shipper: str) -> float:
    """Nominal delivery probability (SHIPPERS[shipper][1])."""
    return C.SHIPPERS[shipper][1]


def expected_transit_days(days: int) -> float:
    """Scheduled 3/2/1 -> mean arrival 3.75/2.50/1.25 (20% slip/day)."""
    return C.EXPECTED_TRANSIT_DAYS[days]


def expected_ship_value(origin, dest, units, unit_value, shipper, days) -> float:
    """EV of shipping `units` each worth `unit_value` at the destination:
    success_prob*units*unit_value - shipping_cost(...). (unit_value is the
    caller's decayed/expected sell price; fee is sunk either way.)"""
    return (
        success_prob(shipper) * units * unit_value
        - shipping_cost(origin, dest, units, shipper, days)
    )
