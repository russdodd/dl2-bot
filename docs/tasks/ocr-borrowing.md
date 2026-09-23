# ocr-borrowing — read loan-shark state + add borrow/repay + wire loan advice

**Do this AFTER `ocr-banking` is merged.** Sequential (edits `parse.py`/
`dl2sweep.py`, drives the one live game). **Before starting:
`cd ~/code/dl2 && git checkout main && git pull`, then `git checkout -b
task/ocr-borrowing`.** Read `README.md` and `docs/HANDOFF_main_thread.md` first.

## Context
`parse.py` already reads your `debt` amount from the Status box. Missing: which
loan shark, its daily rate, and days-until-due. `dl2model.finance` has the shark
table (`LOAN_SHARKS`: name → max-multiplier, daily-rate%, due-days),
`debt_after_days`, `early_payment_total`, `max_loan`; `dl2model.strategy` has
`evaluate_loan_to_promote`. The game's loan sharks: Odd Lenny 60%/1d, One-eyed
Wilbur 50%/3d, Laughing Max 40%/4d, Strange ear Leonard 30%/5d, Buddles 15%/6d;
you start owing $1,000 to Buddles.

## Scope
1. **Read the loan-shark window (read-only):** open it (`Places…` → Finances →
   loan shark list — reuse the popup-window-by-bounds navigation from
   `measure_ship_rates`/the Info-menu code; `bin/dl2windows` enumerates popups).
   Parse: your current lender, outstanding principal, daily rate, days remaining
   /due; and each shark's offered terms if shown. Feed `state["loans"]` (and a
   `loan_rate`, `loan_due_day`). Cross-check the rate against
   `finance.LOAN_SHARKS` (they should match; log a discrepancy, don't override).
2. **Wire loan advice into `decide` (compute-only):**
   - repay-by-day warning from `finance.debt_after_days` + due day;
   - whether a **loan-to-promote** pays (borrow to vault the next
     `RANK_THRESHOLD`, keyed off cash+bank, for +5 days & higher capacity vs the
     compounding interest) via `strategy.evaluate_loan_to_promote`. Advisory
     only — surface it, don't auto-borrow.
3. **Borrow/repay actions (drives screen — confirm first):** drive the shark
   window to borrow a given amount from a named shark, and to repay. Ship
   `dl2 loan borrow <shark> <amt>` / `dl2 loan repay <amt>` (route like
   `bank`/`ship`). Amount entry via `bin/dl2input num`.

## Rules / protocol
- **SHARED-SCREEN PROTOCOL** (warn, bursts, hands-off, say-go). Borrow/repay =
  taking on / clearing compounding debt → ALWAYS confirm before executing.
- Never drop a read; degrade silently on a store/nav hiccup (never break a trade).

## Tests
- Unit-test the loan-window parser against a saved sample OCR JSON fixture.
- Unit-test the advice (repay-by-day, loan-to-promote) with synthetic state —
  these are pure `finance`/`strategy` calls, no screen.
- Actions verified live with the owner (audit-logged).

## Workflow
`python3 -m pytest tests/ -q` green. Commit + push `task/ocr-borrowing`. **Do NOT
open a PR and do NOT merge** — stop and report to the owner: pushed, summary,
what needs a live check.
