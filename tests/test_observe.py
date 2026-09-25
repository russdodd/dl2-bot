"""Tests for dl2model.observe — the state-conditioned world sampler (Phase A).

Verifies the day-0 world MATCHES the observation, hidden targets are sampled per
decode-reference §2-§3, spikes are flagged event-active with normal-range targets,
active rumors land in `_scheduled` and materialize at the decoded 70/20/10 rate
(§4a), and unobserved markets fall back to the seeded prior.
"""
from dl2model import observe
from dl2model.constants import normal_mean
from dl2model.simulator import Game


class FakeRng:
    """Deterministic rand_mod: yields seq[i] % n, cycling. (Same shape as the
    other suites' fakes.)"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0

    def rand_mod(self, n):
        v = self.seq[self.i % len(self.seq)]
        self.i += 1
        return v % n if n > 0 else 0


CITY, DRUG = "New York", "Cocaine"
M = normal_mean(DRUG, CITY)          # 5100


# --------------------------------------------------------------------------- #
# classify_price / sample_target (pure, fake rng)
# --------------------------------------------------------------------------- #
def test_classify_price_bands():
    assert observe.classify_price(M, M) == "normal"
    assert observe.classify_price(M // 2, M) == "normal"        # band floor
    assert observe.classify_price(int(1.5 * M), M) == "normal"  # band ceiling (not >)
    assert observe.classify_price(int(1.5 * M) + 1000, M) == "shortage"
    assert observe.classify_price(60000, M) == "shortage"       # a real spike
    assert observe.classify_price(int(0.2 * M) - 1, M) == "glut"


def test_sample_target_normal_stays_near_price_and_in_band():
    # rng returns the midpoint of the ±M//10 window -> target == price.
    half = max(1, M // 10)
    target, event = observe.sample_target(5000, M, FakeRng([half]))
    assert event == 0
    assert target == 5000
    # clamp to the normal band [M//2, M//2 + M - 1].
    lo, hi = M // 2, M // 2 + M - 1
    t_lo, _ = observe.sample_target(lo, M, FakeRng([0]))          # pushes below floor
    assert t_lo >= lo
    t_hi, _ = observe.sample_target(hi, M, FakeRng([2 * half]))   # pushes above ceiling
    assert t_hi <= hi


def test_sample_target_spike_is_event_with_normal_target():
    target, event = observe.sample_target(60000, M, FakeRng([0]))
    assert event == +1                                   # shortage active
    assert M // 2 <= target <= M // 2 + M - 1            # fresh NORMAL draw (not the spike)


def test_sample_target_crash_is_glut_event():
    target, event = observe.sample_target(int(0.1 * M), M, FakeRng([0]))
    assert event == -1
    assert M // 2 <= target <= M // 2 + M - 1


# --------------------------------------------------------------------------- #
# sample_world — day-0 reproduction
# --------------------------------------------------------------------------- #
def test_observed_price_and_quantity_reproduced_exactly():
    state = {
        "current_city": CITY, "cash": 12345, "bank": 100, "debt": 50,
        "prices": {CITY: {DRUG: 5000, "Heroin": 8000}},
        "stock": {CITY: {DRUG: 42, "Heroin": 7}},
    }
    g = observe.sample_world(state, seed=1)
    assert g.world[CITY][DRUG].price == 5000
    assert g.world[CITY][DRUG].quantity == 42
    assert g.world[CITY]["Heroin"].price == 8000
    assert g.world[CITY]["Heroin"].quantity == 7
    # normal observed price -> target inside the band, no event.
    assert g.world[CITY][DRUG].event == 0
    assert M // 2 <= g.world[CITY][DRUG].target <= M // 2 + M - 1


def test_player_attributes_applied():
    state = {"current_city": "Boston", "cash": 999, "bank": 5, "debt": 3,
             "rank": "Dealer", "no_scent": 2,
             "inventory": [{"drug": "Cocaine", "qty": 8}],
             "prices": {"Boston": {"Cocaine": 6000}}}
    g = observe.sample_world(state, seed=1)
    assert g.player.city == "Boston"
    assert (g.player.cash, g.player.bank, g.player.debt) == (999, 5, 3)
    assert g.player.rank == "Dealer" and g.player.no_scent == 2
    assert g.player.inventory["Cocaine"]["qty"] == 8


def test_spike_price_flagged_event_active():
    state = {"current_city": CITY, "prices": {CITY: {DRUG: 60000}}}
    g = observe.sample_world(state, seed=3)
    m = g.world[CITY][DRUG]
    assert m.price == 60000                       # observed spike kept exactly
    assert m.event == +1                          # flagged event-active
    assert M // 2 <= m.target <= M // 2 + M - 1   # target is a fresh NORMAL draw


def test_unobserved_markets_fall_back_to_seeded_prior():
    state = {"current_city": CITY, "prices": {CITY: {DRUG: 5000}}}
    g = observe.sample_world(state, seed=7)
    fresh = Game(7)
    # A market we never observed is byte-identical to the fresh seeded world.
    assert g.world["Boston"]["Heroin"] == fresh.world["Boston"]["Heroin"]
    assert g.world["Sydney"]["Pot"] == fresh.world["Sydney"]["Pot"]


def test_deterministic_for_fixed_seed():
    state = {"current_city": CITY, "prices": {CITY: {DRUG: 5000}},
             "stock": {CITY: {DRUG: 30}}}
    a = observe.sample_world(state, seed=11).world[CITY][DRUG]
    b = observe.sample_world(state, seed=11).world[CITY][DRUG]
    assert a == b


# --------------------------------------------------------------------------- #
# rumor injection + materialization at the decoded rate
# --------------------------------------------------------------------------- #
def test_rumor_lands_in_scheduled():
    state = {"current_city": CITY,
             "prices": {CITY: {DRUG: 5000}},
             "rumors": [{"drug": DRUG, "city": "Boston", "direction": "scarce"},
                        {"drug": "Heroin", "city": "Miami", "direction": "abundant"}]}
    g = observe.sample_world(state, seed=1)
    assert g._scheduled[("Boston", DRUG)] == +1
    assert g._scheduled[("Miami", "Heroin")] == -1


def test_scheduled_rumor_materializes_at_decoded_rate():
    # A scarce rumor for Boston/Cocaine should fire a shortage (~70%) on the next
    # step_day across many seeds (decode §4a: 70/20/10). We measure the realized
    # predicted-direction rate and check it against the decoded band.
    predicted, opposite, n = 0, 0, 600
    for seed in range(n):
        state = {"current_city": CITY, "prices": {CITY: {DRUG: 5000}},
                 "rumors": [{"drug": DRUG, "city": "Boston", "direction": "scarce"}]}
        g = observe.sample_world(state, seed)
        g.step_day()
        ev = g.world["Boston"][DRUG].event
        if ev == +1:
            predicted += 1
        elif ev == -1:
            opposite += 1
    # ~70% predicted, ~10% opposite (generous bands for the sample size).
    assert 0.62 <= predicted / n <= 0.78
    assert 0.04 <= opposite / n <= 0.16
