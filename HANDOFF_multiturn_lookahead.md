# Handoff: two-turn (reposition-then-arb) lookahead for the DL2 assistant

## ⚡ Prompt to paste into a fresh Claude session

> You're working on my Drug Lord 2 trade-assistant CLI in `~/code/dl2/`. Read
> `~/code/dl2/HANDOFF_multiturn_lookahead.md` in full, then build a **two-turn
> ("reposition-then-arb") lookahead** for `dl2 decide`. Today the tool only sees
> ONE hop from the city I'm in, so it misses plays where the huge profit is a
> flight away: e.g. a drug SPIKING in city B, whose cheap stock is in city C — I
> have to fly C, buy, then fly B to sell, which is two turns from where I stand.
> The potential (often 10–50x a one-hop play) dwarfs the safe options, and spikes
> persist ~3 days so it's favorable-EV. Build a spike-gated 2-hop search that
> surfaces these ALONGSIDE (never replacing) the 1-hop plays, honestly labelled
> as higher-variance and conditional on the buy-city market cooperating.
>
> Don't break the 1-hop trade math or the screen-driving paths. The screen-driving
> commands (decide/buy/ship/sell) move my real mouse — run those only in short
> bursts when I'm hands-off and say "go". Everything else (the price matrix, the
> planner, the audit log, the rate store) is pure computation/file I/O and can be
> built and tested freely. Ask me to run a live `dl2 decide` when you need fresh
> data. Read the prior handoffs too:
> `HANDOFF_decide_plan_and_positions.md` and `HANDOFF_decay_and_shiprates.md`.

---

## 1. The problem — one-hop blindness misses the biggest plays

`dl2 decide` ranks, from the CURRENT city, "buy in this Market → fly to ONE city
→ sell" (see `combined_destinations` / `decide_main` in `dl2sweep.py`). It never
considers first REPOSITIONING to another city to set up a better arb. So when a
drug spikes in city B but its cheap stock is in city C (not where you are), the
play is invisible — it's two turns away from your current position.

**Concrete case that triggered this (real numbers, from `/tmp/dl2_state.json`):**
I'm in **Miami**, which is SELLING **Heroin at $54,116 — a 7× spike** (cross-city
median $7,936). Heroin's cheap elsewhere (world prices: Detroit $4,581, San
Francisco $4,890, **Austin $8,588** — I could see Austin had ~11 buyable units).
The play: **fly to Austin, buy ~10–11 Heroin (~$86–94k), fly back to Miami, sell
at $54k → ~$450–500k profit.** That obliterates the best one-hop option the tool
found (~$8.8k). But from Miami the tool can't see it, because Miami's own Market
doesn't sell cheap Heroin — Austin does.

Note the tool ALREADY nails this the moment you're standing in the buy city:
run `dl2 decide` from Austin and "buy Heroin here → sell Miami $54k" shows up as a
one-hop arb. The 2-hop feature just automates the *"which city should I reposition
to?"* decision so I don't have to eyeball the matrix.

## 2. When two-turn pays (and when it doesn't)

- A **1-hop** play bets on ONE unknown — will the destination Market actually
  trade the drug on arrival — using TODAY's exact prices. Low risk.
- A **2-hop** play compounds three risks: (a) does the buy-city really SELL the
  drug near its world price when you land (we only ever KNOW the current city's
  buyable Market; other cities' prices are reference, not a purchase guarantee),
  (b) does the spike city STILL pay the spike price ~2 days later, (c) an extra
  travel day.
- Risk (b) is small **exactly when the sell leg is a strong, persistent spike**
  (spikes last ~3 days per the game facts). So **2-hop is worth surfacing only
  for spike sell-legs** (e.g. ≥3× cross-city median). For ordinary margins, a
  daily reroll can erase the edge before you finish the round trip — don't
  surface those as 2-hop plays; the 1-hop tool already handles ordinary arb.

## 3. What to build

1. **A 2-hop search in `dl2 decide` (additive):** for each candidate reposition
   city C (that you'd fly to), and each spike city B, estimate the best
   buy-in-C / sell-in-B arb, net of one extra travel day, capped by cash, carry
   (=inventory size) and stock. Rank these against the 1-hop plays and print a
   separate **"(3) TWO-TURN PLAYS (higher variance)"** section. GATE to sell-legs
   ≥ SPIKE×median so only persistent-spike chases appear.
   - Reuse the existing per-unit economics: carried units cost `buy` (no ship),
     shipped units `buy+rate`; profit uses headline sell, `_arb_ok` (ARB_SAFETY)
     is the keep/drop filter. See `combined_destinations` — factor its per-city
     arb math into a helper you can call for an arbitrary (buy-city market, sell-
     city prices, rate) so the 2-hop search reuses it.
   - **The hard limit:** we don't know city C's buyable Market or its stock until
     we're there. So a 2-hop buy leg is a BET on C's world price being buyable.
     Present every 2-hop row explicitly conditional: e.g. "IF Austin sells Heroin
     near $8,588: buy 10, fly Miami, sell $54,116 ≈ $455k". Never present it as
     certain. This is the whole reason it's a separate, labelled section.
2. **Honest EV once the decay model exists.** A 2-turn play eats ~2 days of spike
   decay. `decay.py` is already collecting projected-vs-realized ratios by days
   elapsed (`dl2 decay`); once it fits `ratio≈e^(-k·days)` into `decay_model.json`,
   show `EV = spike_price × ratio(≈2 days)` ALONGSIDE the headline (never drop the
   headline — the user hates hidden numbers). Until then, show the raw spike and
   say the decay is unmodelled.
3. **Save the Market to `/tmp/dl2_state.json`.** `decide` currently saves prices +
   rates but NOT the buyable Market (buy prices + per-drug stock). Save it, so the
   planner (and affordability checks) can reason offline and so a future `dl2
   positions`/2-hop view doesn't need a fresh sweep. (This bit me this session — I
   couldn't confirm Austin's buy price/stock from the saved state.)

Keep the 1-hop sections exactly as they are; the 2-hop section is additive.

## 4. Already fixed / built this session (do NOT redo)

- **Cash parse bug (critical).** The Status "Cash:" value OCRs its thousands
  separator inconsistently as `,` OR `.` (e.g. `271,339` vs `271.291`, even mixed
  `2,722.356,320`). The old regex captured `[\d,]{2,}` and stopped at a period →
  read `$271` instead of `$271,291`, which made `decide`'s cash cap wildly wrong.
  Fixed in `parse.py`: money fields now match `[\d.,]` and strip BOTH separators
  (amounts are whole dollars, so `.` is never a decimal). Bank/Debt too.
- **Inventory-size parse.** The stash box name changes as you level up ("Your
  pants pocket (1/10)" → "Your island paradise (…)" → …). `parse.py` used to
  hardcode "paradise" and read capacity `None`; now it matches the parenthesized
  `(held/capacity)` regardless of name. Capacity = the plane-carry cap.
- **Status day parse** reads via geometry (handles the value box sitting a few px
  above the "Day:" label). Games can be 30 OR 55 days — don't hardcode 55.
- **Cash-constrained arb** (`combined_destinations(..., cash=)`), **free
  plane-carry** (`carry=`, capped by inventory size, `DL2_CARRY` default 500),
  **`dl2 buy Drug:max`**, **3-way near-equal-value ship split**, **`dl2 newgame`**
  + segmenting (`audit.latest_segment()`), **cash-aware decide output**. See
  `HANDOFF_decide_plan_and_positions.md` §5/§5a.

## 5. Current game state (fresh game — the prior one ended: caught by police)

- **At Miami, Day 3/30**, cash **~$271,291**, inventory **1 Pot**, **inventory
  size 10** ("pants pocket" — so free plane-carry is 10; grows as you level up).
  A `newgame` marker is recorded, so decay/positions start clean.
- **Live spike worth chasing:** Heroin SELLS $54,116 at Miami (7× median). Cheap
  buyable Heroin is a flight away (Austin ~$8,588, ~11 units; Detroit/SF cheaper
  on the world board but their stock is unconfirmed). At carry 10 the play is
  ~10 Heroin ≈ **$450k** — capacity-limited, not cash-limited, this early.
  **Immediate way to capture it with today's tool:** fly to the buy-city and run
  `dl2 decide` there (it becomes a one-hop arb). The 2-hop feature automates
  spotting it from anywhere.
- **Pot** is buyable in Miami ~$48 and spikes to ~$1,516 in Moscow (~32×) — the
  best *one-hop* play, but far smaller than the Heroin two-turn play.
- **Rate store** (`ship_rates.json`) has Miami filled (14/14) this game, plus
  Toronto/Moscow from the prior game. Rates are distance-based so they should
  carry across games; `decide` re-measures when the current origin's day differs
  and logs `rate_check` (cached vs fresh) to confirm cross-game stability.

## 6. Files / functions

| File | Relevant |
|---|---|
| `dl2sweep.py` | `combined_destinations(current, matrix, market, inv, ship_rates, safety, cash, carry)` — the 1-hop arb; factor its per-city math into a reusable helper for the 2-hop search. `decide_main()` — writes `/tmp/dl2_state.json` (add `market`), prints the sections, logs the `decision`. `SPIKE=3.0` and the median calc live in `decide_main`. `DEFAULT_CARRY`, `ARB_SAFETY`, `_arb_ok`. |
| `parse.py` | `CANON_CITIES`, `CITY_SHORT`; `status` (cash/day/location); capacity via the `(held/capacity)` match. |
| `decay.py` | `pairs()`, `fit_exp()` — the decay model that will price 2-turn EV. |
| `audit.py` | `latest_segment()` — scope everything to the current game. |
| `bin/dl2` | dispatcher. |

## 7. Conventions

- Match the codebase style (terse, comment density like the surrounding code).
- Never drop or hide a number; surface uncertainty loudly (the tool's hard rule).
- Everything degrades gracefully — never raise into a live trade.
- Build/test pure logic against synthetic fixtures + the saved matrix; only
  exercise decide/buy/ship/sell live, in short bursts, on the user's "go".
- Shared-screen protocol + game mechanics: see `HANDOFF_decay_and_shiprates.md` §1.
