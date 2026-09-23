# p03 — shipping.py (exact cost + delivery/failure EV)

**Files you own:** `dl2model/shipping.py`, `tests/test_shipping.py`
**Wave 1** (no dependencies). Read `docs/tasks/README.md` first.

## Exact rules
```
distance = |CITY_POS[origin] - CITY_POS[dest]|
cost = distance * shipper_rate * speed_mult * units // 1000   # ONE divide, at the end
```
`shipper_rate` = `SHIPPERS[shipper][0]` (10/20/50/100). `speed_mult` =
`SPEED_MULT[days]` (3-day→1, 2-day→2, overnight/1-day→4). Never round a per-unit
rate first — divide once at the end.

Delivery is **all-or-nothing**: on failure the goods AND the already-paid fee are
lost, no extra fine. `success_prob(shipper)` = the nominal 0.50/0.75/0.90/0.99
(`SHIPPERS[shipper][1]`). Each transit day has a 20% chance to slip one more day →
mean arrival 3.75 / 2.50 / 1.25 for scheduled 3/2/1 (`EXPECTED_TRANSIT_DAYS`).
A 3-day grace window applies at the destination (`SHIP_GRACE_DAYS`).

`expected_ship_value(origin,dest,units,unit_value,shipper,days)` =
`success_prob*units*unit_value - shipping_cost(...)`. `unit_value` is the
caller's expected/decayed destination sell price (that decay is p01's job — this
module just takes the number).

## Worked example (Boston→New York, distance 74)
Per-unit 3-day rates: Quicker 0.74, Stan 1.48, Sharp 3.70, International 7.40.
Overnight = 4×. So `shipping_cost("Boston","New York",100,"Sharp Ship",3)` =
`74*50*1*100//1000` = **370**.

## Tests (`tests/test_shipping.py`) — must include
```python
from dl2model import shipping
import pytest

def test_distance():
    assert shipping.distance("Boston","New York") == 74

def test_cost_boston_ny():
    assert shipping.shipping_cost("Boston","New York",100,"Sharp Ship",3) == 370
    assert shipping.shipping_cost("Boston","New York",100,"Quicker Shipper",3) == 74
    # overnight (1-day) = 4x the 3-day
    assert shipping.shipping_cost("Boston","New York",100,"Quicker Shipper",1) == 296

def test_per_unit_rates():
    assert shipping.per_unit_rate("Boston","New York","Sharp Ship",3) == pytest.approx(3.70)

def test_expected_transit():
    assert shipping.expected_transit_days(3) == 3.75
    assert shipping.expected_transit_days(1) == 1.25

def test_ev_all_or_nothing():
    # 100 units worth 1000 each via International (0.99), cost subtracted once
    ev = shipping.expected_ship_value("Boston","New York",100,1000,"International Couriers",3)
    cost = shipping.shipping_cost("Boston","New York",100,"International Couriers",3)
    assert ev == pytest.approx(0.99*100*1000 - cost)
```

## Done when
`python3 -m pytest tests/test_shipping.py -q` green, then commit + push + PR.
