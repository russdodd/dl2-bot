# p09 — strategy.py (multi-day plan search)

**Files you own:** `dl2model/strategy.py`, `tests/test_strategy.py`
**Wave 2** — depends on wave-1 modules + `simulator` (p08). Read
`docs/tasks/README.md` first.

## What this is
Beyond today's 1-hop decision: search a sequence of day-by-day actions to
maximize expected `final_score`, using the price/stock EV formulas (and the
simulator for rollouts where useful).

## Must respect
- **Horizon.** `days_left` is hard; each first promotion adds 5 (max 55). Plans
  past the end are invalid.
- **Inventory is worthless at the end** — force full liquidation on the final
  tradable day; never end holding goods.
- **Loans.** Borrowing can vault a rank threshold (keys off cash+bank, sustained
  3 days) to unlock +5 days and higher capacity `C` (which also raises market
  liquidity everywhere), but debt compounds brutally (`finance.debt_after_days`)
  and subtracts from score. Evaluate loan-to-promote explicitly, don't assume.
- **Decay/rumors.** Value a remote destination price with
  `prices.expected_price_after_days` over the actual arrival delay, and override
  with `rumors.expected_next_price_given_rumor` when a rumor is active.
- **Channel.** Use `risk`/`shipping` to choose carry-with-No-Scent vs ship.

## Input `state` schema
Reuse the live optimizer's state shape (see `optimizer.py` docstring) plus:
`rank`, `bank`, `days_left`, `rumors` (list of `{drug,city,direction}`),
`no_scent`. Take `horizon=None` → default to `days_left`.

## Approach
Keep it tractable — greedy-with-lookahead or a small beam search over
(fly/stay, buy set, ship-vs-carry, loan y/n) is fine. **Document the search and
its pruning.** Return
`{"actions": [ {day, action, ...}, ... ], "projected_score": int, "notes": [...]}`.

## Testing while p08/wave-1 unmerged
Unit-test the pure planning helpers with fakes/monkeypatched EV functions. Mark
any test that instantiates a real `Game` `@pytest.mark.xfail(reason="needs p08",
strict=False)`.

## Tests (`tests/test_strategy.py`) — must include
- last-day liquidation: a plan whose horizon ends never carries inventory into
  the final day.
- loan-to-promote: on a crafted state where borrowing crosses a threshold and
  the extra days clearly pay for the interest, the plan takes the loan; on one
  where interest exceeds the gain, it does not.
- a remote spike is valued with multi-day decay, not raw current price.

## Done when
Tests green (integration xfail until deps merge), then commit + push + PR.
