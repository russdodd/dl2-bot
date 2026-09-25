"""Tests for dl2model.planner — the MC receding-horizon day-1 ranker (Phase B).

Uses small, hand-constructed states and the real (seeded) simulator. Assertions
are on RANKING and on the shape of the outcome distribution, not exact dollar EV,
because the rollout is genuinely stochastic (spike decay, 2/3 listing risk).
"""
from dl2model import planner


def _summary_keys(c):
    return {k: c[k] for k in ("ev", "p10", "p50", "p90", "p_loss", "n")}


# --------------------------------------------------------------------------- #
# horizon=1 degenerates to immediate liquidation
# --------------------------------------------------------------------------- #
def test_horizon_1_is_immediate_liquidation():
    # Only the current city in view (no arb, no travel). Holding 10 Cocaine that
    # the local market lists -> the plan just liquidates here for qty*price, with
    # no spread (no day is stepped, so no RNG is consumed after the sale).
    state = {
        "current_city": "New York", "cash": 10000, "bank": 0, "debt": 0,
        "capacity": 100,
        "prices": {"New York": {"Cocaine": 5000}},
        "market": {"Cocaine": {"price": 5000, "qty": 1000}},
        "inventory": [{"drug": "Cocaine", "qty": 10}],
    }
    res = planner.plan_mc(state, horizon=1, n=16, base_seed=0)
    best = res["best"]
    assert best["dest"] == "New York"
    assert best["ev"] == 10 * 5000                      # liquidated held inventory
    assert best["p10"] == best["p50"] == best["p90"] == best["ev"]
    assert best["p_loss"] == 0.0


# --------------------------------------------------------------------------- #
# a spike+rumor in a reachable city is chased
# --------------------------------------------------------------------------- #
def test_prefers_chasing_a_rumored_spike():
    # Cheap Cocaine buyable in New York; a "scarce" rumor for Boston means the next
    # day likely spikes there (§4a 70%). Flying to Boston with a load should rank
    # #1, well above staying (which has nothing to sell).
    state = {
        "current_city": "New York", "cash": 1_000_000, "bank": 0, "debt": 0,
        "capacity": 100,
        "prices": {"New York": {"Cocaine": 5000},
                   "Boston": {"Cocaine": 6000}},
        "market": {"Cocaine": {"price": 5000, "qty": 1000}},
        "inventory": [],
        "rumors": [{"drug": "Cocaine", "city": "Boston", "direction": "scarce"}],
    }
    res = planner.plan_mc(state, horizon=2, n=150, base_seed=0)
    assert res["best"]["dest"] == "Boston"
    boston = next(c for c in res["candidates"] if c["dest"] == "Boston")
    stay = next(c for c in res["candidates"] if c["dest"] == "New York")
    assert boston["ev"] > stay["ev"]
    assert boston["ev"] > 0


# --------------------------------------------------------------------------- #
# the higher-margin reachable city wins a plain arb (hand-reasoned optimum)
# --------------------------------------------------------------------------- #
def test_ranks_highest_margin_destination_first():
    # Buy Cocaine cheap in New York (2000). Boston sells at 5000 (M=6120, so it
    # even decays UPWARD); Detroit at 3000 (M=4080). Boston is the clear optimum.
    state = {
        "current_city": "New York", "cash": 1_000_000, "bank": 0, "debt": 0,
        "capacity": 100,
        "prices": {"New York": {"Cocaine": 2000},
                   "Boston": {"Cocaine": 5000},
                   "Detroit": {"Cocaine": 3000}},
        "market": {"Cocaine": {"price": 2000, "qty": 1000}},
        "inventory": [],
    }
    res = planner.plan_mc(state, horizon=2, n=150, base_seed=0)
    assert res["best"]["dest"] == "Boston"
    detroit = next(c for c in res["candidates"] if c["dest"] == "Detroit")
    boston = next(c for c in res["candidates"] if c["dest"] == "Boston")
    assert boston["ev"] > detroit["ev"]


# --------------------------------------------------------------------------- #
# listing risk / spike decay surface as downside mass
# --------------------------------------------------------------------------- #
def test_listing_risk_shows_up_as_downside():
    # A thin-margin arb: buy Cocaine 5000 in New York, sell in Boston at 6000
    # (near M). On arrival the drug is listed only 2/3 of the time (decode §9); if
    # unlisted through the final day the load is stuck and worthless -> a real loss.
    # So the flying candidate must show nonzero p_loss and p10 below its EV.
    state = {
        "current_city": "New York", "cash": 1_000_000, "bank": 0, "debt": 0,
        "capacity": 100,
        "prices": {"New York": {"Cocaine": 5000},
                   "Boston": {"Cocaine": 6000}},
        "market": {"Cocaine": {"price": 5000, "qty": 1000}},
        "inventory": [],
    }
    res = planner.plan_mc(state, horizon=2, n=300, base_seed=0)
    boston = next(c for c in res["candidates"] if c["dest"] == "Boston")
    assert boston["buys"], "expected the arb to buy a Cocaine load"
    assert boston["p_loss"] > 0.0
    assert boston["p10"] < boston["ev"]


# --------------------------------------------------------------------------- #
# structure + determinism
# --------------------------------------------------------------------------- #
def test_result_structure_and_ordering():
    state = {
        "current_city": "New York", "cash": 500_000, "capacity": 100,
        "prices": {"New York": {"Cocaine": 4000}, "Boston": {"Cocaine": 6000}},
        "market": {"Cocaine": {"price": 4000, "qty": 1000}},
        "inventory": [],
    }
    res = planner.plan_mc(state, horizon=3, n=60, base_seed=0)
    assert res["horizon"] == 3 and res["n"] == 60
    assert res["best"] is res["candidates"][0]
    evs = [c["ev"] for c in res["candidates"]]
    assert evs == sorted(evs, reverse=True)
    for c in res["candidates"]:
        assert set(("ev", "p10", "p50", "p90", "p_loss", "n")) <= set(c)


def test_deterministic_for_fixed_base_seed():
    state = {
        "current_city": "New York", "cash": 500_000, "capacity": 100,
        "prices": {"New York": {"Cocaine": 4000}, "Boston": {"Cocaine": 6000}},
        "market": {"Cocaine": {"price": 4000, "qty": 1000}},
        "inventory": [],
    }
    a = planner.plan_mc(state, horizon=2, n=40, base_seed=5)
    b = planner.plan_mc(state, horizon=2, n=40, base_seed=5)
    assert [_summary_keys(c) for c in a["candidates"]] == \
           [_summary_keys(c) for c in b["candidates"]]
