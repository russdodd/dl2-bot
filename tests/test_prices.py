"""Tests for dl2model.prices — exact DL2 price momentum + spike-decay expectation."""
from dl2model import prices
import pytest


class FakeRng:
    """Deterministic rand_mod for step tests. Yields seq[i] % n, tracks consumption."""

    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0

    def rand_mod(self, n):
        v = self.seq[self.i]
        self.i += 1
        return v % n if n > 0 else 0


def test_expected_rand_mod_table():
    assert prices.expected_rand_mod(5000) == pytest.approx(2405, abs=1)
    assert prices.expected_rand_mod(10000) == pytest.approx(4694, abs=1)
    assert prices.expected_rand_mod(20000) == pytest.approx(8591, abs=1)
    assert prices.expected_rand_mod(30000) == pytest.approx(13849, abs=1)
    assert prices.expected_rand_mod(32768) == pytest.approx(16383.5)
    assert prices.expected_rand_mod(99999) == pytest.approx(16383.5)
    assert prices.expected_rand_mod(0) == 0.0


def test_expected_rand_mod_nonpositive():
    assert prices.expected_rand_mod(0) == 0.0
    assert prices.expected_rand_mod(-100) == 0.0


def test_spike_one_day():
    # $60k Cocaine spike toward M≈5100: expect ~$43,616.5, floor $27,233
    assert prices.expected_next_price(60000, 5100) == pytest.approx(43616.5, abs=1)


def test_expected_next_price_directions():
    # gap == 0 -> price unchanged
    assert prices.expected_next_price(5000, 5000) == 5000.0
    # gap > 0 -> drifts up toward higher target
    up = prices.expected_next_price(5000, 6000)
    assert up == pytest.approx(5000 + prices.expected_rand_mod(1000))
    assert up > 5000
    # gap < 0 -> drifts down toward lower target
    down = prices.expected_next_price(6000, 5000)
    assert down == pytest.approx(6000 - prices.expected_rand_mod(1000))
    assert down < 6000


def test_expected_price_after_days_converges_toward_target():
    # A big spike decays toward target over successive days, staying above it.
    p0 = prices.expected_price_after_days(60000, 5100, 1)
    assert p0 == pytest.approx(prices.expected_next_price(60000, 5100))
    p3 = prices.expected_price_after_days(60000, 5100, 3)
    # monotonically decreasing, still above target after a few days
    assert p3 < p0
    assert p3 > 5100
    # zero days is a no-op
    assert prices.expected_price_after_days(60000, 5100, 0) == 60000.0


def test_signed_rand_sign_handling():
    # gap > 0 -> positive rand_mod(gap)
    assert prices.signed_rand(100, FakeRng([37])) == 37
    # gap < 0 -> negated rand_mod(|gap|)
    assert prices.signed_rand(-100, FakeRng([37])) == -37
    # gap == 0 -> 0, and rng is NOT consumed
    rng = FakeRng([])
    assert prices.signed_rand(0, rng) == 0
    assert rng.i == 0


def test_new_target_range():
    M = 5100
    # M//2 + rand_mod(M); draw 0 -> lower bound, draw M-1 -> near upper bound
    assert prices.new_target(M, FakeRng([0])) == M // 2
    assert prices.new_target(M, FakeRng([M - 1])) == M // 2 + (M - 1)


def test_initial_price_target():
    M = 5100
    r = 1234
    price, target, out_r = prices.initial_price_target(M, FakeRng([r]))
    assert (price, target, out_r) == (M // 2 + r, M // 2 + r, r)


def test_step_price_reselect_fires():
    # small old_gap relative to new price -> abs(old_gap) < new_price//10 -> reselect
    M = 5100
    price, target = 1000, 1050  # old_gap = 50
    # draw 40 for signed_rand(50) -> new_price = 1040; 1040//10 = 104 > 50 -> fires
    # draw 100 for new_target -> 2550 + 100 = 2650
    rng = FakeRng([40, 100])
    new_price, new_target = prices.step_price(price, target, M, rng)
    assert new_price == 1040
    assert new_target == M // 2 + 100  # 2650, target changed
    assert new_target != target
    assert rng.i == 2  # both draws consumed


def test_step_price_reselect_does_not_fire():
    # large old_gap -> abs(old_gap) >= new_price//10 -> target unchanged
    M = 5100
    price, target = 1000, 6000  # old_gap = 5000
    # draw 100 for signed_rand(5000) -> new_price = 1100; 1100//10 = 110 < 5000 -> no reselect
    rng = FakeRng([100])
    new_price, new_target = prices.step_price(price, target, M, rng)
    assert new_price == 1100
    assert new_target == 6000  # unchanged
    assert rng.i == 1  # only the drift draw consumed, no target draw


def test_event_shortage_price():
    M = 5100
    # (M + rand_mod(M//2)) * (5 + rand_mod(5))
    # draws: 200 -> 200 % 2550 = 200 ; 3 -> 3 % 5 = 3
    rng = FakeRng([200, 3])
    assert prices.event_shortage_price(M, rng) == (M + 200) * (5 + 3)


def test_event_glut_price():
    M = 5100
    # (M - rand_mod(M//2)) // (5 + rand_mod(5))
    rng = FakeRng([200, 3])
    assert prices.event_glut_price(M, rng) == (M - 200) // (5 + 3)
