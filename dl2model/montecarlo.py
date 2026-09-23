"""Monte-Carlo EV + variance of a decision over many simulated futures.

Given a decision (e.g. "fly to Boston, carry 200 Cocaine, sell on arrival"),
run N seeded simulator rollouts of the intervening days and report the
distribution of realized outcome (mean, p10/p50/p90, prob-of-loss). This is how
we price spike-chases whose one-day price is nearly uniform over a huge range:
a point EV hides that the downside is a real chunk of the mass.

DEPENDS ON simulator + prices. Write to their contracts; verify once they merge.

The statistics (`_summarize`, `_percentile`) are pure and are tested directly.
The rollout (`_rollout`/`evaluate`) drives a `Game` and only runs end-to-end
once p08 (simulator) is merged.
"""
from math import floor, ceil

from .simulator import Game


# --------------------------------------------------------------------------- #
# statistics (pure; no Game needed)
# --------------------------------------------------------------------------- #
def _percentile(sorted_vals, p: float) -> float:
    """The `p`-th percentile (0..100) of an already-sorted sequence, using the
    standard linear interpolation between closest ranks (numpy's default
    "linear" method). `sorted_vals` must be sorted ascending and non-empty."""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    rank = (p / 100.0) * (n - 1)
    lo = floor(rank)
    hi = ceil(rank)
    if lo == hi:
        return float(sorted_vals[lo])
    frac = rank - lo
    return float(sorted_vals[lo]) + frac * (sorted_vals[hi] - sorted_vals[lo])


def _summarize(outcomes) -> dict:
    """Summarize a list of realized outcomes into the reported distribution.

    Returns {'ev','p10','p50','p90','p_loss','n'} where `p_loss` is the fraction
    of outcomes strictly less than 0. Percentiles use linear interpolation, so
    p50 of an even-length sample is the average of the two middle values.
    """
    n = len(outcomes)
    if n == 0:
        return {"ev": 0.0, "p10": 0.0, "p50": 0.0, "p90": 0.0, "p_loss": 0.0, "n": 0}
    s = sorted(outcomes)
    ev = sum(outcomes) / n
    p_loss = sum(1 for x in outcomes if x < 0) / n
    return {
        "ev": ev,
        "p10": _percentile(s, 10),
        "p50": _percentile(s, 50),
        "p90": _percentile(s, 90),
        "p_loss": p_loss,
        "n": n,
    }


# --------------------------------------------------------------------------- #
# rollout (drives the simulator; end-to-end once p08 merges)
# --------------------------------------------------------------------------- #
def _new_game(start, seed: int) -> Game:
    """Build a `Game(seed)` and, if `start` carries an exact state, apply the
    documented player overrides onto it. With no `start`, this is just a fresh
    seeded world."""
    game = Game(seed)
    if start:
        player = game.player
        for attr in ("city", "cash", "bank", "debt", "rank", "no_scent"):
            if attr in start:
                setattr(player, attr, start[attr])
        if "inventory" in start:
            player.inventory = dict(start["inventory"])
    return game


def _apply(game: Game, action) -> None:
    """Apply one action to `game`. An action is either a mapping with an "op"
    key, or a (op, *args) tuple. Supported ops mirror the Game player actions:
    buy, sell, fly, ship."""
    if isinstance(action, dict):
        op = action["op"]
        args = {k: v for k, v in action.items() if k != "op"}
        getattr(game, op)(**args)
    else:
        op, *args = action
        getattr(game, op)(*args)


def _rollout(decision: dict, seed: int) -> float:
    """One seeded rollout: seed a world, apply the actions, resolve k days, and
    return the realized Δ score."""
    game = _new_game(decision.get("start"), seed)
    before = game.score()
    for action in decision.get("actions", ()):
        _apply(game, action)
    for _ in range(int(decision.get("resolve_after_days", 0))):
        game.step_day()
    after = game.score()
    return after - before


def evaluate(decision: dict, n: int = 2000, base_seed: int = 0) -> dict:
    """Run `n` seeded rollouts of `decision` and return the outcome distribution.

    `decision` schema:
        {"start": <state dict or None>, "actions": [...], "resolve_after_days": k}

    Each rollout builds `Game(base_seed + i)` (seeded from `decision["start"]` if
    given), applies the actions, steps the world forward `k` days, and marks the
    outcome to the realized state (Δ score). Returns
    {'ev','p10','p50','p90','p_loss','n'} where `p_loss` is the fraction of
    rollouts whose outcome is < 0.
    """
    outcomes = [_rollout(decision, base_seed + i) for i in range(n)]
    return _summarize(outcomes)
