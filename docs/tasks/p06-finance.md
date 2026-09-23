# p06 — finance.py (loans, rank, overhead, horizon, score)

**Files you own:** `dl2model/finance.py`, `tests/test_finance.py`
**Wave 1** (no dependencies). Read `docs/tasks/README.md` first.

## Exact rules
```
final_score          = cash + bank - debt        # UNSOLD INVENTORY = worthless
debt_after_days      : each day  debt += debt * rate_pct // 100   (C int divide)
early_payment_total  = debt + debt * rate_pct // 200
max_loan(cash,shark) = cash * LOAN_SHARKS[shark][0]
rank_for_wealth(w)   = highest rank with RANK_THRESHOLD[rank] <= w   (w = cash+bank, NOT net of debt)
max_days(promos)     = START_DAYS + PROMO_BONUS_DAYS * min(promos, MAX_PROMOTIONS)  # 30..55
```
Rank thresholds/capacities/overhead and the loan-shark table are all in
`constants.py`. Promotion also needs the tier sustained
`RANK_PROMOTE_SUSTAIN_DAYS` (=3) days — you don't have to model the sustain here
(that's the simulator's job), just expose `rank_for_wealth`, `capacity_for_rank`,
`daily_overhead`, `max_days`.

## Tests (`tests/test_finance.py`) — must include
```python
from dl2model import finance
import pytest

def test_score():
    assert finance.final_score(50000, 10000, 2000) == 58000

def test_debt_compounds():
    assert finance.debt_after_days(1000, 15, 1) == 1150     # +15%
    assert finance.debt_after_days(1000, 15, 0) == 1000
    # Odd Lenny 60%/day for 2 days: 1000 -> 1600 -> 2560
    assert finance.debt_after_days(1000, 60, 2) == 2560

def test_early_payment():
    assert finance.early_payment_total(1000, 15) == 1075    # +half a day

def test_max_loan():
    assert finance.max_loan(2000, "Odd Lenny") == 10000     # 5x
    assert finance.max_loan(2000, "Buddles")   == 2000      # 1x

def test_rank_for_wealth():
    assert finance.rank_for_wealth(4999)     == "Wannabe"
    assert finance.rank_for_wealth(5000)     == "Small-time operator"
    assert finance.rank_for_wealth(40000)    == "Dealer"
    assert finance.rank_for_wealth(15000000) == "Drug Lord"

def test_capacity_overhead_days():
    assert finance.capacity_for_rank("Dealer") == 100
    assert finance.daily_overhead("Drug Lord") == 10000
    assert finance.max_days(0) == 30
    assert finance.max_days(5) == 55
    assert finance.max_days(2) == 40
```

## Done when
`python3 -m pytest tests/test_finance.py -q` green, then commit + push + PR.
