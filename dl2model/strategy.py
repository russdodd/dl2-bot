"""Multi-day plan search — beyond today's 1-hop decision.

Searches sequences of actions (buy / fly / ship / bank / loan / rank-up) over a
horizon to maximize expected final_score, using the price/stock EV formulas and
the simulator for rollouts. Must respect: game horizon (days_left, +5/promotion),
that inventory is worthless at the end (force liquidation on the last day), loan
compounding, and rank thresholds keying off cash+bank.

DEPENDS ON wave-1 modules + simulator. Write to their contracts; verify once
they merge.

STUB — implement per docs/tasks/p09-strategy.md.
"""
from . import constants as C
from . import prices, stock, shipping, finance, rumors


def plan(state: dict, horizon: int = None) -> dict:
    """Return the best multi-day plan for `state` (schema in the task doc):
    an ordered list of day-by-day actions plus projected final score. Keep it
    tractable (beam/greedy-with-lookahead is fine); document the search."""
    raise NotImplementedError
