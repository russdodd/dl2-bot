# Handoff: two features for the Drug Lord 2 assistant

## ⚡ Prompt to paste into a fresh Claude session

> You're working on my Drug Lord 2 trade-assistant CLI in `~/code/dl2/`. Read
> `~/code/dl2/HANDOFF_decay_and_shiprates.md` in full, then implement the two
> features it specifies:
> 1. **A persistent shipping-rate store** so we stop re-measuring rates on every
>    `dl2 decide` (measuring drives the screen and is slow).
> 2. **Decay data collection** — instrument the tool so it records, for every
>    sale, the price we *projected* vs the price we *realized* and how many game
>    days elapsed, into the audit log; plus a `dl2 decay` command that pairs and
>    reports that data. **Do NOT hardcode any decay percentage** — we only have 3
>    data points; the goal is to *collect* more so a decay curve can be fit later.
>
> Both are additive/instrumentation changes — don't alter the trade logic or the
> reads. The tool drives my live game screen, so anything that captures/clicks
> must be run in short bursts with me hands-off (I'll say "go"); read-only static
> analysis (parsing the audit log, the rate store) needs no screen. Ask me to run
> a live `dl2 decide` when you need to exercise the screen-driving paths.

---

## 1. Project context (read first)

**What it is:** a CLI that reads the live *Drug Lord 2.2* game (2003, runs under
CrossOver/Wine on macOS) by screen-capture + OCR, and prints the profit-maximizing
trades. No memory hacking — it captures the game window (ScreenCaptureKit),
OCRs it (Vision), then re-reads every number exactly with a custom fixed-font
**digit-template reader** (`digits.py`). It can also drive the mouse/keyboard to
buy / ship / sell.

**Run it:** `~/code/dl2/bin/dl2 <cmd>`. `bin/dl2` auto-selects a Python that has
`numpy`+`PIL` (the user's anaconda `python3`; `/usr/bin/python3` has neither).
Subcommands: `plan`, `buy`, `sell`, `decide`, `ship`, `audit`.

**Key files:**
| File | What |
|---|---|
| `dl2sweep.py` | The brain. `sweep()`, `decide_main()`, `combined_destinations()`, `best_net_plan()`, `measure_ship_rates()`, `recover_right_prices()`, `recover_market_prices()`, `buy_drug/ship_drugs/sell_drug/sell_all_inventory`, `_arb_ok`, `ARB_SAFETY`. |
| `digits.py` | `DigitReader`, `refine_numbers()` — exact price reads. Don't touch for these features. |
| `parse.py` | OCR→state; `CANON_DRUGS` (17, alphabetical), `CANON_CITIES` (15 full names), `CITY_SHORT` (short names). |
| `audit.py` | Append-only JSONL trade log. `record(event, **fields)`, `load()`, CLI `dl2 audit` / `audit review` / `audit note`. **This is the backbone for feature 2.** |
| `bin/dl2` | Dispatcher; add new subcommands here. |

**Game facts you need:**
- The game shows **world prices for every city at once** (the "World Cities"
  view), so buy-here/sell-there/fly-to is solvable. Every city lists all 17
  drugs. Prices **re-randomize daily**; a drug spikes occasionally (huge price).
- You can only **sell** a drug where that city's *Market* (the buyable subset,
  main-window left panel) currently lists it. World prices are reference.
- **Shipping** = a flat per-unit cost, drug-independent, that **varies by
  origin→destination distance** (e.g. from Toronto: Detroit $51, Sydney $2,266;
  from St Petersburg: London $14, Beijing $218). Overnight, International Couriers.
- Flying/shipping costs a **day**, and shipments can be **delayed** extra days.
- **MEASURED THIS SESSION — important for feature 2:** the gap between a spike's
  displayed price and what you actually sell for is **pure time-decay of the
  spike**, NOT market impact. Selling 4,120 Morphine did **not** move the price
  ($6,654 → $6,654). So the decay model should be a function of **days elapsed
  only**, independent of quantity sold.

**Data structures already in place:**
- `/tmp/dl2_state.json` (ephemeral, rewritten each `decide`):
  `{ "current": <short>, "prices": {city_full: {drug: price}}, "rates":
  {city_full: rate}, "unverified": [...], "short_cities": {...} }`
- `~/code/dl2/audit_log.jsonl` — one JSON object per line. Current events:
  - `decision`: `{ts, event:"decision", city, day, inventory:{drug:qty}, safety,
    sell_now_total, recommend, recommend_value,
    candidates:[{city, combined, inv_net, arb_net}],
    held_spikes:[{drug, city, price, mult}]}`
    — `day` is the game day (`X` of `X/55`, from the Status box) or null.
    — `held_spikes` = projected SELL prices for drugs you already HOLD (inventory
      liquidation targets). **Arb (buy-here/sell-there) projected sell prices are
      NOT logged yet** — feature 2 adds them.
  - `buy`: `{ts, event:"buy", drug, qty, price}`
  - `ship`: `{ts, event:"ship", drug, qty, dest, src}`
  - `sell`: `{ts, event:"sell", city, drug, price, qty, revenue}` — **no game-day
    field yet; feature 2 adds it.**
  - `note`: `{ts, event:"note", text}`

**Shared-screen protocol:** the buy/ship/sell/decide paths move the real mouse &
keyboard. Only run them when the user is hands-off and says go. Everything in
these two features that reads the *audit log* or the *rate store* is pure file
I/O — no screen — and can be built and tested freely.

---

## 2. FEATURE A — persistent shipping-rate store

**Problem:** `measure_ship_rates(current_short)` (dl2sweep.py ~line 569) opens the
Shipping window and iterates every destination reading the courier cost — a slow
screen-driving session run on **every** `dl2 decide`. Rates only live in
`/tmp/dl2_state.json` for the current origin and get overwritten. We recompute
constantly.

**Goal:** a persistent, growing store of origin→dest rates that `decide` reads
from, so we rarely need to re-measure.

**Build:**
1. **`~/code/dl2/ship_rates.json`** — schema:
   ```json
   { "<origin_short>": { "<dest_short>": {"rate": 1068.0, "day": 34, "ts": "2026-09-22T.."} } }
   ```
   (short names = `parse.CITY_SHORT`, e.g. "Toronto", "St Petersburg".)
2. A small module or helpers (e.g. `shiprates.py`, or functions in dl2sweep):
   `load_rates()`, `get_rate(origin, dest)`, `put_rate(origin, dest, rate, day)`,
   `save_rates()`. Never raise — this must not break a trade.
3. **`measure_ship_rates` writes every rate it measures into the store** (keyed by
   the current origin), in addition to returning them.
4. **`decide_main` reads the store first**: for the current origin, use cached
   rates for dests it already has; only call the (slow) live measurement for
   dests that are **missing or stale** (see refresh policy). If the store already
   has all 14 dests fresh, skip live measurement entirely.
5. **`dl2 shiprates`** command: pretty-print the store (matrix of what we know),
   and flag gaps.

**Open questions to RESOLVE EMPIRICALLY (don't assume):**
- **Are rates stable day-to-day, or do they change?** Rates clearly differ by
  *origin* (distance-based). Whether the *same* origin→dest rate changes across
  game days is unverified. → Start with a conservative refresh policy: treat a
  cached rate as fresh only if measured on the current game day; but **also**,
  when you do re-measure, log cached-vs-fresh so we can learn if they're actually
  stable. If they turn out stable, relax the policy to "measure once, ever."
- **Symmetry:** distance is symmetric, so `origin→dest` is *probably* equal to
  `dest→origin`. If verified, a measurement from Toronto also fills the
  Toronto column for every other origin — halving the work. **Verify with a few
  real pairs before exploiting this;** until then store only the direction
  actually measured.
- Note: you can only measure rates *from the city you're currently in* (Shipping
  opens from the current city). So the store fills in as the user travels. That's
  fine — cache accumulates over trips.

**Gotchas:**
- `measure_ship_rates` needs ≥1 unit of stock to sample; `decide_main` already
  buys a 1-unit probe of the cheapest Market drug when inventory is empty.
- A stock-name column bug was fixed this session (the Shipping "Your stock" names
  are in the rightmost drug-name column, matched via `parser._clean_drug` + max
  `x`, not a hardcoded x-band). Don't reintroduce a hardcoded band.

---

## 3. FEATURE B — decay data collection (NOT a hardcoded %)

**Problem:** `decide` ranks plays at the spike's *displayed* price, but spikes
**decay** over the days it takes to fly/ship there and sell. We have only **3**
data points so far (below) — nowhere near enough to fix a coefficient. We need
the tool to **accumulate** projected-vs-realized data so a curve can be fit later.

**The 3 points so far (seed only — days are approximate):**
| Drug | City | Projected sell | Realized sell | ratio | ~days |
|---|---|--:|--:|--:|--:|
| Ecstacy | St Petersburg | 25,844 | 8,838 | 0.34 | ~1–2 (had a shipment delay) |
| Ice | St Petersburg | 17,875 | 10,978 | 0.61 | ~1 |
| Morphine | Toronto | 16,610 | 6,654 | 0.40 | ~1 (2 of 4 loads delayed) |

**Build:**
1. **Log arb projections.** In `decide_main`, after `combined_destinations(...)`,
   the recommended (and ideally each top) city's `arb` list is
   `[(drug, qty, buy, sell, profit), ...]` where `sell` is the *projected* sell
   price. Add these to the `decision` audit event, e.g.
   `arb_targets: [{city, drug, buy, projected_sell}]`. (Right now only
   `held_spikes` — inventory drugs — carry projected sell prices; arb drugs, which
   are what we actually trade, don't.)
2. **Stamp sells (and buys/ships) with the game day.** Add `day=<status day>` to
   `audit.record("sell", ...)` in `sell_all_inventory` (dl2sweep.py ~line 935) and
   ideally to buy/ship. `sell_all_inventory` reads the main window already; get
   the day from the Status box (there's status parsing in `read_status_from_main`
   / `parse.py`). Needed to compute `days_elapsed`.
3. **`dl2 decay` command** (pure audit-log analysis, no screen): read
   `audit_log.jsonl`, and for each `sell` event (city, drug, day, realized price),
   pair it with the most recent PRIOR projection for that (city, drug) — from
   `arb_targets` or `held_spikes` of an earlier `decision` (projected price,
   projection day). Emit rows `{drug, city, projected, realized,
   ratio=realized/projected, days=sell_day-proj_day}` and a summary (count, and
   ratio distribution bucketed by days). If fewer than ~10 clean pairs, print the
   rows and say **"insufficient data to fit a decay model — keep trading."**
4. **Only once there's enough data**, fit `ratio ≈ f(days)` (try
   `ratio = e^(-k·days)`, fit `k`; report fit quality). Put the fitted params in a
   small file (e.g. `decay_model.json`). Do NOT invent numbers before the data
   supports them.
5. **Later (separate step, note it but don't force it):** once a model exists,
   `decide` can show an **honest EV** column = headline × expected-ratio for the
   expected travel days (≈1 normally, more if delays are likely), *alongside* the
   headline (never replacing/dropping it). The user hates dropped/hidden numbers.

**Important:** the decay is **time-based only** (measured — see §1). Do not model
it as a function of quantity sold / market impact.

---

## 4. Testing / conventions

- Validate the pure-analysis parts (rate store I/O, `dl2 decay` pairing) against
  the **existing `~/code/dl2/audit_log.jsonl`** and synthetic fixtures — no screen
  needed.
- To exercise `decide`/`measure_ship_rates` live, ask the user to bring the game
  up (World Cities view, frontmost) and say go; it takes ~3–4 min and drives the
  screen. Watch for it to read all **15 cities × 17 drugs** cleanly.
- Match the codebase style (terse, comment density like the surrounding code).
- The audit log and rate store are the user's real data — append/merge, never
  clobber.
- Everything must degrade gracefully (never raise into a live trade).

## 5. Current game position (context, may be stale by the time you run)
User is mid-game, cash-rich (~$700M+ after a run of arbs). At Toronto with
~8,241 Morphine still in transit here (delayed) and ~4,169 Ecstacy stranded at
St Petersburg (both from split shipments — collect via `dl2 sell` on return).
Latest `dl2 decide` from Toronto led with Moscow (Heroin) and San Francisco
(Cocaine) arbs. None of that matters for these two features — it's just why the
audit log has the entries it has.
