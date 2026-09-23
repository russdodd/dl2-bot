"""Monte-Carlo EV + variance of a decision over many simulated futures.

Given a decision (e.g. "fly to Boston, carry 200 Cocaine, sell on arrival"),
run N seeded simulator rollouts of the intervening days and report the
distribution of realized outcome (mean, p10/p50/p90, prob-of-loss). This is how
we price spike-chases whose one-day price is nearly uniform over a huge range.

DEPENDS ON simulator + prices. Write to their contracts; verify once they merge.

STUB — implement per docs/tasks/p10-montecarlo.md.
"""
from . import constants as C
from . import prices
from .simulator import Game


def evaluate(decision: dict, n: int = 2000, base_seed: int = 0) -> dict:
    """Run n rollouts of `decision` (schema in the task doc) and return
    {'ev': float, 'p10':..., 'p50':..., 'p90':..., 'p_loss':..., 'n': n}."""
    raise NotImplementedError
