"""Combat / bank / mugging / hospital primitives (decoded from the DL2 2.2 executable).

This is the piece that was UNKNOWN when ``risk.py`` was written: its
``channel_recommendation`` takes ``bust_cost_hook=None`` because the loss
magnitude of an airport bust was not yet decoded. This module supplies that hook
as a real function, plus the banking policy the planner needs.

All functions are pure (data in, number out). This module is intentionally
self-contained: it keeps its own constants so ``constants.py`` stays frozen. The
only intra-package dependency is ``dl2model.risk`` for the law-encounter prob.

OUT OF SCOPE: the optimal fight/run/surrender/bribe DP policy. Primitives only.

Decoded rules (see docs/tasks/combat.md):
- Bank is risk-free: never reduced by theft, mugging, police, surrender, or
  airport security -- only by explicit withdraw. Rank uses cash+bank, so banking
  does not slow promotion.
- Law-enforcement surrender (police/ATF/SWAT/airport security) accepted with
  prob ~= 0.7330: carried_drugs=0, cash & bank unchanged.
- Ordinary-criminal surrender (accepted): carried_drugs=0, cash=0, bank unchanged.
- Flee: k = min(attackers+1, 10); escape iff rand()%k == 0, i.e. prob 1/k.
  Airport-security flee-escape special case: drugs=0, cash=min(cash, 50).
- Mugging (cash only, capped by cash): 10% -> 1000 + rand%5000 ($1,000-5,999);
  90% -> 100 + rand%500 ($100-599); loss = min(loss, cash).
- Hospital: heal H->H2 costs (100-H)**3 - (100-H2)**3; full heal (100-H)**3.
  Paid from CASH on hand (not bank).
"""

from . import risk


# --- local constants (kept here so constants.py stays frozen) --------------
#
# Airport detection does NOT auto-resolve to a fixed bust outcome. Detection
# opens the normal combat dialog against the special "airport security" opponent
# (index 9), and the PLAYER picks Surrender / Run / Bribe / Fight. The constants
# below are the exact decoded RNG thresholds for those actions (from the
# druglord2.exe disassembly). All are expressed as exact rationals over the
# 32,768-output MSVC rand() space, so they carry the real modulo bias rather than
# the idealized round number.

#: P(law-enforcement surrender accepted): rand()%101 draws value 1..74 -> 24020
#: of 32768 outputs. On accept: carried drugs cleared, cash & bank UNCHANGED.
#: = 6005/8192 ≈ 0.7330322265625 (the task's "≈0.7330").
SURRENDER_ACCEPT_LAW = 6005 / 8192

#: P(Run escapes on the FIRST airport attempt): rand()%k == 0 with k = min(
#: attackers+1, 10). Airport security always starts with >=9 attackers at every
#: rank, so k == 10 initially -> residue 0 hits 3277 of 32768 outputs.
#: = 3277/32768 ≈ 0.100006103515625 (biased, not exactly 0.10). On escape:
#: carried drugs cleared AND cash capped at $50; bank unchanged.
AIRPORT_RUN_ESCAPE_PROB = 3277 / 32768

#: P(airport-security bribe accepted): rand()%101 <= 1 -> 650 of 32768 outputs.
#: On accept: drugs KEPT, cash reduced by the bribe amount, bank unchanged.
#: = 325/16384 ≈ 0.01983642578125.
BRIBE_ACCEPT_AIRPORT = 650 / 32768

#: Pocket cash is capped at this many $ ONLY on a successful airport Run escape.
#: Surrender does not touch cash.
AIRPORT_RUN_CASH_CAP = 50

#: Mugging: 10% of muggings draw the "big" bracket, 90% the "small" bracket.
_MUG_BIG_PROB = 0.10
_MUG_SMALL_PROB = 0.90
#: loss = 1000 + rand()%5000  -> uniform over integers 1000..5999, mean 3499.5
_MUG_BIG_MEAN = 1000 + (5000 - 1) / 2.0     # 3499.5
#: loss = 100 + rand()%500    -> uniform over integers 100..599,  mean 349.5
_MUG_SMALL_MEAN = 100 + (500 - 1) / 2.0     # 349.5


def flee_prob(attackers: int) -> float:
    """Probability of escaping a flee attempt: 1 / min(attackers+1, 10).

    1 attacker -> 0.5, 2 -> 1/3, ..., 9+ -> 0.1 (capped)."""
    k = min(attackers + 1, 10)
    return 1.0 / k


def hospital_cost(h_from: int, h_to: int) -> int:
    """Cash cost to heal from health ``h_from`` to ``h_to`` (h_to > h_from):
    (100 - h_from)**3 - (100 - h_to)**3."""
    return (100 - h_from) ** 3 - (100 - h_to) ** 3


def full_heal_cost(h: int) -> int:
    """Cash cost to fully heal from health ``h`` to 100: (100 - h)**3."""
    return (100 - h) ** 3


def expected_mugging_loss(cash: int) -> float:
    """Expected cash lost in one mugging, capped by cash on hand.

    0.10 * E[1000..5999] + 0.90 * E[100..599] = 0.10*3499.5 + 0.90*349.5 = 664.5,
    then min(loss, cash) applied per-bracket (a real mugging caps the *drawn*
    loss at cash, so we cap each bracket mean, not just the blended mean)."""
    big = min(_MUG_BIG_MEAN, cash)
    small = min(_MUG_SMALL_MEAN, cash)
    return _MUG_BIG_PROB * big + _MUG_SMALL_PROB * small


def airport_bust_loss(units, unit_value, cash=0, policy="surrender") -> float:
    """Capital loss when carried drugs are DETECTED at the airport, under a
    chosen response ``policy``.

    Detection does NOT auto-resolve: it opens combat vs. airport security and the
    player picks an action. The clean capital-loss terminal for each sensible
    policy (decoded from the executable) is:

      * ``"surrender"`` (default, recommended): the accepted-surrender branch
        clears all carried drugs and leaves cash & bank untouched, so the
        capital loss is the full inventory value ``units * unit_value``. Surrender
        is accepted with prob :data:`SURRENDER_ACCEPT_LAW` per attempt; on
        rejection you take an attack and can re-surrender, so across the
        encounter you lose the drugs with near-certainty while keeping all cash.
        Cash is irrelevant to this branch -- the ``cash`` arg is unused here.

      * ``"run"``: a *successful* Run also clears the drugs but additionally caps
        pocket cash at :data:`AIRPORT_RUN_CASH_CAP` ($50), so its terminal loss
        is ``units*unit_value + max(cash - 50, 0)``. (A single Run only escapes
        with prob :data:`AIRPORT_RUN_ESCAPE_PROB` ≈ 0.10; this returns the loss
        GIVEN the escape happens, not weighted by that probability.)

    NOT modeled here (out of scope -- see docs/tasks/combat.md): the mortality /
    health cost of the rejected-surrender / failed-run branches, which feed back
    into combat and can end in death = game over. The optimizer must weigh that
    separately; this function returns only the clean capital loss of the
    terminal drug/cash write.

    Suitable as the ``bust_cost_hook`` for ``risk.channel_recommendation`` via
    :func:`bust_cost_hook_for_risk` (which uses the default surrender policy).
    With the default surrender policy the result is exactly the inventory
    value, independent of ``cash``."""
    inventory_loss = units * unit_value
    if policy == "surrender":
        return inventory_loss
    if policy == "run":
        return inventory_loss + max(0, cash - AIRPORT_RUN_CASH_CAP)
    raise ValueError(f"unknown airport policy: {policy!r} (use 'surrender' or 'run')")


def daily_carry_risk_cost(inventory_value: float, day: int,
                          seizure_prob: float = SURRENDER_ACCEPT_LAW) -> float:
    """Expected inventory loss from carrying drugs through one ordinary day:

    ``risk.law_encounter_prob(day) * seizure_prob * inventory_value``.

    ``seizure_prob`` defaults to the law-surrender-accept rate (the fraction of
    encounters that end with carried drugs dropped)."""
    return risk.law_encounter_prob(day) * seizure_prob * inventory_value


def recommended_deposit(cash, planned_trade_cash, near_term_expenses=0,
                        combat_buffer=0) -> int:
    """How much cash to deposit in the (risk-free) bank right now.

    Bank dominance: keep only what you need soon, bank the rest.
    target = planned_trade_cash + near_term_expenses + combat_buffer;
    deposit = max(0, cash - target)."""
    target = planned_trade_cash + near_term_expenses + combat_buffer
    return max(0, cash - target)


def bust_cost_hook_for_risk():
    """Return a callable matching the signature ``risk.channel_recommendation``
    invokes its ``bust_cost_hook`` with (``units, unit_value, cans, origin,
    dest`` -- all keyword) and wrapping :func:`airport_bust_loss`.

    Usage:
        risk.channel_recommendation(..., bust_cost_hook=combat.bust_cost_hook_for_risk())

    ``cans``, ``origin`` and ``dest`` are accepted and ignored: an airport bust
    strips the carried load regardless of No-Scent coverage (coverage lowers the
    *probability* of detection, handled by risk.detection_prob, not the loss
    magnitude once detected)."""
    def hook(units, unit_value, cans=None, origin=None, dest=None):
        return airport_bust_loss(units, unit_value)
    return hook
