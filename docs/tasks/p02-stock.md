# p02 — stock.py (availability / stock + listing roll)

**Files you own:** `dl2model/stock.py`, `tests/test_stock.py`
**Wave 1** (no dependencies). Read `docs/tasks/README.md` first.

## What this is
How many units a market offers, and whether a drug is even listed. Stock is
anti-correlated with price and scaled by the player's capacity tier `C`. A
massive spike deliberately comes with tiny stock.

## Exact rules
```
M = normal_mean(drug, city)           # from constants
C = RANK_CAPACITY[rank]

# game init:
init quantity = C - trunc_div(r * C, M)         # r = rand()%M from prices; clamp >=0

# ordinary daily update, AFTER the price moves:
stock_target = C - trunc_div((price - M//2) * C, M)
quantity     = max(trunc_div(quantity + stock_target, 2), 0)

# events modify the just-computed quantity:
shortage: quantity //= (2 + rand()%5)      # divide by 2..6
glut:     quantity *= (2 + rand()%5)       # multiply by 2..6

# listing (separate from stock; a listed drug can have 0 stock = sell-only):
listed = True if event else (rand()%3 != 0)   # 2/3 listed absent an event
```
Use `constants.trunc_div` (C truncates toward zero; `price` can exceed `1.5M`
making the numerator negative → target must clamp to `>=0`). Take an `rng` arg
with `.rand_mod(n)` for `event_*` and `is_listed`; don't import prng.

## Stock-target reference table (price as multiple of M, cap C)
| price | target |
|------|--------|
| 0.5M | C |
| 0.75M | 0.75C |
| 1.0M | 0.5C |
| 1.25M | 0.25C |
| 1.5M | 0 |
| >1.5M | 0 (after clamp) |

## Tests (`tests/test_stock.py`) — must include
```python
from dl2model import stock
from dl2model.constants import trunc_div

def test_stock_target_table():
    M, C = 5000, 100
    assert stock.stock_target(M//2, M, C)      == 100
    assert stock.stock_target(M, M, C)         == 50
    assert stock.stock_target(3*M//2, M, C)    == 0
    assert stock.stock_target(2*M, M, C)       <= 0   # (planner clamps)

def test_step_quantity_clamps_nonneg():
    assert stock.step_quantity(0, 3*5000, 5000, 100) >= 0

def test_trunc_div_negatives():
    assert trunc_div(-7, 2) == -3      # not -4
```
Add: `initial_quantity(r,C,M)` matches `C - trunc_div(r*C,M)` clamped; `is_listed`
returns True when `event=True`, and for a FakeRng returns the `rand%3!=0` pattern;
`event_shortage_quantity`/`event_glut_quantity` divide/multiply by 2..6.

## Done when
`python3 -m pytest tests/test_stock.py -q` green, then commit + push + PR.
