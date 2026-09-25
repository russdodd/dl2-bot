# mc-planner — state-conditioned Monte-Carlo receding-horizon planner

Build the multi-day planner: from the live OCR state, sample plausible futures
consistent with what's on screen, roll them forward with the (now decode-
validated) 255-market transition function, and recommend the **day-1 action**
that maximizes expected final score. Re-run daily on fresh OCR — that is the
receding horizon; only day 1 is ever executed. **Before starting:**
`cd ~/code/dl2 && git checkout main && git pull`, then
`git checkout -b task/mc-planner`. Read `docs/decode-reference.md` (ground truth)
and `docs/HANDOFF_main_thread.md` first.

## Why a new component (the core insight)
`dl2model/montecarlo.evaluate` already rolls a decision over N seeded `Game`s and
summarizes EV / p10/p50/p90 / p_loss — but `_new_game` seeds a **fresh random
world** and only overrides *player* attributes (`city/cash/bank/debt/rank/
no_scent/inventory`). The market **prices/quantities come from the seed, not from
what the player observes.** For a live planner that is wrong: the day-0 world
must MATCH the screen. The missing capability is **state-conditioning** — build
sampled worlds whose day-0 markets equal the observed prices/quantities, with
only the *hidden* state sampled. Everything downstream reuses existing code.

`strategy.plan` already does a beam search over a horizon, but against a **frozen
price snapshot** (decode-reference §3 shows prices actually evolve). The planner
replaces that frozen assumption with sampled forward evolution via `Game`.

## Phases (land A+B first — pure model, no screen; then C — live wiring)

### Phase A — state-conditioned world sampler  (`dl2model/observe.py`, new; pure)
`sample_world(state, seed) -> Game`: a `Game(seed)` whose day-0 `world[city][drug]`
is overwritten to match observation, hidden state sampled:
- **Price/quantity:** set `market.price` and `market.quantity` to the observed
  values for every market present in `state["prices"]` / `state["stock"]`. For
  markets not observed (remote cities the OCR didn't read), keep the seeded
  values (they're a prior) — document this.
- **Hidden target (the hard part):** targets are unobservable. Sample per decode
  §2–§3: normal target range is `[M//2, M//2 + M - 1]` (`M = normal_mean`). Rule:
  if the observed price is within/near that range, sample the target near the
  observed price (drift keeps price close to target); if the observed price is a
  **spike far above ~1.5M** (or far below ~M/5), treat it as an **active event**
  (decode §4b: events overwrite *price* but NOT target), so set the target to a
  fresh normal draw and set `market.event` so the next `step_day` decays price
  toward normal. State the exact thresholds you pick; perfection isn't required —
  daily re-planning corrects drift.
- **Active rumors:** for each rumor in `state["rumors"]`, inject the scheduled
  event into `game._scheduled[(city, drug)] = +1/-1` so the *next* `step_day`
  honors it with the decoded 70/20/10 realized reliability (decode §4a). This is
  the main exploitable signal — get it right.
- Player attributes from `state` as `_new_game` already does.

Tests (pure, fake/seeded RNG): observed prices/quantities are reproduced exactly
on day 0; a spike price is flagged event-active with a normal-range target; an
active rumor lands in `_scheduled` and materializes at the decoded rate over many
seeds; unobserved markets fall back to seeded priors.

### Phase B — horizon rollout + day-1 ranking  (`dl2model/planner.py`, new; pure)
`plan_mc(state, horizon=H, n=N, base_seed=0) -> dict`:
1. **Candidate day-1 actions:** enumerate the same candidates the live sweep
   considers (buy-here/fly-to-dest/ship legs). Reuse `strategy._expand` /
   `strategy._best_load` for day-0 hop generation, or the sweep's candidate set —
   don't invent a new search.
2. **Rollout:** for each candidate, for `i in range(N)`: `g = observe.sample_world
   (state, base_seed+i)`; apply the candidate's day-1 action (`buy/fly/ship/sell`
   via the `Game` methods); step to the horizon; **force-liquidate** held goods on
   the final day (mirror `strategy._liquidate`); record `final_score`
   (`finance.final_score`, = cash+bank−debt). The traded market's next-day
   availability is already the decoded 2/3 Bernoulli inside `step_day`
   (`stock.is_listed`, decode §9) — so "the drug I meant to sell isn't listed on
   arrival" shows up naturally as downside mass.
3. **Rank:** summarize each candidate's outcomes with `montecarlo._summarize`
   (EV, p10/p50/p90, p_loss); return candidates ranked by EV, each with its
   distribution and the projected multi-day action path for context.
- **Continuation policy** for days 2..H inside each rollout: keep it cheap (greedy
  best-hop per day, or a width-1/2 beam) — it runs inside the N×candidate loop.
  Document the choice. Suggested budget: **H≈3–4, N≈500–2000.**

Tests (synthetic states, seeded/deterministic RNG): on a hand-constructed world
the ranked #1 day-1 action matches a hand-computed optimum; a clear spike+rumor
in a reachable city makes the planner prefer chasing it; the 2/3 listing risk and
spike-decay show up as nonzero p_loss / p10<EV; horizon=1 degenerates to immediate
liquidation (matches `strategy` H≤1 path).

### Phase C — live wiring  (`dl2sweep.py`; drives no screen itself)
Add `dl2 plan --horizon N` (route in `bin/dl2`; default N off → current behavior
unchanged). Build `state` from the same OCR parse `decide`/`plan` already assemble
(reuse it — don't re-OCR), call `planner.plan_mc`, and print the top day-1 action
as an **ADDED view** beneath the observed single-day sweep: the recommended day-1
move + its EV / p10-p50-p90 / p_loss, and a one-line "re-run tomorrow after you
act" note (receding horizon). Degrade silently if the model/parse is incomplete.

## Hard rules
- **Never drop or replace the observed single-day sweep / any observed price.**
  The MC recommendation is an added column/section, exactly like the EV column
  and the combat/banking advisories. Advisory — it does not auto-act.
- Phase C reuses the existing capture/parse; it introduces **no new screen-driving
  and no money/combat actions.** (Executing the recommended trade still goes
  through the existing confirm-first buy/ship/sell paths.)
- Target-sampling and any priors are documented assumptions, never silent magic.

## Workflow
`/Users/russelldodd/anaconda3/bin/python3 -m pytest tests/ -q` green (NOT
`/usr/bin/python3` — no numpy/PIL). Land **Phase A+B first** (pure, fully unit-
tested, no game needed) as its own commit/push and stop for the owner's verify;
then Phase C. Push `task/mc-planner`. **Do NOT open a PR and do NOT merge** —
report to the owner: pushed, a summary, and (for C) what needs a live-screen
check. Verify-first: the owner merges.
