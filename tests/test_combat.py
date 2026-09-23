"""Tests for dl2model.combat — decoded combat/bank/mugging/hospital primitives."""
from dl2model import combat
from dl2model import risk
import pytest


# --- required numeric checks (from docs/tasks/combat.md) --------------------

def test_flee_prob():
    assert combat.flee_prob(1) == pytest.approx(0.5)
    assert combat.flee_prob(2) == pytest.approx(1 / 3)
    assert combat.flee_prob(9) == pytest.approx(0.1)
    assert combat.flee_prob(20) == pytest.approx(0.1)   # capped


def test_hospital():
    assert combat.full_heal_cost(50) == 125000
    assert combat.full_heal_cost(90) == 1000
    assert combat.hospital_cost(70, 90) == 30 ** 3 - 10 ** 3   # 27000-1000


def test_mugging_capped():
    assert combat.expected_mugging_loss(50) == 50          # capped by cash
    assert combat.expected_mugging_loss(10 ** 9) > 0


def test_airport_bust_is_inventory_value():
    # ~loses the whole carried load
    assert combat.airport_bust_loss(200, 5000) == pytest.approx(200 * 5000, rel=0.01)


def test_daily_carry_risk_uses_encounter_prob():
    c = combat.daily_carry_risk_cost(1_000_000, day=5)
    assert 0 < c < 1_000_000


def test_recommended_deposit():
    assert combat.recommended_deposit(1000, planned_trade_cash=300) == 700
    assert combat.recommended_deposit(300, planned_trade_cash=300) == 0


# --- additional coverage ----------------------------------------------------

def test_flee_prob_boundaries():
    # k = min(attackers+1, 10); cap kicks in at 9 attackers and holds after.
    assert combat.flee_prob(0) == pytest.approx(1.0)     # k=1, always escape
    assert combat.flee_prob(3) == pytest.approx(0.25)
    assert combat.flee_prob(8) == pytest.approx(1 / 9)
    assert combat.flee_prob(9) == pytest.approx(0.1)
    assert combat.flee_prob(1000) == pytest.approx(0.1)


def test_hospital_cost_is_full_heal_when_target_100():
    for h in (0, 25, 50, 73, 99):
        assert combat.hospital_cost(h, 100) == combat.full_heal_cost(h)
    assert combat.full_heal_cost(100) == 0
    assert combat.full_heal_cost(0) == 100 ** 3          # 1,000,000
    # Partial heal is a difference of the two cube costs.
    assert combat.hospital_cost(40, 60) == 60 ** 3 - 40 ** 3


def test_expected_mugging_loss_blended_mean_when_rich():
    # 0.1*3499.5 + 0.9*349.5 = 664.5 once cash exceeds every bracket mean.
    assert combat.expected_mugging_loss(1_000_000) == pytest.approx(664.5)
    assert combat.expected_mugging_loss(0) == 0


def test_expected_mugging_loss_partial_cap():
    # cash between the small and big bracket means: small bracket uncapped,
    # big bracket capped at cash.
    cash = 400
    expected = 0.10 * min(3499.5, cash) + 0.90 * min(349.5, cash)
    assert combat.expected_mugging_loss(cash) == pytest.approx(expected)


def test_airport_bust_loss_surrender_ignores_cash():
    # Surrender (default & recommended): drugs lost, cash untouched. Cash arg
    # has no effect on the surrender branch.
    for cash in (0, 10, 1_000_000):
        assert combat.airport_bust_loss(200, 5000, cash=cash) == pytest.approx(200 * 5000)


def test_airport_bust_loss_run_caps_cash_at_50():
    # A successful Run clears drugs AND caps pocket cash at $50.
    assert combat.airport_bust_loss(200, 5000, cash=0, policy="run") == 200 * 5000
    assert combat.airport_bust_loss(200, 5000, cash=50, policy="run") == 200 * 5000
    assert combat.airport_bust_loss(200, 5000, cash=1_000_000, policy="run") == (
        200 * 5000 + (1_000_000 - 50))


def test_airport_bust_loss_rejects_unknown_policy():
    with pytest.raises(ValueError):
        combat.airport_bust_loss(200, 5000, policy="bribe")


def test_decoded_action_probabilities_are_exact():
    # Exact RNG thresholds decoded from druglord2.exe (32,768-output rand()).
    assert combat.SURRENDER_ACCEPT_LAW == 6005 / 8192
    assert combat.SURRENDER_ACCEPT_LAW == pytest.approx(0.7330322265625)
    assert combat.AIRPORT_RUN_ESCAPE_PROB == 3277 / 32768
    assert combat.AIRPORT_RUN_ESCAPE_PROB == pytest.approx(0.100006103515625)
    assert combat.BRIBE_ACCEPT_AIRPORT == 650 / 32768
    assert combat.AIRPORT_RUN_CASH_CAP == 50


def test_daily_carry_risk_matches_formula():
    inv = 500_000
    day = 7
    expected = risk.law_encounter_prob(day) * combat.SURRENDER_ACCEPT_LAW * inv
    assert combat.daily_carry_risk_cost(inv, day) == pytest.approx(expected)
    # Day 1 (and earlier) has no law encounters -> no carry risk.
    assert combat.daily_carry_risk_cost(inv, day=1) == 0.0
    # seizure_prob is overridable.
    assert combat.daily_carry_risk_cost(inv, day, seizure_prob=1.0) == pytest.approx(
        risk.law_encounter_prob(day) * inv)


def test_recommended_deposit_with_buffers():
    # target = trade + near_term + combat_buffer.
    assert combat.recommended_deposit(
        10_000, planned_trade_cash=3_000, near_term_expenses=1_000,
        combat_buffer=500) == 10_000 - 4_500
    # Never negative: don't recommend a withdraw.
    assert combat.recommended_deposit(
        100, planned_trade_cash=3_000) == 0


def test_bust_cost_hook_matches_channel_recommendation_call():
    hook = combat.bust_cost_hook_for_risk()
    # risk.channel_recommendation calls the hook with all-keyword args.
    got = hook(units=200, unit_value=5000, cans=3, origin="A", dest="B")
    assert got == pytest.approx(combat.airport_bust_loss(200, 5000))
    # cans/origin/dest are ignored (coverage affects detection prob, not loss).
    assert hook(units=200, unit_value=5000, cans=10, origin="X", dest="Y") == got


def test_bust_cost_hook_plugs_into_risk():
    # End-to-end: the hook actually populates bust_loss in the risk report.
    out = risk.channel_recommendation(
        units=500, unit_value=200, cans_available=3,
        origin="Miami", dest="New York",
        shipper="Courier Stan", days=2,
        bust_cost_hook=combat.bust_cost_hook_for_risk())
    carry = out["carry"]
    assert carry["bust_loss_known"] is True
    assert carry["bust_loss"] == pytest.approx(500 * 200)
