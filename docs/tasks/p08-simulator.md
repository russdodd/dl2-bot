# p08 — simulator.py (full seedable game engine)

**Files you own:** `dl2model/simulator.py`, `tests/test_simulator.py`
**Wave 2** — depends on ALL of wave 1 (`prng, prices, stock, shipping, risk,
rumors, finance`). Read `docs/tasks/README.md` first.

## What this is
A reproducible Drug Lord 2 world you can step day-by-day and act on, for
backtesting. Compose the wave-1 primitives — **do not reimplement their
formulas**; import and call them.

## Build
- `Game(seed)` builds one `MsvcRand(seed)` and initializes
  `world[city][drug] = Market(price, target, quantity, listed, event)` using
  `prices.initial_price_target` and `stock.initial_quantity` (share the same
  `r`). A `Player` starts per constants (`START_CASH`, `START_DEBT`, Wannabe,
  city of your choice / Vancouver default).
- **Draw order matters** (it's the same PRNG stream): fix and document the order
  in which you consume `rand()` across cities/drugs/events/rumors, and keep it
  stable. The exact in-game order is not fully recovered, so make it a single
  documented convention; the point is reproducibility, not frame-matching the
  original.
- `step_day()`: for every city×drug — move price (`prices.step_price`), pull
  stock (`stock.step_quantity`), roll a `constants.EVENT_PROB` event (apply
  `prices.event_*_price` + `stock.event_*_quantity`), roll listing
  (`stock.is_listed`). Then generate rumors (`RUMOR_GEN_PROB`, current + one
  other city) and schedule their events for the NEXT day per the 70/20/10 rule.
  Then process daily overhead (`finance.daily_overhead`), debt compounding, and
  rank promotion (sustain `RANK_PROMOTE_SUSTAIN_DAYS`, grant +5 days once per
  first promotion). Return the day's rumors.
- Actions (no market impact on price): `buy/sell` adjust cash + inventory +
  market quantity; `fly` changes city and advances a day; `ship` schedules a
  delivery resolved by `shipping.success_prob` after `shipping.expected_transit`
  (model the slip + 3-day grace). `score()` = `finance.final_score`.

## Testing while wave 1 is unmerged
Until the wave-1 branches merge, their functions raise NotImplementedError.
Write engine-logic tests that **monkeypatch** the primitives with tiny fakes
(e.g. `simulator.prices.step_price = lambda *a: (100,100)`), and mark the full
end-to-end determinism test `@pytest.mark.xfail(reason="needs wave-1 merged",
strict=False)`. After wave 1 merges, rebase on `main` and it should pass.

## Tests (`tests/test_simulator.py`) — must include
- determinism: `Game(7)` stepped 10 days twice gives identical `world` (xfail
  until wave 1).
- a buy then sell in the same market moves cash by exactly `(sell-buy)*qty` and
  leaves `market.quantity` conserved (buy decrements, sell increments).
- `score()` ignores inventory (holding 500 units worth a lot but unsold →
  score unchanged vs holding none, given equal cash/bank/debt).
- rank promotion requires the wealth sustained 3 days, not 1.

## Done when
Tests green (integration ones xfail until wave-1 merge), then commit + push + PR.
