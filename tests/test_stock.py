"""Tests for the exact DL2 stock / availability model."""
from dl2model import stock
from dl2model.constants import trunc_div


class FakeRng:
    """Deterministic rng exposing .rand_mod(n): yields a queued sequence,
    each value taken modulo n (mirroring the game's rand()%n)."""

    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def rand_mod(self, n):
        v = self._values[self._i % len(self._values)]
        self._i += 1
        return v % n


def test_stock_target_table():
    M, C = 5000, 100
    assert stock.stock_target(M // 2, M, C) == 100
    assert stock.stock_target(M, M, C) == 50
    assert stock.stock_target(3 * M // 2, M, C) == 0
    assert stock.stock_target(2 * M, M, C) <= 0   # (planner clamps)


def test_stock_target_quarter_points():
    M, C = 5000, 100
    assert stock.stock_target(3 * M // 4, M, C) == 75
    assert stock.stock_target(5 * M // 4, M, C) == 25


def test_step_quantity_clamps_nonneg():
    assert stock.step_quantity(0, 3 * 5000, 5000, 100) >= 0


def test_step_quantity_pulls_halfway_to_target():
    M, C = 5000, 100
    # target at price==M is 0.5C == 50; halfway between 0 and 50 -> 25.
    assert stock.step_quantity(0, M, M, C) == 25
    # from 100 toward target 50 -> (100+50)//2 == 75.
    assert stock.step_quantity(100, M, M, C) == 75


def test_trunc_div_negatives():
    assert trunc_div(-7, 2) == -3      # not -4


def test_initial_quantity():
    M, C = 5000, 100
    assert stock.initial_quantity(0, C, M) == C          # r=0 -> full cap
    assert stock.initial_quantity(M // 2, C, M) == 50    # r=0.5M -> half cap
    assert stock.initial_quantity(M, C, M) == 0          # r=M -> empty
    # r>M can only come from clamping; ensure never negative.
    assert stock.initial_quantity(2 * M, C, M) == 0


def test_event_shortage_quantity():
    # rand_mod(5) -> 0 => divide by 2.
    assert stock.event_shortage_quantity(100, FakeRng([0])) == 50
    # rand_mod(5) -> 4 => divide by 6.
    assert stock.event_shortage_quantity(100, FakeRng([4])) == 16


def test_event_glut_quantity():
    # rand_mod(5) -> 0 => multiply by 2.
    assert stock.event_glut_quantity(100, FakeRng([0])) == 200
    # rand_mod(5) -> 4 => multiply by 6.
    assert stock.event_glut_quantity(100, FakeRng([4])) == 600


def test_is_listed_event_always_true():
    # event overrides the roll; rng must not even be consulted.
    class Boom:
        def rand_mod(self, n):
            raise AssertionError("rng should not be called when event=True")

    assert stock.is_listed(Boom(), event=True) is True


def test_is_listed_roll_pattern():
    # rand_mod(3): 0 -> not listed, 1 and 2 -> listed (2/3 listed).
    rng = FakeRng([0, 1, 2])
    assert stock.is_listed(rng, event=False) is False
    assert stock.is_listed(rng, event=False) is True
    assert stock.is_listed(rng, event=False) is True
