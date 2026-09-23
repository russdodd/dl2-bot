"""Tests for the multi-day plan search.

The pure planning helpers (`decayed_unit_value`, `evaluate_loan_to_promote`) and
the end-to-end `plan()` are testable without a real game — wave-1 is merged, so
the EV primitives are real. Only a rollout that instantiates the p08
`simulator.Game` is deferred (marked xfail until p08 merges).
"""
import pytest

from dl2model import strategy, prices, rumors, finance
from dl2model import constants as C


# ---------------------------------------------------------------------------
# Remote spike is valued with multi-day decay, not the raw current price.
# ---------------------------------------------------------------------------
def test_remote_spike_valued_with_multiday_decay():
    drug, city = "Cocaine", "Beijing"
    M = C.normal_mean(drug, city)          # normal level the price reverts toward
    spike = 50000                          # a big remote spike, well above M
    assert spike > M

    v3 = strategy.decayed_unit_value(spike, city, drug, arrival_days=3)

    # It uses the exact decay formula over the arrival delay, not the raw price.
    assert v3 == pytest.approx(prices.expected_price_after_days(spike, M, 3))
    assert v3 < spike                      # decayed below the raw spike
    assert v3 > M                          # but still elevated after 3 days

    # Longer to arrive -> more decay -> worth less; arriving now -> raw price.
    v6 = strategy.decayed_unit_value(spike, city, drug, arrival_days=6)
    assert v6 < v3
    assert strategy.decayed_unit_value(spike, city, drug, arrival_days=0) == float(spike)


def test_active_rumor_overrides_decay_valuation():
    drug, city = "Heroin", "Moscow"
    M = C.normal_mean(drug, city)
    price = M                               # at normal level, so plain decay ~ flat
    rumor = rumors.Rumor(drug=drug, city=city, direction="scarce")

    v = strategy.decayed_unit_value(price, city, drug, arrival_days=1, rumor=rumor)
    expected = rumors.expected_next_price_given_rumor(price, M, M, rumor)
    assert v == pytest.approx(expected)
    # A "scarce" rumor predicts a shortage (spike up), so it beats the flat decay.
    assert v > strategy.decayed_unit_value(price, city, drug, arrival_days=1)


def test_unknown_market_falls_back_to_raw_price():
    # Synthetic city/drug not in the tables: can't model decay, return raw.
    assert strategy.decayed_unit_value(999, "Atlantis", "Cocaine", 3) == 999.0
    assert strategy.decayed_unit_value(999, "Beijing", "Unobtanium", 3) == 999.0


# ---------------------------------------------------------------------------
# Loan-to-promote: take it when the extra days pay for the interest; not when
# the interest exceeds the gain.
# ---------------------------------------------------------------------------
def test_loan_to_promote_taken_when_days_pay_for_interest():
    # cash 4000 -> next threshold is Small-time operator (5000): need $1000.
    state = {"cash": 4000, "bank": 0}
    res = strategy.evaluate_loan_to_promote(state, value_per_extra_day=5000)

    assert res["take"] is True
    assert res["target_rank"] == "Small-time operator"
    assert res["loan"] == 1000
    # Cheapest affordable shark is Buddles (15%/day); 3 days on $1000 -> $1520.
    assert res["interest_cost"] == finance.debt_after_days(1000, 15, 3) - 1000
    assert res["benefit"] == 5 * 5000
    assert res["benefit"] > res["interest_cost"]


def test_loan_to_promote_declined_when_interest_exceeds_gain():
    state = {"cash": 4000, "bank": 0}
    # Tiny per-day value: 5 days * $1 = $5 benefit, far below the ~$520 interest.
    res = strategy.evaluate_loan_to_promote(state, value_per_extra_day=1)

    assert res["take"] is False
    assert res["benefit"] < res["interest_cost"]
    assert res["reason"] == "interest exceeds extra-day value"


def test_loan_to_promote_declined_when_expensive_shark_forced():
    state = {"cash": 4000, "bank": 0}
    # Force Odd Lenny (60%/day): $1000 over 3 days compounds to $4096 -> $3096
    # interest, which even a healthy $5000/day * 5 = $25000 benefit... beats.
    # So use a modest per-day value where the 60% shark tips it to "don't".
    res = strategy.evaluate_loan_to_promote(state, value_per_extra_day=500,
                                            shark="Odd Lenny")
    assert res["shark"] == "Odd Lenny"
    assert res["rate"] == 60
    # benefit 5*500=2500; interest = compound(1000,60%,3) - 1000 = 3096.
    assert res["interest_cost"] == finance.debt_after_days(1000, 60, 3) - 1000
    assert res["take"] is False


def test_loan_to_promote_declined_when_no_shark_can_reach():
    # cash 100, next threshold 5000 -> need $4900; max even Odd Lenny gives is
    # 5 * 100 = $500. No shark can supply it.
    state = {"cash": 100, "bank": 0}
    res = strategy.evaluate_loan_to_promote(state, value_per_extra_day=10_000)
    assert res["take"] is False
    assert res["reason"] == "no shark can supply the required loan"


def test_loan_to_promote_skipped_when_rank_already_earned():
    state = {"cash": 4000, "bank": 0, "promotions": ["Small-time operator"]}
    res = strategy.evaluate_loan_to_promote(state, value_per_extra_day=10_000)
    assert res["take"] is False
    assert "already earned" in res["reason"]


# ---------------------------------------------------------------------------
# Last-day liquidation: a plan whose horizon ends never holds inventory.
# ---------------------------------------------------------------------------
def _small_state(**overrides):
    state = {
        "current_city": "New York",
        "cash": 5000,
        "bank": 0,
        "debt": 0,
        "rank": "Dealer",
        "capacity": 100,
        "no_scent": 10,
        "days_left": 1,
        "rumors": [],
        "prices": {
            "New York": {"Cocaine": 6000, "Heroin": 5000},
            "Boston":   {"Cocaine": 9000, "Heroin": 4000},
        },
        "inventory": [{"drug": "Cocaine", "qty": 5, "avg_buy": 5500}],
    }
    state.update(overrides)
    return state


def test_last_day_liquidates_all_inventory():
    # One day left: no room to hop, must sell held goods immediately.
    state = _small_state(days_left=1)
    result = strategy.plan(state)

    liquidations = [a for a in result["actions"] if a["action"] == "liquidate"]
    assert liquidations, "a horizon-ending plan must include a liquidation"
    final = result["actions"][-1]
    assert final["action"] == "liquidate"
    sold = {s["drug"]: s["qty"] for s in final["sell"]}
    assert sold == {"Cocaine": 5}          # every held unit sold
    # Sold at New York's price -> cash grows by 5 * 6000.
    assert result["projected_score"] == 5000 + 5 * 6000


def test_multiday_plan_ends_with_liquidation_and_holds_nothing():
    state = _small_state(days_left=4, cash=20000)
    result = strategy.plan(state, horizon=4)

    assert isinstance(result["projected_score"], int)
    assert isinstance(result["actions"], list)
    assert isinstance(result["notes"], list)
    # The plan must end by liquidating; nothing is carried past the final day.
    final = result["actions"][-1]
    assert final["action"] == "liquidate"
    # No hop schedules an arrival on or after the liquidation day (the horizon may
    # have grown if a promotional loan was taken — reference the actual last day).
    for a in result["actions"]:
        if a["action"] in ("fly", "stay"):
            assert a["day"] + a["arrival_days"] <= final["day"]


def test_plan_prefers_flying_to_a_profitable_arbitrage():
    # Boston pays far more for Cocaine than New York's buy price -> plan should
    # fly there and buy Cocaine.
    state = _small_state(days_left=3, cash=100000, inventory=[])
    result = strategy.plan(state, horizon=3)
    first_hop = next(a for a in result["actions"] if a["action"] in ("fly", "stay"))
    assert first_hop["action"] == "fly"
    assert first_hop["dest"] == "Boston"
    assert any(b["drug"] == "Cocaine" for b in first_hop["buys"])
    # Trading should beat sitting still (starting cash 100000, minus overhead).
    assert result["projected_score"] > 100000


def test_horizon_none_defaults_to_days_left():
    state = _small_state(days_left=1)
    assert strategy.plan(state) == strategy.plan(state, horizon=1)


# ---------------------------------------------------------------------------
# Integration: a real simulator rollout to score a plan (needs p08).
# ---------------------------------------------------------------------------
@pytest.mark.xfail(reason="needs p08", strict=False)
def test_plan_scored_against_real_simulator_rollout():
    from dl2model.simulator import Game

    game = Game(7)
    # Build a state snapshot from the live game world, plan against it, and
    # confirm the plan's projected score is a sane integer. Requires the p08
    # Game API (currently raises NotImplementedError).
    world = game.world
    state = {
        "current_city": "Vancouver",
        "cash": game.player.cash,
        "bank": game.player.bank,
        "debt": game.player.debt,
        "rank": game.player.rank,
        "capacity": finance.capacity_for_rank(game.player.rank),
        "days_left": 5,
        "rumors": [],
        "prices": {city: {drug: m.price for drug, m in drugs.items()}
                   for city, drugs in world.items()},
        "inventory": [],
    }
    result = strategy.plan(state)
    assert isinstance(result["projected_score"], int)
    assert result["actions"][-1]["action"] == "liquidate"
