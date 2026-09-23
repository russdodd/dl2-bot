import pytest

from dl2model.montecarlo import _percentile, _summarize, evaluate


# --------------------------------------------------------------------------- #
# statistics (pure; no Game needed)
# --------------------------------------------------------------------------- #
def test_percentile_helper():
    # factor your percentile/summary math into a helper and test it directly
    s = _summarize([-10, 0, 10, 20, 30, 40, 50, 60, 70, 80])
    assert s["p_loss"] == 0.1
    assert s["p50"] == 35  # linear interpolation of the two middle values


def test_summarize_full_distribution():
    s = _summarize([-10, 0, 10, 20, 30, 40, 50, 60, 70, 80])
    # linear-interpolated percentiles over ranks 0..9
    assert s["ev"] == 35.0                 # mean
    assert s["p10"] == pytest.approx(-1.0)  # rank 0.9 between -10 and 0
    assert s["p50"] == pytest.approx(35.0)  # rank 4.5 between 30 and 40
    assert s["p90"] == pytest.approx(71.0)  # rank 8.1 between 70 and 80
    assert s["n"] == 10
    assert s["p10"] < s["p50"] < s["p90"]


def test_summarize_keys():
    s = _summarize([1, 2, 3])
    assert set(s) == {"ev", "p10", "p50", "p90", "p_loss", "n"}


def test_p_loss_counts_strictly_negative():
    # zero is not a loss; only strictly-negative outcomes count
    assert _summarize([0, 0, 0, 0])["p_loss"] == 0.0
    assert _summarize([-1, -1, 1, 1])["p_loss"] == 0.5
    assert _summarize([-5, -5, -5, -5])["p_loss"] == 1.0


def test_all_positive_no_loss():
    s = _summarize([10, 20, 30, 40])
    assert s["p_loss"] == 0.0
    assert s["ev"] == 25.0


def test_single_value():
    s = _summarize([42])
    assert s["ev"] == 42.0
    assert s["p10"] == s["p50"] == s["p90"] == 42.0
    assert s["p_loss"] == 0.0
    assert s["n"] == 1


def test_empty_is_safe():
    s = _summarize([])
    assert s == {"ev": 0.0, "p10": 0.0, "p50": 0.0, "p90": 0.0, "p_loss": 0.0, "n": 0}


def test_percentile_endpoints():
    vals = sorted([-10, 0, 10, 20, 30, 40, 50, 60, 70, 80])
    assert _percentile(vals, 0) == -10.0
    assert _percentile(vals, 100) == 80.0
    assert _percentile([7], 50) == 7.0


def test_percentile_monotonic():
    vals = sorted([3, 1, 4, 1, 5, 9, 2, 6])
    prev = _percentile(vals, 0)
    for p in range(10, 101, 10):
        cur = _percentile(vals, p)
        assert cur >= prev
        prev = cur


# --------------------------------------------------------------------------- #
# rollout / end-to-end (drives the real p08 simulator)
# --------------------------------------------------------------------------- #
def test_evaluate_end_to_end():
    # Buy and hold across two days: unsold inventory is worthless, so every
    # rollout realizes a loss whose size depends on the seed-dependent buy
    # price. That gives a genuine spread to summarize (a same-day round-trip
    # would be deterministic).
    decision = {
        "start": {"city": "New York", "cash": 500000},
        "actions": [("buy", "Cocaine", 50)],
        "resolve_after_days": 2,
    }
    s = evaluate(decision, n=200, base_seed=0)
    assert s["n"] == 200
    assert set(s) == {"ev", "p10", "p50", "p90", "p_loss", "n"}
    # a sane distribution: strictly ordered percentiles, ev inside the p10..p90 band
    assert s["p10"] < s["p50"] < s["p90"]
    assert s["p10"] <= s["ev"] <= s["p90"]
    assert 0.0 <= s["p_loss"] <= 1.0


def test_evaluate_is_deterministic_for_a_fixed_seed():
    decision = {
        "start": {"cash": 500000},
        "actions": [("buy", "Cocaine", 20)],
        "resolve_after_days": 1,
    }
    assert evaluate(decision, n=50, base_seed=7) == evaluate(decision, n=50, base_seed=7)


def test_deterministic_action_has_no_spread():
    # A same-day buy+sell nets to zero cash regardless of the seed-dependent
    # price, so the only movement is fixed daily overhead: no spread, and the
    # percentiles collapse onto a single value.
    decision = {
        "start": {"cash": 500000},
        "actions": [("buy", "Cocaine", 20), ("sell", "Cocaine", 20)],
        "resolve_after_days": 1,
    }
    s = evaluate(decision, n=50, base_seed=0)
    assert s["p10"] == s["p50"] == s["p90"] == s["ev"]
