# Drug Lord 2 assistant

Reads the live Drug Lord 2 window (macOS ScreenCaptureKit + Vision OCR — no typing,
no memory hacking) and prints the profit-maximizing trade: which drug to buy, and
which city to fly to and sell it in. Because the game exposes world prices, the
buy-here / sell-there / fly-to decision is solved exactly.

## Use it
Have Drug Lord 2 open (it works whether you're on the **main window** or the **world
window** — either "World Prices" or "World Cities" view), take your hands off the
mouse, then:
```
~/code/dl2/bin/dl2 plan
```
What it does, in order:
1. Sweeps every city → full price matrix; reads your Location/Cash/Day from the Status box.
2. Prints the pre-shipping ("gross") best move.
3. Opens Shipping and measures the flat per-unit shipping rate to **every** city in one
   session (needs a few units of any drug in stock as the sample).
4. Prints the **after-shipping** best city and exactly which drugs to buy — dropping any
   whose spread can't cover shipping.
5. Prints a **Contenders** table: gross vs net for the top cities, each city's shipping
   rate, drug count, and `top-drug%` (how concentrated the profit is = a risk gauge).

Rules it encodes: buying is restricted to what "The Market" sells (capped by stock);
selling is unlimited; no transaction cost to buy/sell in the same city; carry capacity
isn't a limit (you buy → ship → buy more); shipping is a flat per-unit cost that varies
by city. Add `--no-ship` to skip the shipping step. Leaves World Cities closed when done.

Quick one-shot reading of just the current view (no sweep):
```
~/code/dl2/bin/dl2            # one reading
~/code/dl2/bin/dl2 --watch    # refreshes as prices change
```

## Requirements (one-time)
Terminal needs two macOS permissions (System Settings → Privacy & Security):
- **Screen Recording** — to capture the window.
- **Accessibility** — to drive the drug/city list during a `plan` sweep.

## How it works / pieces
- `bin/dl2read`  — captures a window (any Space, even occluded) + Vision OCR → JSON.
  `--cursor` shows the pointer (used for click calibration); `--save x.png` dumps the image.
- `bin/dl2input` — synthetic click / dclick / move / key / raise (needs Accessibility).
- `dl2 ship <City> [Drug ...] [--split N | --batch SIZE]` — ship held inventory to a city,
  keeping only drugs that still profit after a 15% price drop (`DL2_ARB_SAFETY`). Shipments are
  delayed independently, so `--split N` spreads your biggest position across N separate shipments
  (a delay then strands only a fraction); small drugs stay in one shipment. Needs a prior `dl2 decide`.
- `audit.py`     — append-only trade log (`~/code/dl2/audit_log.jsonl`). Every `dl2 decide`
  records its projection (state, ranked candidates, recommendation, spikes, and the buy-here/
  sell-there `arb_targets`); every buy/ship/sell records the outcome (drug, qty, price, game day).
  Review it with `dl2 audit` (timeline) or `dl2 audit review` (realized price as a % of what was
  projected — e.g. a spike that decayed to 81% overnight), and `dl2 audit note "..."` to annotate.
  Purpose: see whether spikes held and destinations traded your drugs, then tune the arb safety
  margin / assumptions accordingly.
- `shiprates.py` — persistent origin→dest courier-rate store (`~/code/dl2/ship_rates.json`), so
  `dl2 decide` stops re-measuring rates (a slow screen session) on every run. `dl2 decide` reads
  cached rates for the current city and only measures live when some destination is missing or
  stale; a rate is fresh on the same game day, or (day unknown) within `DL2_RATE_MAXAGE_HOURS`
  (default 18). `dl2 shiprates` prints the known matrix and flags gaps. Whether the same
  origin→dest rate changes day-to-day is unverified, so a re-measure logs a `rate_check` audit
  event (cached vs fresh) to learn it — relax the freshness window once they're shown stable.
- `decay.py`     — `dl2 decay` (pure log analysis): pairs each realized `sell` with the most
  recent prior projection for that (city, drug) and reports `realized / projected` vs game-days
  elapsed. Decay is time-based only (measured — selling more doesn't move the price). It does
  **not** hardcode a coefficient; it accumulates pairs and, once there are ≥10 with a known day
  gap, fits `ratio ≈ e^(-k·days)` into `decay_model.json`. Until then it prints the rows and says
  "insufficient data — keep trading."
- `digits.py`    — exact digit reader. The game draws numbers in a fixed bitmap font,
  so instead of trusting Vision's guess (which can *systematically* misread a price —
  same pixels, same wrong answer, every time) it crops each number cell, segments the
  glyphs, and matches them against learned 0-9 templates. Vision is used only to *locate*
  the numbers; the digits themselves are read by template match. Templates are bootstrapped
  once from Vision's confident reads (`python3 digits.py bootstrap samples/bootstrap.json`)
  and cached in `digit_templates.npz`. A read is only accepted when the winning template
  beats the runner-up by a wide margin — an ambiguous glyph returns "unknown" and keeps
  Vision's value rather than guessing. `parse.py` sees the corrected numbers transparently.
- `parse.py`     — OCR JSON → structured state; view-aware (World Prices vs World Cities).
- `optimizer.py` — state → optimal buy/sell/fly plan (1-hop exact; unlimited-cash mode).
- `dl2sweep.py`  — the `plan` command: self-calibrates click coords, walks the list, optimizes.
- `dl2.py`       — the quick one-shot reading.

## Notes
- Wine renders to an off-screen surface, so capture pixels ≠ screen coordinates; the
  sweep self-calibrates the mapping each run by locating the cursor via pixel-diff.
- Direct game-memory reading was ruled out (SIP + CrossOver hardened runtime block it).
- The optimizer looks one hop ahead (today's prices are exact; tomorrow's are random).
