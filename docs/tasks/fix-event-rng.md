# fix-event-rng — correct the two decoded event-probability bugs + pin every formula

**Standalone, pure-compute, no live screen.** Fixes two probability/RNG bugs in
`dl2model/simulator.py` found by reconciling the 2026-09-24 decompile pass
against the model, and adds regression tests that pin the decoded formulas to
their executable addresses so the model can never silently drift again.
**Before starting:** `cd ~/code/dl2 && git checkout main && git pull`, then
`git checkout -b task/fix-event-rng`. Read `docs/decode-reference.md` first —
it is the source of truth; every fix and test below cites a section there.

## Why this matters / scope boundary
These are the **only two discrepancies that touch the probability distribution a
Monte-Carlo planner samples** (decode-reference §8). Everything else that
differs (6-slot record count, listing-draw scope, intra-day RNG order) is
**RNG-stream-sync detail the planner does not need** — it is explicitly OUT OF
SCOPE here. Do **not** try to make the simulator byte-for-byte seed-reproducible;
target the marginal probabilities only.

## Fix 1 — rumor scheduler must use `rand()%10`, not `rand()%100`
`decode-reference §4a` (rumor_step `0x4040B6–0x4040E6`). In
`simulator._roll_event`, the model currently does:
```python
roll = self.rng.rand_mod(100)
if roll < 70:      ... predicted
elif roll < 90:    ... _ordinary_event_roll   # the "nothing" branch
else:              ... opposite
```
Change to the executable's exact form:
```python
roll = self.rng.rand_mod(10)
if roll <= 6:      ... predicted          # 0..6  -> 22939/32768 = 70.0043%
elif roll <= 8:    ... _ordinary_event_roll  # 7..8 -> 6553/32768 = 19.9982%
else:              ... opposite           # 9     -> 3276/32768 =  9.9976%
```
Keep the architecture as-is: the model applies the 70/20/10 at consumption time
(day D+1) and the "nothing" branch calls `_ordinary_event_roll` on that same
day. That is faithful to the realized outcome — combined with Fix 2 it exactly
reproduces the collapsed **70.2044 / 19.5978 / 10.1977%** realized next-day
distribution (decode-reference §4a). The displayed rumor direction stays an
independent 50/50 in `_make_rumor` (unchanged). Do NOT move the draw into
`rumor_step` — that is seed-sync only and out of scope.

## Fix 2 — ordinary spontaneous event: two draws (`%50` then `%2`), not one `%100`
`decode-reference §4c` (`0x4053F0–0x405416`). In
`simulator._ordinary_event_roll`, replace:
```python
r = self.rng.rand_mod(100)
if r == 0:   self._apply_event(market, +1, M)
elif r == 1: self._apply_event(market, -1, M)
```
with the executable's exact form:
```python
if self.rng.rand_mod(50) == 0:                     # 656/32768 = 2.0020%
    direction = +1 if self.rng.rand_mod(2) == 0 else -1   # 1.0010% each
    self._apply_event(market, direction, M)
```
This corrects both the total probability (~2.00%) and the two-draw structure
(direction is a *second* draw only when the event fires).

## Pin the formulas (regression tests) — `tests/test_decode_pin.py` (new)
Add a test module that asserts each decoded formula against a **deterministic
fake RNG** (feed a scripted sequence of `rand()` values, MSVC range 0..32767),
so any future edit that diverges from the executable fails. Each test docstring
cites the decode-reference section + address. Cover:
- §2  `constants.normal_mean` == `BASE_PRICE*CITY_MULT//100` (spot-check a few).
- §3  `prices.step_price`: drift by `signed_rand(old_gap)`, and reselect **iff
      `abs(old_gap) < new_price//10`** (test both sides of the strict `<`, and
      that equality does NOT reselect); `new_target == M//2 + rand%M`.
- §4b `prices.event_shortage_price`/`event_glut_price`,
      `stock.event_shortage_quantity`/`event_glut_quantity` exact formulas;
      assert `_apply_event` leaves `market.target` UNCHANGED (no target write).
- §4a Fix 1: with scripted rolls 6/7/9, `_roll_event` yields
      predicted/ordinary-roll/opposite respectively.
- §4c Fix 2: `_ordinary_event_roll` consumes `%50` then (only on fire) `%2`;
      assert draw counts for both the fire and no-fire cases.
- §5  `stock.step_quantity` == `max(trunc_div(q + (C - trunc_div((P-H)*C, M)), 2),
      0)` with `H=M//2`, incl. a negative-intermediate case proving `trunc_div`
      (toward zero) not `//` (floor); assert it consumes **no** RNG.
- §6  `_generate_rumors`/`_make_rumor`: `rand%3` gates, drug `rand%17`, direction
      `rand%2`; scheduled event queued for exactly the next day (lead-time 1).

## Rules
- Pure model change — do not touch the live-bot/OCR files, do not drive the game.
- Do not "fix" the seed-sync gaps (§8 rows 3/4/5) — out of scope.

## Workflow
`/Users/russelldodd/anaconda3/bin/python3 -m pytest tests/ -q` all green (note
`/usr/bin/python3` lacks numpy/PIL — use the anaconda python, the same one
`bin/dl2` picks). Commit + push `task/fix-event-rng`. **Do NOT open a PR and do
NOT merge** — stop and report to the owner: pushed, a summary, and the before/
after probabilities so they can sanity-check. Verify-first: the owner merges.
