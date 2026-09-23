# p01 — prices.py (price/target momentum + spike decay)

**Files you own:** `dl2model/prices.py`, `tests/test_prices.py`
**Wave 1** (no dependencies — code against `constants.py`). Read
`docs/tasks/README.md` first.

## What this is
Each (city, drug) market has a hidden target `T`. Each ordinary day the price
drifts toward `T`, then may pick a new target. Because MSVC `rand()` maxes at
32767, big spikes decay by at most **$32,767/day** (expected **$16,383.50**) —
linear in dollars, not a fixed percentage. This replaces the old fitted decay
curve in the live bot.

## Exact rules (from the executable)
```
M    = base_price[drug] * city_mult[city] // 100     # constants.normal_mean
r    = rand() % M
init price  = M//2 + r ;  init target = price          # (r reused by stock)
new target  = M//2 + rand()%M                           # spans ~0.5M..1.5M

# ordinary daily update:
old_gap = target - price
price  += signed_rand(old_gap)
if abs(old_gap) < price // 10:        # NB: OLD gap, NEW price
    target = M//2 + rand()%M

signed_rand(gap) = rand()%gap if gap>0 ; -(rand()%|gap|) if gap<0 ; 0 if 0

# special-event prices (used by simulator/rumors):
shortage price = (M + rand()%(M//2)) * (5 + rand()%5)
glut     price = (M - rand()%(M//2)) // (5 + rand()%5)
```
`rand()` here is `MsvcRand.rand()`/`.rand_mod(n)` from `dl2model.prng` — but for
this module take an `rng` argument and call `rng.rand_mod(n)`; **do not import
prng** (keeps you dependency-free; tests pass a tiny fake rng).

## Expectation (the important part)
`rand()` is uniform on `0..32767`, so `rand()%g` has modulo bias. Exact mean:
```
expected_rand_mod(g):  # g>0
    q, r = divmod(32768, g)
    return (q*g*(g-1)/2 + r*(r-1)/2) / 32768
```
For `g >= 32768` this is exactly `16383.5`.

`expected_next_price(price, target)`: `gap=target-price`; add
`expected_rand_mod(gap)` if gap>0 else subtract `expected_rand_mod(-gap)`.

`expected_price_after_days(price, target, n)`: iterate `expected_next_price`
`n` times holding `target` fixed (document this as the modeling assumption — the
target only reselects once the gap is within ~10%, which for a fresh big spike
takes several days).

## Implement
All functions in the stub. For `signed_rand`, `step_price`,
`initial_price_target`, `new_target`, `event_*_price`, take an `rng` with a
`.rand_mod(n)` method.

## Tests (`tests/test_prices.py`) — must include
```python
from dl2model import prices
import pytest

def test_expected_rand_mod_table():
    assert prices.expected_rand_mod(5000)  == pytest.approx(2405, abs=1)
    assert prices.expected_rand_mod(10000) == pytest.approx(4694, abs=1)
    assert prices.expected_rand_mod(20000) == pytest.approx(8591, abs=1)
    assert prices.expected_rand_mod(30000) == pytest.approx(13849, abs=1)
    assert prices.expected_rand_mod(32768) == pytest.approx(16383.5)
    assert prices.expected_rand_mod(99999) == pytest.approx(16383.5)
    assert prices.expected_rand_mod(0) == 0.0

def test_spike_one_day():
    # $60k Cocaine spike toward M≈5100: expect ~$43,616.5, floor $27,233
    assert prices.expected_next_price(60000, 5100) == pytest.approx(43616.5, abs=1)

class FakeRng:            # deterministic rand_mod for step tests
    def __init__(self, seq): self.seq=list(seq); self.i=0
    def rand_mod(self, n):
        v=self.seq[self.i]; self.i+=1; return v % n if n>0 else 0
```
Add tests for: `signed_rand` sign handling; `step_price` reselect firing on
`abs(old_gap) < new_price//10` and NOT otherwise (use FakeRng to control draws,
assert the target changed / didn't); `initial_price_target` returns
`(M//2+r, M//2+r, r)`.

## Done when
`python3 -m pytest tests/test_prices.py -q` is green, then commit + push +
open PR per README.
