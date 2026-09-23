# combat — combat/bank/mugging risk primitives (dl2model/combat.py)

**Files you own:** `dl2model/combat.py` (NEW), `tests/test_combat.py`
Isolated module — does NOT edit any existing `dl2model/*` file (keep your own
constants inside `combat.py` so `constants.py` stays frozen). Read
`docs/tasks/README.md` first. Wave-1 modules are all merged and importable.

## What this is
The decompiled combat/bank/mugging/hospital mechanics — the piece that was
UNKNOWN when `risk.py` was written (its `channel_recommendation` takes a
`bust_cost_hook=None` because the loss magnitude wasn't known yet). This module
provides that hook as a real function plus the banking policy the planner needs.

## Decoded rules (from the executable)
**Bank is risk-free.** No interest, but the bank balance is NEVER reduced by
theft, mugging, police, surrender, or airport security — only by explicit
withdraw. Rank uses `cash + bank`, so banking doesn't slow promotion.

**Law-enforcement surrender** (Drug Force / police / ATF / SWAT / airport
security), accepted with prob ≈ **0.7330**: `carried_drugs = 0`, cash & bank
unchanged. Rejected → they attack, combat continues.

**Ordinary-criminal surrender** (accepted): `carried_drugs = 0`, `cash = 0`,
bank unchanged ("take everything! Hope you have money in the bank!").

**Flee:** `k = min(attackers + 1, 10)`; escape if `rand() % k == 0` (so prob
`1/k`: 1 att→50%, 2→33.3%, …, 9+→10%). Failed flee → one attack, fight continues.
**Airport-security flee** special case: escape → `drugs = 0`, `cash = min(cash,50)`,
bank unchanged (worse than surrendering to airport security, which keeps cash).

**Mugging** (cash only, capped by cash): 10% of muggings `loss = 1000 + rand%5000`
($1,000–5,999); 90% `loss = 100 + rand%500` ($100–599); `loss = min(loss, cash)`.

**Hospital:** heal H→H2 costs `(100-H)**3 - (100-H2)**3`; full heal `(100-H)**3`.
Paid from CASH on hand (not bank). Death = game over (no respawn); final score is
still `cash + bank - debt`, inventory worthless.

**Encounter rates** (already in constants.py): `P_LAW_ENCOUNTER_PER_DAY ≈ 0.1042`
after day 1.

## Implement (all pure functions; keep constants local to this file)
```python
SURRENDER_ACCEPT_LAW = 0.7330

def flee_prob(attackers: int) -> float            # 1 / min(attackers+1, 10)
def hospital_cost(h_from: int, h_to: int) -> int  # (100-h_from)**3 - (100-h_to)**3
def full_heal_cost(h: int) -> int                 # (100-h)**3
def expected_mugging_loss(cash: int) -> float     # 0.1*E[1000..5999] + 0.9*E[100..599], capped at cash

def airport_bust_loss(units, unit_value, cash=0) -> float:
    """Expected loss when carried drugs are DETECTED at the airport. In ~every
    resolution the drugs are lost (surrender-accept -> drugs gone; flee -> drugs
    gone + cash capped $50), so ~= units*unit_value plus a small pocket-cash tail
    on the flee branch. Document the approximation. Suitable as risk.channel_
    recommendation(bust_cost_hook=...)."""

def daily_carry_risk_cost(inventory_value: float, day: int,
                          seizure_prob: float = SURRENDER_ACCEPT_LAW) -> float:
    """Expected inventory loss from carrying through one ordinary day:
    risk.law_encounter_prob(day) * seizure_prob * inventory_value. (Import
    dl2model.risk for the encounter prob.)"""

def recommended_deposit(cash, planned_trade_cash, near_term_expenses=0,
                        combat_buffer=0) -> int:
    """Bank dominance: keep only what you need soon, bank the rest.
    target = planned_trade_cash + near_term_expenses + combat_buffer;
    return max(0, cash - target)."""
```
Provide `bust_cost_hook_for_risk()` returning a callable with the signature
`risk.channel_recommendation` expects (`units, unit_value, cans, origin, dest`)
that wraps `airport_bust_loss`, so a caller can do
`risk.channel_recommendation(..., bust_cost_hook=combat.bust_cost_hook_for_risk())`.

## OUT OF SCOPE (do not build)
The optimal fight/run/surrender/bribe DP policy (attacker type/count, health,
weapons). Provide the primitives above only.

## Tests (`tests/test_combat.py`) — must include
```python
from dl2model import combat
import pytest

def test_flee_prob():
    assert combat.flee_prob(1) == pytest.approx(0.5)
    assert combat.flee_prob(2) == pytest.approx(1/3)
    assert combat.flee_prob(9) == pytest.approx(0.1)
    assert combat.flee_prob(20) == pytest.approx(0.1)   # capped

def test_hospital():
    assert combat.full_heal_cost(50) == 125000
    assert combat.full_heal_cost(90) == 1000
    assert combat.hospital_cost(70, 90) == 30**3 - 10**3   # 27000-1000

def test_mugging_capped():
    assert combat.expected_mugging_loss(50) == 50          # capped by cash
    assert combat.expected_mugging_loss(10**9) > 0

def test_airport_bust_is_inventory_value():
    # ~loses the whole carried load
    assert combat.airport_bust_loss(200, 5000) == pytest.approx(200*5000, rel=0.01)

def test_daily_carry_risk_uses_encounter_prob():
    c = combat.daily_carry_risk_cost(1_000_000, day=5)
    assert 0 < c < 1_000_000

def test_recommended_deposit():
    assert combat.recommended_deposit(1000, planned_trade_cash=300) == 700
    assert combat.recommended_deposit(300, planned_trade_cash=300) == 0
```

## Workflow
Work in the worktree at `~/code/dl2-wt/combat` (branch `task/combat`). Run
`python3 -m pytest tests/ -q` from the worktree root until the whole suite is
green. Commit and push `task/combat`. **Do NOT open a PR and do NOT merge to
main** — stop and tell me it's pushed and ready to verify, with a short summary
and the test results.
