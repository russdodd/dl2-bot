# p04 — risk.py (No-Scent carry detection + police encounters)

**Files you own:** `dl2model/risk.py`, `tests/test_risk.py`
**Wave 1** (no dependencies). Read `docs/tasks/README.md` first.

## What this is
Flying *with* drugs risks an airport bust unless covered by No-Scent (1 can per
100 units, max 10 cans → at most 1000 units fully covered). Shipping avoids the
airport check. Ordinary police encounters do NOT depend on inventory size.

## Exact rules
```
cans_needed(units)        = ceil(units / 100)
covered                   = min(units, 100 * cans)
detection_prob(units,cans)= 0.99 * (0.01 + 0.99 * (units - covered) / units)   # units>0 else 0
law_encounter_prob(day)   ≈ 0.1042 for day>1 else 0.0    # constants.P_LAW_ENCOUNTER_PER_DAY
```
Attacker count scales with rank tier. **Combat resolution — what you lose on a
loss, whether you can die or flee — is UNKNOWN.** Do not invent a cost: in
`channel_recommendation`, take a `bust_cost_hook` (callable or None); when None,
report the detection probability only and flag the loss as unknown.

`channel_recommendation(units, unit_value, cans_available, origin, dest, shipper,
days, bust_cost_hook=None)`: compare
- **carry**: detection risk = `detection_prob(units, min(cans_available, cans_needed(units)))`;
  expected loss = `bust_cost_hook(...)` if given else `None`.
- **ship**: use `shipping.expected_ship_value(...)`. **Import `shipping`** — that's
  fine (it's wave 1, dependency-free); until it merges its funcs raise
  NotImplementedError, so guard the ship branch behind a try/except in tests or
  monkeypatch it. Return a dict describing both channels for the planner.

## Tests (`tests/test_risk.py`) — must include
```python
from dl2model import risk
import pytest, math

def test_cans_needed():
    assert risk.cans_needed(1) == 1
    assert risk.cans_needed(100) == 1
    assert risk.cans_needed(101) == 2

def test_detection_table():
    assert risk.detection_prob(500, 5)    == pytest.approx(0.0099, abs=1e-4)
    assert risk.detection_prob(500, 3)    == pytest.approx(0.402, abs=1e-3)
    assert risk.detection_prob(1000, 10)  == pytest.approx(0.0099, abs=1e-4)
    assert risk.detection_prob(3500, 10)  == pytest.approx(0.710, abs=1e-3)
    assert risk.detection_prob(20000, 10) == pytest.approx(0.941, abs=1e-3)
    assert risk.detection_prob(0, 0) == 0.0

def test_law_encounter():
    assert risk.law_encounter_prob(1) == 0.0
    assert risk.law_encounter_prob(5) == pytest.approx(0.1042, abs=1e-3)
```
Add a `channel_recommendation` test with `bust_cost_hook=None` asserting it
returns the carry detection prob and marks bust loss unknown (monkeypatch
`risk.shipping.expected_ship_value` to a stub so the ship branch is exercised).

## Done when
`python3 -m pytest tests/test_risk.py -q` green, then commit + push + PR.
