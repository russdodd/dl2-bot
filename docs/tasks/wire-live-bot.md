# wire-live-bot — fold dl2model's exact formulas into the live optimizer

**Files you may edit:** `optimizer.py`, `decay.py`, `dl2sweep.py` (decision logic
ONLY), `shiprates.py` (cross-check only), and a new `tests/test_wiring.py`.
**Do NOT edit** `dl2model/*` (frozen, exact model), the OCR/screen layer
(`dl2read.swift`, `dl2input.swift`, `digits.py`, `dl2windows.swift`, `bin/*`), or
`parse.py`. Read `docs/tasks/README.md` and the repo `README.md` first.

## What this is
`dl2model/` is now a complete, tested reimplementation of the DL2.2 market
(prices, stock, shipping, risk, rumors, finance, prng — all merged, 79 tests
green). This task makes the LIVE bot's decision math *use* it, replacing the
old empirical/fitted assumptions. It does **not** change how the bot reads the
screen or clicks — only how it VALUES trades.

## Hard rules (from the project owner — non-negotiable)
- **Never drop or hide a price, and never null an observed value.** The exact-model
  EV is an ADDED column/field ALONGSIDE the raw observed price, never a
  replacement for the headline number. (See the note in `decay.py`: "an honest-EV
  column alongside, never replacing, the headline price.")
- **Trust a confident digit read even when extreme** — spikes are real and are
  where the money is. Do not reintroduce any outlier/median filter.
- **This task is screen-free.** Do not run anything that captures/clicks the game
  (`dl2 plan`/`decide`/`buy`/`ship`/`sell` against the live window). Refactor the
  PURE decision functions and unit-test them with synthetic state. The owner will
  verify against the live game after review.
- Keep changes additive and low-risk: existing behavior should still work if a
  new input (rumor/rank/target) is absent.

## Integration points (all pure, screen-free functions)
Target these functions (see repo README for the map): `optimizer.optimize` /
`optimizer._fill_load`; `dl2sweep.combined_destinations`, `best_net_plan`,
`_arb_ok`; and `decay.py`.

1. **Future sell-price EV (the big correction).** The old model haircut spikes
   by a fitted % (`decay_model.json`) or a flat `ARB_SAFETY` 15%. Replace the
   valuation of a price you'll realize `n` days from now with
   `dl2model.prices.expected_price_after_days(observed_price, target, n)`:
   - `target` is the market's hidden target, not visible in one OCR read — default
     to `M = dl2model.constants.normal_mean(drug, city)` (document this). (Future
     refinement, NOT required now: infer a better target from successive audit-log
     readings of the same city/drug.)
   - `n` = days until you sell: `1` for a flown trade, else
     `dl2model.shipping.expected_transit_days(days)` for a shipment.
   - This makes big spikes decay ~linearly ($16k/day, max $32,767/day), so the bot
     stops under-valuing them the way the exponential fit did. Show the EV as an
     added column; keep the observed price as the headline.

2. **Shipping cost + failure EV.** The bot measures live per-unit rates
   (`shiprates.py`, International/overnight). `dl2model.shipping.shipping_cost`
   is the game's EXACT formula, so it should match. Use it to (a) FILL a missing
   cached rate and (b) cross-check a measured rate — log a `rate_check`-style
   discrepancy to the audit log, don't silently override the measured value.
   Fold `dl2model.shipping.expected_ship_value` (which includes the shipper's
   all-or-nothing success prob) into the net-profit ranking so a delivery-failure
   probability is priced in. Value a plan with the shipper/speed it will actually
   use (current default: International Couriers, overnight); if a cheaper shipper
   yields higher EV, surface it as an advisory — do NOT change the default ship
   action.

3. **Remote stock estimate.** You can't see a city's market until you arrive.
   Use `dl2model.stock.stock_target(observed_price, M, C)` (with
   `C = dl2model.finance.capacity_for_rank(rank)`) to ESTIMATE buyable volume at a
   remote destination for the arb-size calc, clearly labeled an estimate.

4. **Rumor conditioning.** If `state` carries a rumor for a (city, drug)
   (`{drug, city, direction}`), value tomorrow's price with
   `dl2model.rumors.expected_next_price_given_rumor(...)` instead of the ordinary
   decay. (The OCR that reads rumor text off-screen is a SEPARATE later task — for
   now just consume a rumor if present in state; absent → ordinary model.)

5. **No-Scent channel advice.** For the chosen load, call
   `dl2model.risk.channel_recommendation(...)` (combat-loss hook = None → report
   detection prob only) to advise carry-vs-ship. Advisory only.

## State additions (optional, tolerated when absent)
`rank` (str), `bank` (int), `rumors` (list of `{drug,city,direction}`),
`no_scent` (int). Default sensibly when missing so old state JSON still works.

## Keep, don't delete
`dl2 decay` (the empirical projected-vs-realized collector) STAYS — it's now a
cross-check on the exact model, not the source of truth. Don't remove the audit
logging.

## Tests (`tests/test_wiring.py`) — must include
- A synthetic `state` through `optimizer.optimize` / `combined_destinations`
  produces an EV that matches a hand-computed `prices.expected_price_after_days`
  for a known spike (e.g. observed $60,000, M=5100, n=1 → EV ≈ $43,616.5).
- The observed/headline price is still present in the output (nothing dropped).
- Shipping net uses `shipping.expected_ship_value` (failure prob priced in):
  a 0.99 shipper vs a 0.50 shipper on the same trade ranks the 0.99 higher.
- A state with a "scarce" rumor values that drug's destination price ABOVE the
  ordinary-decay value; without the rumor it uses the ordinary value.
- Old state JSON with no `rank`/`rumors`/`no_scent` still runs (back-compat).

## Workflow
Work in the worktree at `~/code/dl2-wt/wire-live-bot` (branch
`task/wire-live-bot`). Run `python3 -m pytest tests/ -q` from the worktree root
until green (the whole suite, so you confirm you didn't break the model tests).
Then commit and push `task/wire-live-bot`. **Do NOT open a PR and do NOT merge to
main** — stop and tell me it's pushed and ready to verify, with a short summary of
what changed and the test results.
