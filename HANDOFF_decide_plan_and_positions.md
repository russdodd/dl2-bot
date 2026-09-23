# Handoff: make the `decide` play executable + track positions

## ⚡ Prompt to paste into a fresh Claude session

> You're working on my Drug Lord 2 trade-assistant CLI in `~/code/dl2/`. Read
> `~/code/dl2/HANDOFF_decide_plan_and_positions.md` in full, then implement the
> three additive features it specifies:
> 1. **Persist the `decide` play as an executable plan file**, and make
>    `dl2 buy <City>` / `dl2 ship <City>` replay the WHOLE basket for that city
>    (every drug leg), so a multi-drug plan can't be silently reduced to its
>    headline drug when it's executed.
> 2. **`dl2 positions`** — reconstruct "what's shipped where but not yet sold"
>    (in-transit / stranded stock) purely from the append-only audit log. No new
>    mutable state file — the log is the source of truth.
> 3. **A game-instance marker** so decay pairing and positions don't bleed across
>    separate 55-day playthroughs.
>
> Don't change the trade math (`combined_destinations`, `_arb_ok`, the sweep, the
> digit reads). These are instrumentation + execution-plumbing changes. The tool
> drives my live game screen: anything that buys/ships/sells/decides moves the
> real mouse — run those only in short bursts when I'm hands-off and say "go".
> Everything that reads the audit log or the plan file is pure file I/O (no
> screen) and can be built and tested freely. Ask me to run a live `dl2 decide`
> or `dl2 buy/ship` when you need to exercise the screen-driving paths.

---

## 1. Why this exists — the bug we're preventing

`dl2 decide` computes a rich per-destination BASKET — for each city, a list of
buy-here/sell-there legs `[(drug, qty, buy, sell, profit), ...]` plus the
inventory it can liquidate there — prints it, and logs it to `audit_log.jsonl`
as a `decision` event. **But it never persists an *executable* form of that
basket.** Execution then happens by a human (me, the assistant) hand-typing
`dl2 buy Crack:16401` and `dl2 ship "San Francisco" Crack`, reconstructing the
plan from the printed text.

That's lossy. In this session the San Francisco play was a **2-drug basket**
(Crack **and** Cocaine); it got executed as Crack only, silently dropping the
Cocaine leg (~$71M projected). We recovered it manually because the user was
still in the origin city — but the failure mode is structural: **the executable
unit was a retyped subset, not the plan itself.**

Note the older `dl2 plan` path already does this right — it writes
`/tmp/dl2_plan.json` and `dl2 buy` (no args) replays it. The `decide` path just
never got the same treatment. This handoff brings `decide` up to that bar and
generalizes it to multi-leg baskets + a chosen destination.

**A second instance of the same root cause, same session:** the Cocaine leg was
then bought as `dl2 buy Cocaine:12000` — a hand-typed *quantity* guess — but the
real Moscow stock was 12,370, so 370 units were left behind. Fix already landed:
`dl2 buy` now accepts `Drug:max` / `Drug:all` (or a bare `Drug:`) to buy the
game's pre-filled MAX (the whole affordable stock) instead of a typed number
(`buy_main` parses it to `qty=None`; `buy_plan`/`buy_drug` already buy MAX on
`None`). **The decide plan (feature 1) must still carry the exact per-leg `qty`
from `market[d]["qty"]`** so the recommended size is faithful — `:max` is the
manual fallback, not a substitute for persisting the real quantity.

**Also learned this session (relevant to feature 2):** a single live main-window
Market read is not fully reliable — `_read(window="Drug Lord")["market"]` came
back with 11 drugs and *no Cocaine*, yet `buy_drug` (which scans the main-window
boxes for the exact drug-name row) found and bought Cocaine fine. Lesson: don't
build a positions/state view on one live read; **derive positions from the
append-only audit log**, which is the durable truth.

---

## 2. Project orientation (read these first)

- `~/code/dl2/README.md` — what the tool is and every command.
- `~/code/dl2/HANDOFF_decay_and_shiprates.md` — the PRIOR handoff; its §1
  "Project context" (game mechanics, digit reader, shared-screen protocol) and
  its data-structure notes still apply verbatim. Read it.
- Run it: `~/code/dl2/bin/dl2 <cmd>`. Subcommands today: `plan`, `buy`, `sell`,
  `decide`, `ship`, `audit`, `shiprates`, `decay`.

**Key files & functions for THIS work:**
| File | What matters here |
|---|---|
| `dl2sweep.py` | `decide_main()` (builds `dests` = ranked baskets via `combined_destinations()`; writes `/tmp/dl2_state.json`; logs the `decision` event with `arb_targets`). `buy_main()` / `buy_plan()` / `buy_drug()`. `ship_main()` (now defaults to a 3-way near-equal-VALUE split; reads `/tmp/dl2_state.json`). `main()` (the `plan` path — writes `/tmp/dl2_plan.json`, the pattern to mirror). |
| `audit.py` | `record(event, **fields)`, `load()`. Events carry a game `day` now (buy/ship/sell/decision). Source of truth for positions. |
| `parse.py` | `CANON_CITIES`, `CITY_SHORT`; `status` (location/day). |
| `bin/dl2` | dispatcher — add `positions` (and any `go`) here. |

**Data structures already in place:**
- `/tmp/dl2_state.json` (written by both `decide` and `plan`, DIFFERENT shapes):
  - from `decide`: `{current, prices:{cityFull:{drug:price}}, rates:{cityFull:rate}, unverified, short_cities}` — **no `market`, no executable plan.**
  - from `plan`: `{current_city, cash, capacity, prices, market, inventory, ship, ...}`.
- `/tmp/dl2_plan.json` (written by `plan` only): `{city, rate, trades:[[drug,qty],...]}` — the executable form `dl2 buy` replays. **`decide` writes nothing like this — that's the gap.**
- `audit_log.jsonl` events: `decision` (now includes `arb_targets:[{city(short),drug,buy,projected_sell}]` and `held_spikes`), `buy {drug,qty,price,day}`, `ship {drug,qty,dest,src,day}`, `sell {city,drug,price,qty,revenue,day}`, `note`, `rate_check`.

---

## 3. FEATURE 1 — persist the decide play, execute the whole basket

**Goal:** after `dl2 decide`, executing a chosen city buys/ships EVERY leg of
that city's basket, from a saved plan — no hand-transcription.

**Build:**
1. **`decide_main` writes `/tmp/dl2_decide_plan.json`** right where it already
   logs the `decision` event (it has `dests`, `current`, `status`, `rates` in
   scope). Suggested schema (keep short-city names to match buy/ship):
   ```json
   {
     "origin": "Moscow", "day": 40, "ts": "2026-09-22T..",
     "recommend": "San Francisco",
     "destinations": {
       "San Francisco": {
         "rate": 1728,
         "arb": [
           {"drug":"Crack","qty":16401,"buy":8144,"sell":34250,"net":399864378},
           {"drug":"Cocaine","qty":12000,"buy":7967,"sell":15448,"net":71123810}
         ],
         "inv": [{"drug":"...","qty":...,"sell":...}]
       }
       // ... the same top-6 cities decide already ranks
     }
   }
   ```
   The per-leg `qty` is `market[d]["qty"]` inside `combined_destinations` — note
   it is **not** currently in `arb_targets` (that only logs buy/projected_sell),
   so surface it here. (Consider also adding `qty` to `arb_targets` for decay.)
2. **`dl2 buy <City>`** — when the arg is a city (not `drug:qty` pairs), load
   `/tmp/dl2_decide_plan.json`, find that city's `arb`, and buy **every** leg
   (`buy_plan([(drug, qty), ...])`). Print the full basket + total first. A drug
   the Market won't sell fails safe today (`buy_drug` returns False) — surface
   which legs bought vs not.
3. **`dl2 ship <City>`** — already ships whatever inventory clears the margin to
   that city (and now 3-way splits). Make sure it ships ALL basket drugs, not a
   passed subset, when driven from the plan. Its per-leg margin re-check against
   `/tmp/dl2_state.json` prices stays (defense against a reroll between buy and
   ship).
4. **Staleness guard:** the plan records `origin`+`day`. `dl2 buy/ship <City>`
   should warn (or refuse without `--force`) if the live `status` shows you've
   changed city or the day rolled since the plan was written — you'd be executing
   a stale basket.
5. Keep `dl2 buy Crack:16401` (explicit pairs) working as an override.

**Optional nicety:** a single `dl2 go <City>` that does buy-basket → ship-basket
in one authorized burst. Not required; two commands are fine and let the user
eyeball between steps.

**Testing:** synthesize a `/tmp/dl2_decide_plan.json` and assert `dl2 buy <City>`
would enumerate every leg (dry-run: factor the plan→trades resolution into a pure
function and unit-test it). Only exercise the real buy/ship live, on "go".

---

## 4. FEATURE 2 — `dl2 positions` (derive from the log, no new state file)

**Goal:** always know what's shipped-but-not-yet-sold (in transit / stranded),
so we never fly away forgetting a stranded position (there's currently ~6,042
Heroin sitting in Moscow and 4,170 Ecstacy in St Petersburg — see §6).

**Build:** a pure audit-log analysis (like `decay.py`). Net, per (city, drug):
`sum(ship qty to city) - sum(sell qty at city)`. Positive = still awaiting sale
there. This exact reconstruction already works — the reference query:
```
shipped[(dest,drug)] += ship.qty ;  sold[(city,drug)] += sell.qty
position[(city,drug)] = shipped - sold   # report where > 0
```
Enrich each row with the latest projected sell price for that (city, drug) from
the most recent `decision` (reuse `decay.pairs`-style projection tracking) so the
user sees "~6,042 Heroin at Moscow, ~$18k/u projected ≈ $105M waiting."

Add `dl2 positions` to `bin/dl2` and a `cmd` in a small module (or in `audit.py`,
alongside `review`). **No screen. No mutable state file** — the append-only log
is the truth; a second store would drift the moment a read misfires (as the
Cocaine market read did this session).

**Caveat to encode:** `ship` events with `qty:"all"` (string, not a number)
carry no unit count — count them as "unknown qty present" rather than 0, and say
so, so an all-stock shipment isn't dropped from the position view.

---

## 5. FEATURE 3 — game-instance marker  (PARTLY DONE)

**Why:** the game is a 55-day cycle; a new game resets Day to a low number and
rerandomizes everything. Decay pairing (`decay.py`) and positions must not pair a
new game's `sell` against a prior game's `decision`.

**Already landed** (a game ended mid-session and this was needed live):
- `dl2 newgame [note]` → records a `newgame` event (a segment boundary).
- `audit.latest_segment(entries=None)` → the events at/after the last `newgame`.
- `decay.pairs()` already restricts to `audit.latest_segment()`.

**Still to do:**
- Make **`dl2 positions` (feature 2)** filter through `audit.latest_segment()`
  too, so a dead game's stranded/in-transit stock doesn't show.
- Optional: auto-detect a boundary when an event's `day` is **less** than the
  previous event's day (a new game began without a `newgame` call).

## 5a. Also landed this session (beyond the original 3 features)

- **`dl2 decide` is now CASH-CONSTRAINED.** `combined_destinations(..., cash=)`
  caps buy-arb at available cash (drug+shipping are paid up front), spending the
  budget greedily by highest profit-per-dollar, each leg capped at Market stock;
  `decide_main` passes `status["cash"]` (`cash=None` = old unlimited behavior).
  **Implication for feature 1:** the persisted plan's per-leg `qty` is already the
  *affordable* quantity — execute that, don't re-expand to full stock.
- **`dl2 buy Drug:max` / `:all`** buys the game's pre-filled MAX (whole affordable
  stock) instead of a typed guess — see §1.
- **`dl2 ship`** defaults to a **3-way near-equal-VALUE split** (`--split N`,
  `--split 1` for one shipment, `--batch S` to cap units/shipment).
- **Free plane-carry.** `combined_destinations(..., carry=)` models the units you
  bring on the plane FOR FREE when you fly (no shipping), a single pool per
  destination bounded by inventory capacity (`DEFAULT_CARRY`, env `DL2_CARRY`,
  default 500). Held inventory rides free first, then bought goods; overflow pays
  the flat `rate`. A carried unit saves `rate` uniformly, so a thin trade that
  can't clear shipping can still be worth carrying — the arb does a two-phase
  greedy (free slots, then shipped). `decide` passes `carry=min(DL2_CARRY,
  capacity)` and prints a `carry/ship` column. **Execution gap to close (feature
  1):** the plan should record, per leg, how many units are carried vs shipped —
  a carry-only basket (≤ carry) needs NO `dl2 ship` at all (just buy → fly →
  sell); only the shipped overflow goes through `dl2 ship`.

Don't over-build feature 3 — a marker + "only look at the latest segment" is enough.

---

## 6. Current game state (live snapshot — will drift as you play)

- **Location:** Moscow, **Day 40/55**, cash ~$2.68B (mid-game, cash-rich).
- **In transit / awaiting sale** (reconstructed from the log — this is exactly
  what `dl2 positions` should print):
  | City | Drug | Units | Note |
  |---|---|--:|---|
  | San Francisco | Crack | 16,401 | just shipped (3×5,467); projected sell $34,250 (6× spike) |
  | San Francisco | Cocaine | 12,000 | just shipped (3×4,000); projected sell $15,448 |
  | Moscow | Heroin | 6,042 | the delayed 3rd shipment of the Toronto→Moscow play; sell on return (~$17–18k/u) |
  | St Petersburg | Ecstacy | 4,170 | older stranded stock (pre-session); collect when passing through |
- **Next physical step (user does in-game):** fly to San Francisco, then
  `dl2 sell` to bank the Crack+Cocaine basket. That sell is day-stamped and feeds
  `dl2 decay`.
- **Decay data:** 1 clean day-stamped pair so far — Heroin @ Moscow, realized
  $17,389 vs projected $18,465 = **94% over 1 day**. Need ~10 with a day gap
  before `decay.py` fits `ratio≈e^(-k·days)` into `decay_model.json`. Keep
  trading; every arb sale adds a point.
- **Rate store (`ship_rates.json`):** origins **Toronto** (14/14) and **Moscow**
  (14/14) filled. `decide` skips the slow Shipping-window measurement when the
  current origin's 14 rates are cached fresh (same game day). `dl2 shiprates`
  prints the matrix.

---

## 7. Conventions

- Match the codebase style (terse, comment density like the surrounding code).
- The audit log and the rate store are the user's real data — append/merge,
  never clobber. `record()` and the stores already swallow errors so logging
  can't break a trade; keep that property.
- Everything must degrade gracefully (never raise into a live buy/ship/sell).
- Build & test the pure-analysis / plan-resolution parts against the existing
  `audit_log.jsonl` and synthetic fixtures — no screen. Only the buy/ship/sell/
  decide paths touch the screen; run those in short bursts on the user's "go".
- Shared-screen protocol and game mechanics: see the prior handoff's §1.
