# ocr-banking — read health/No-Scent + bank balance, add deposit/withdraw

**Do this FIRST of the OCR tasks** (it adds the shared `health`/`no_scent` parse
the later tasks rely on). Sequential, not parallel — it edits `parse.py`/
`dl2sweep.py` and drives the ONE live game. **Before starting:
`cd ~/code/dl2 && git checkout main && git pull`, then `git checkout -b
task/ocr-banking`.** Read the repo `README.md` and `docs/HANDOFF_main_thread.md`
first for context and the hard rules.

## Context
The bot reads the game by screen-capture + Vision OCR + a fixed-font digit
reader (`digits.py`); it drives the mouse/keyboard via `bin/dl2input` and finds
popup windows via `bin/dl2windows`. `parse.py` turns OCR JSON → a `status` dict
already yielding `location, cash, day, total_days, rank, bank, debt`. The
decompiled model (`dl2model/`, complete incl. `combat`) is wired into the live
decision math; `dl2model.combat` says **banked cash is risk-free** (never
stolen/confiscated; rank keys off cash+bank), so surplus cash should be banked.

## Scope
1. **Parse two missing Status fields (read-only, unit-testable):**
   - `health` and `no_scent` (No-Scent cans). Add regexes to `parse.py` beside
     the existing cash/bank/debt ones (lines ~135–173). Locate where the game
     shows them (Status box for health; No-Scent likely in the inventory/right
     box or the flight dialog — capture a frame with `DL2_SAVE_CAPS=<dir>
     ./bin/dl2` and inspect). No-Scent count feeds the carry advisory (today it
     defaults to 0 → always 99% detection).
2. **Confirm `bank`/`debt` reach `decide`'s state** and surface net worth
   (`cash+bank`) vs the next `RANK_THRESHOLD` (`dl2model.finance`).
3. **Deposit/withdraw actions (drives the screen — confirm before executing):**
   Find the Bank window (`Places…` button → Finances; the "Places…" popup is a
   separate window clicked by real bounds fraction — reuse the pattern in
   `open_world_cities`/`measure_ship_rates`/the Info-menu code). Enter the amount
   via `bin/dl2input num <digits>` (Unicode typing does NOT work in Wine — use
   the num keycodes), confirm with Return. Ship `dl2 bank deposit <amt>` /
   `dl2 bank withdraw <amt>` (route in `bin/dl2` + `dl2sweep` like `ship`/`sell`).
4. Optional: after a `decide`, offer to execute `combat.recommended_deposit`
   (surplus beyond planned trade spend) — confirm-gated, never automatic.

## Rules / protocol
- **SHARED-SCREEN PROTOCOL:** anything that captures/clicks fights the user.
  Warn, run in short bursts, only when they say "go" (hands-off). Money-moving
  actions (deposit/withdraw) ALWAYS confirm first.
- Never drop/hide a read; keep the digit reader's confident reads.
- Nothing here raises uncaught — a bank hiccup must never break a live trade.

## Tests
- Unit-test the new `parse.py` regexes against saved sample OCR JSON (see
  `samples/bootstrap.json`; add a small fixture with health/No-Scent text).
- Screen-driving (deposit/withdraw) can't be unit-tested — verify live with the
  owner in a hands-off burst; log the action to the audit trail.

## Workflow
Test what you can (`python3 -m pytest tests/ -q` stays green). Commit + push
`task/ocr-banking`. **Do NOT open a PR and do NOT merge to main** — stop and tell
the owner it's pushed and ready to verify, with a summary + what still needs a
live check.
