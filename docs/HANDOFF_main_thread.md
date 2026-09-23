# DL2 exact-model rebuild — main-thread handoff

You are the **coordinator** of a rebuild of the Drug Lord 2.2 trade assistant.
The user reverse-engineered the DL2.2 executable (via ChatGPT) and we turned the
exact game formulas into a tested Python package (`dl2model/`) and wired them
into the live OCR bot. You merge branches (verify-first), write handoff specs for
farmed-out tasks, and do the live-screen validation with the user hands-off.

## Repo / where
- Code: `~/code/dl2`. Remote: `git@github.com:russdodd/dl2-bot.git`.
- Live bot entry: `~/code/dl2/bin/dl2` (`plan`, `decide`, `buy`, `ship`, `sell`,
  `audit`, `decay`, `shiprates`). `bin/dl2` auto-picks a Python with numpy+PIL.
- Parallel task specs: `docs/tasks/*.md`. Detailed session history: the user's
  memory file `dl2-assistant` (auto-loaded).

## Architecture
- **`dl2model/`** — exact, screen-free reimplementation, fully tested (~139
  tests). Modules: `constants` (all data tables + `trunc_div`, `normal_mean`),
  `prng` (MSVC rand), `prices` (momentum + spike decay), `stock`, `shipping`,
  `risk`, `rumors`, `finance`, `combat`, `simulator` (seedable day-stepping
  engine), `strategy` (multi-day search), `montecarlo` (EV/variance).
- **Live bot** — `dl2read.swift`/`dl2input.swift`/`dl2windows.swift` (capture,
  click, window bounds), `digits.py` (fixed-font digit reader — trust its
  confident reads), `parse.py` (OCR JSON → state; Status box → cash/bank/debt/
  rank/day/location), `optimizer.py` + `dl2sweep.py` (decision math), `decay.py`,
  `shiprates.py`, `audit.py`.
- **Wiring** — the exact model feeds the live decision math via `decay.py`
  (`expected_sell_price`) and `dl2sweep.py` (EV column, shipping cross-check,
  remote-stock estimate, channel + banking advisories).

## Key decoded facts (the corrections that matter)
- **Spike decay is LINEAR in dollars, not exponential %:** price drifts toward a
  hidden target by `rand()%gap`/day, `rand()` maxes at 32767 → ≤ $32,767/day,
  expected $16,383.50. Default the unknown target to `M = normal_mean`. (Live-
  checked: $60k Cocaine spike, 1 day → EV $43,616.)
- **Stock** anti-correlates with price, scaled by rank capacity C.
- **Shipping** = `distance·rate·speed·units//1000`; delivery all-or-nothing
  (0.50/0.75/0.90/0.99), goods+fee lost on failure; 3-day pickup grace.
- **Rumors** = strongest signal: 70.2% true / 10.2% opposite / 19.6% none.
- **Bank is risk-free** (no interest, never stolen/confiscated; rank uses
  cash+bank) → BANK DOMINANCE: bank surplus. **Combat:** law-enforcement
  surrender ~73.3% (lose carried drugs, cash+bank safe), criminal surrender
  (lose drugs+cash, bank safe), flee `1/min(att+1,10)`, airport flee → drugs
  gone + cash capped $50, death = game over. Hospital heal `(100-H)**3` from cash.
- **`final_score = cash + bank − debt`; unsold inventory is worthless** →
  liquidate on the last day.

## Hard rules (owner, non-negotiable)
- **Never drop or hide an observed price.** Exact-model EV is an ADDED column,
  never a replacement for the headline/observed number.
- Trust the digit reader's confident reads even when extreme (spikes are real).
- **SHARED-SCREEN PROTOCOL:** anything that captures/clicks the game must be run
  in short bursts, hands-off, only after the user brings the game up and says
  "go". Warn first. **Confirm before any money-moving or combat action.**
- **Verify-first git:** push task branches; the user verifies before you merge.
  Runtime data (`audit_log.jsonl`, `ship_rates.json`, `decay_model.json`) is
  gitignored — don't re-track it.

## Status (as of this handoff)
- **Merged to `main`:** all of `dl2model` (p01–p10 + `combat`) and the
  live-bot wiring (`wire-live-bot`). ~139 tests green. Live `dl2 decide`
  validated end-to-end (sweep 15/15 cities, EV column reproduces by hand).
- **PENDING your verify + merge:** branch **`task/wire-combat`** (pushed) — wires
  `combat` into the live bot: real airport bust-loss in the channel advisory +
  a banking advisory (recommend depositing surplus). 139 tests green on it.
  **Merge this before the OCR tasks start** (they `git pull` main).
- **Not started — OCR tasks (SEQUENTIAL, live-screen, specs in `docs/tasks/`):**
  1. `ocr-banking` — parse health/No-Scent, confirm bank/debt flow, deposit/
     withdraw actions. (Do first; adds shared health/No-Scent parse.)
  2. `ocr-borrowing` — read loan-shark state, wire loan advice, borrow/repay.
  3. `ocr-combat-detect` — detect/read/SURFACE encounters (no handling).
  Each: `git checkout main && git pull` first, own branch, verify-first.
- **SHELVED:** combat auto-handling (fight/run/surrender/bribe automation).
- **Optional/later:** `p11-tui` (a clickless terminal game on `simulator`).

## Your next action
Verify `task/wire-combat` with the user and merge it, then hand out the OCR
prompts (banking → borrowing → combat-detect, sequential) and do the live-screen
validation for each in a hands-off burst. Keep the memory file updated as state
changes.
