# ocr-combat-detect — detect, read, and SURFACE a combat encounter (no handling)

**Do this AFTER `ocr-banking` (+ ideally `ocr-borrowing`) is merged.** Sequential
(drives the one live game). **Before starting: `cd ~/code/dl2 && git checkout
main && git pull`, then `git checkout -b task/ocr-combat-detect`.** Read
`README.md` and `docs/HANDOFF_main_thread.md` first.

## Scope is DETECT + READ + SURFACE only
**Combat AUTO-HANDLING is explicitly SHELVED** — do NOT click Fight/Run/
Surrender/Bribe. A wrong combat click can lose goods or end the run (death). This
task makes the bot NOTICE an encounter and REPORT it, then hand control back to
the human.

## Context
Encounters pop up as a separate window (like the Info/Places/Shipping popups)
during fly/ship/sell/sweep actions. `dl2model.combat` (merged) has the decoded
resolution: law-enforcement surrender accepted ~73.3% → lose carried drugs, cash
& bank safe; criminal surrender → lose drugs + cash, bank safe; flee prob
`1/min(attackers+1,10)`; airport-security flee → drugs gone + cash capped $50;
death = game over; hospital heal cost `(100-H)**3` from cash. `combat` exposes
`flee_prob`, `airport_bust_loss`, `hospital_cost`, `SURRENDER_ACCEPT_LAW`, etc.
(`ocr-banking` should already have added `health` to the Status parse — depend on
that; if it's missing, add it here.)

## Do
1. **Detect:** make the action loop recognize the encounter window (via
   `bin/dl2windows` bounds/title + a capture) instead of blindly clicking. When
   present, STOP the current automated action.
2. **Read:** encounter type (airport security / police / ATF / SWAT / criminal),
   attacker count, your health, and the visible buttons. Reuse the digit reader
   and the popup-bounds pattern.
3. **Surface:** print a clear situation report annotated with `combat.py`, e.g.
   *"Airport security, 3 attackers, health 60. Surrender accepted ~73% → you lose
   your ~N carried units (cash & bank safe). Flee ≈ 25%/attempt → drugs gone +
   cash capped $50. Full heal would cost $X."* Then hand control to the human —
   do not act. Log the encounter to the audit trail.

## Rules / protocol
- **SHARED-SCREEN PROTOCOL** (warn, bursts, hands-off). NEVER click a combat
  button. If unsure whether a window is an encounter, stop and ask, don't guess.
- Never drop a read; degrade to "stop and tell the human" on any uncertainty.

## Tests
- Unit-test the encounter-window PARSER against a saved sample OCR JSON of an
  encounter (capture one live with `DL2_SAVE_CAPS=<dir>` when one occurs; if you
  can't trigger one, build the fixture from the window layout and note it needs
  live confirmation).
- Unit-test the situation-report text builder (pure `combat` calls) with
  synthetic inputs.
- Live detection verified with the owner.

## Workflow
`python3 -m pytest tests/ -q` green. Commit + push `task/ocr-combat-detect`. **Do
NOT open a PR and do NOT merge** — stop and report to the owner: pushed, summary,
what needs a live encounter to confirm.
