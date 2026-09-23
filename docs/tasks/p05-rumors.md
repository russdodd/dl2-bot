# p05 — rumors.py (forward-signal rumor model)

**Files you own:** `dl2model/rumors.py`, `tests/test_rumors.py`
**Wave 1** (imports `prices` for event-price means, but tests can monkeypatch —
see below). Read `docs/tasks/README.md` first.

## What this is
A rumor is shown BEFORE you choose where to fly and names a drug + city +
direction for tomorrow. It's the strongest route signal in the game. When a
rumor is active, condition on it instead of the ordinary decay model.

## Exact rules
Each day, independently: 1/3 chance of a rumor about the current city, 1/3 about
a random other city. A rumor schedules tomorrow's event:
```
70% the predicted event | 20% nothing (ordinary 2% roll still applies) | 10% opposite
→ realized:  true 70.2% | opposite 10.2% | none 19.6%
```
(constants: `RUMOR_P_TRUE/OPPOSITE/NONE`.) "scarce" ⇒ shortage (spike up),
"abundant" ⇒ glut (crash down).

## Implement
- `parse_rumor(text)` → `Rumor(drug, city, direction)` or None. Recognize drugs
  from `constants.DRUGS`, cities from `constants.CITIES`, direction from the
  words "scarce"/"abundant". Be tolerant of surrounding text.
- `outcome_probs()` → `{"true":0.702,"opposite":0.102,"none":0.196}`.
- `expected_next_price_given_rumor(price, target, M, rumor)`:
  `P_true·E[predicted] + P_opp·E[opposite] + P_none·expected_next_price(price,target)`.
  For E[shortage price] and E[glut price] take the expectation of
  `prices.event_shortage_price`/`event_glut_price` over their rand terms:
  `E[shortage] = (M + expected_rand_mod(M//2)) * E[5+rand%5]`, with
  `E[5+rand%5]=7.0` (uniform 5..9) — likewise `E[glut] = (M -
  expected_rand_mod(M//2)) / E[5+rand%5]`. Use `prices.expected_rand_mod`.
  Import `prices`; tests monkeypatch it so you don't depend on it being
  implemented yet.

## Tests (`tests/test_rumors.py`) — must include
```python
from dl2model import rumors
import pytest

def test_parse():
    r = rumors.parse_rumor("You hear a rumor that Cocaine will be scarce in Boston tomorrow.")
    assert (r.drug, r.city, r.direction) == ("Cocaine", "Boston", "scarce")
    assert rumors.parse_rumor("nothing here") is None

def test_probs_sum():
    p = rumors.outcome_probs()
    assert p["true"]+p["opposite"]+p["none"] == pytest.approx(1.0, abs=1e-3)

def test_expected_given_rumor(monkeypatch):
    monkeypatch.setattr(rumors.prices, "expected_rand_mod", lambda g: g/2)
    monkeypatch.setattr(rumors.prices, "expected_next_price", lambda p,t: p)
    r = rumors.Rumor("Cocaine","Boston","scarce")
    val = rumors.expected_next_price_given_rumor(5100, 5100, 5100, r)
    assert val > 5100      # a scarcity rumor pulls expectation well above M
```

## Done when
`python3 -m pytest tests/test_rumors.py -q` green, then commit + push + PR.
