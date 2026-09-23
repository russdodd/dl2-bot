# p10 — montecarlo.py (EV + variance of a decision)

**Files you own:** `dl2model/montecarlo.py`, `tests/test_montecarlo.py`
**Wave 2** — depends on `simulator` (p08) + `prices`. Read
`docs/tasks/README.md` first.

## What this is
A decision's one-day outcome can be nearly uniform over a huge range (a spike
tomorrow is roughly uniform from target to current price). A point EV hides that.
This runs many seeded rollouts and reports the whole distribution, so the
planner/user can weigh downside, not just the mean.

## Build
`evaluate(decision, n=2000, base_seed=0)`:
- `decision` schema: `{"start": <state dict>, "actions": [...], "resolve_after_days": k}`
  — the actions to take, then roll the world forward `k` days and mark to the
  realized prices/liquidation.
- For `i in range(n)`: build `Game(base_seed + i)` seeded from `decision["start"]`
  (or a fresh seeded world if no exact state), apply the actions, step `k` days,
  compute the realized outcome (Δ score, or realized sell value).
- Return `{"ev","p10","p50","p90","p_loss","n"}` where `p_loss` is the fraction
  of rollouts with outcome < 0.

## Testing while deps unmerged
Unit-test the statistics (given a fixed list of outcomes, assert the percentiles
and `p_loss`), which needs no `Game`. Mark rollout tests
`@pytest.mark.xfail(reason="needs p08", strict=False)`.

## Tests (`tests/test_montecarlo.py`) — must include
```python
def test_percentile_helper():
    # factor your percentile/summary math into a helper and test it directly
    from dl2model.montecarlo import _summarize   # (or your chosen name)
    s = _summarize([-10, 0, 10, 20, 30, 40, 50, 60, 70, 80])
    assert s["p_loss"] == 0.1
    assert s["p50"] == 35  # or documented interpolation
```
Plus an xfail end-to-end `evaluate(...)` sanity test (mean within a sane band,
`p10 < p50 < p90`) once p08 is merged.

## Done when
Stats tests green (rollout xfail until p08), then commit + push + PR.
