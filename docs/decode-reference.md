# DL2.2 decode reference — ground-truth mechanics from the decompiled executable

**Source:** decompile pass over the DL2.2 binary (ChatGPT reading the
disassembly), 2026-09-24. Addresses are from that pass. This file is the
**source of truth**: `dl2model/` must reproduce these, and the regression tests
should cite these addresses so the model can never silently drift from the
executable again.

**Verdict up front:** the decode *validates* the model's core transition
mechanics almost letter-for-letter. Two small probability/RNG bugs were found
(§7 Corrections). Everything else that differs is **RNG-stream sync detail**,
which the Monte-Carlo planner does not need (§8). The 6-market-slot question is
**resolved** (§9): the player only ever trades slot 0, so the 255-market model
is the exact tradeable world and **the multi-day build is unblocked.**

---

## 1. RNG primitive — `0x4117F0`
Standard 32-bit MSVC LCG, 15-bit output:
```python
state = (214013 * state + 2531011) & 0xffffffff
rand  = (state >> 16) & 0x7fff          # 0 .. 32767
```
`RAND_RANGE = 32768`. All `rand()%n` carry MSVC modulo bias; expectations must
use the exact biased formula (`prices.expected_rand_mod`), not `n/2`.

## 2. Market scale `M` — `0x40534F–0x405368`  ✓ matches `constants.normal_mean`
```python
M = base_price[drug] * city_multiplier[city] // 100      # trunc, all positive
```
`M` is the executable's exact central market price — **not a fitted value.**
Hidden targets wander around it (§3).

## 3. Price drift + hidden-target reselection — `0x405343–0x4053B4`  ✓ matches `prices.step_price`
Per market, per day, in this order:
```python
old_gap   = target - price                     # OLD target, OLD price   (0x405343)
price    += signed_randmod(old_gap)            # signed drift            (0x405370)
if abs(old_gap) < price // 10:                 # OLD gap vs NEW price, strict <, /10 (0x405399)
    target = M // 2 + randmod(M)               # reselect: spans floor(M/2)..floor(3M/2)-1 (0x4053B1)
```
Equality does **not** reselect. `signed_randmod` (`0x401280`):
```python
def signed_randmod(n):
    if n < 0: return -(rand() % -n)
    if n > 0: return  rand() %  n
    return 0
```
**Spike duration is emergent, not a constant:** a fresh high target takes several
drift-steps to close within 10%, and only then can it re-draw low and decay. Any
"spikes last ~3 days" heuristic in `dl2sweep.py` should be *derived* from this
rule, not asserted.

## 4. Events

### 4a. Rumor scheduler (70/20/10) — `rumor_step 0x4040B6–0x4040E6` (dup `0x4041C7`)
Runs **after** the day's `market_step`; queues an event into the target market
(`record+0x0C`) for **exactly the next day, once**. Consumed and cleared next
`market_step` at `0x4056CF–0x4056D5`. Does not persist or auto-refire.
```python
r = rand() % 10
scheduled = predicted   if r <= 6      # 0..6
            NONE         if r in (7,8)
            opposite     if r == 9
```
Exact scheduler probabilities: predicted `22939/32768 = 70.0043%`,
none `6553/32768 = 19.9982%`, opposite `3276/32768 = 9.9976%`.

**Two ways to read the reliability numbers:** the table above reproduces the
executable. If you collapse the whole *realized next-day* outcome into one
categorical (because the scheduler's "NONE" branch still lets the ordinary 2%
roll fire, §4c), you get **70.2044% direction / 19.5978% none / 10.1977%
opposite** — use these when a single next-day distribution is wanted.

The *displayed* rumor direction is an independent 50/50 (`rand` parity,
`0x4040E9`); the scheduler then makes the next day match it 70% of the time.
That is why displayed rumors are ~70% reliable.

### 4b. Shock magnitude — `0x405421–0x405455` (shortage), `0x40545C–0x40548E` (glut)  ✓ matches `prices.event_*_price`, `stock.event_*_quantity`
```python
# shortage / scarce  (event == +1)
price     = (M + randmod(M//2)) * (5 + randmod(5))        # ~5M .. 13.5M
quantity //= (2 + randmod(5))                             # ÷ 2..6
# abundance / glut   (event == -1)
price     = (M - randmod(M//2)) // (5 + randmod(5))       # ~M/18 .. M/5
quantity *= (2 + randmod(5))                              # × 2..6
```
**Neither branch writes the target field** — the shock overwrites *visible
price* only, so post-shock decay is drift back toward the pre-existing normal
target. Stock smoothing (§5) happens **before** the shock, so event-day stock is
`smoothed_stock` then scaled, not stock recomputed from the shocked price.

### 4c. Ordinary spontaneous event (~2%) — `0x4053F0–0x405416`
Only when no rumor event is queued:
```python
if randmod(50) == 0:                      # 656/32768 = 2.0020%
    event = +1 if randmod(2) == 0 else -1 # 1.0010% each
```
Same magnitude formulas as §4b — there is **no separate "small spike" class.**

## 5. Stock daily step — `0x4053B4–0x4053EF`  ✓ matches `stock.step_quantity` (consumes no RNG)
`P` = price after §3 drift (before any §4b shock), `H = M//2`, `C` = capacity:
```python
desired  = C - truncdiv((P - H) * C, M)
quantity = max(truncdiv(quantity + desired, 2), 0)
```
`truncdiv` = C truncate-toward-zero (`constants.trunc_div`), **not** Python `//`,
for negative intermediates. Init (`0x405ED0–0x405F55`): `r=randmod(M);
price=target=M//2+r; quantity = C - truncdiv(r*C, M)` (stock starts inverse to
relative price).

## 6. Rumor generation — `0x40405A`, `0x40415E`  ✓ matches `simulator._generate_rumors`
```python
if rand()%3 == 0: rumor about current city     # 10923/32768 = 33.334% each gate
if rand()%3 == 0: rumor about one random OTHER city (of the other 14)
```
E[rumors/day] = 0.66669; P(≥1) = 55.557%. Each rumor: drug from all 17
(`rand%17`), then direction 50/50 (`rand%2`).

## 7. RNG draw order (seed-sync only — see §8)

### 7a. Per-market record — within `market_step 0x4052D0`
```python
if target != price:            rand()          # §3 drift
if abs(old_gap) < price//10:   rand()          # §3 reselect (%M)
# stock (§5): NO rand
if no queued event:            rand()          # §4c ordinary test (%50)
    if test == 0:              rand()          #      direction (%2)
if event != 0:                 rand(); rand(); rand()   # §4b: %(M//2), %5, %5
# UI, ACTIVE local slot ONLY:
if event != 0:                 rand(); rand()  # message variants (%5, %5)
else:                          rand()          # listing test (%3)
```
**The listing draw fires only for the current city's active slot, not every
market.** Remote/hidden markets still evolve (drift/stock/event) but consume no
listing/message RNG.

### 7b. Market loop order — `0x4052D0`
`for city in 0..14: for slot in 0..5: for drug in 0..16` → **15 × 6 × 17 = 1530
records/tick.** Init (`0x405ED0`) also consumes one `rand%M` per record = 1530
draws at world gen.

City order: Austin, Beijing, Boston, Detroit, London, Los Angeles, Miami,
Moscow, New York, Paris, San Francisco, St Petersburg, Sydney, Toronto,
Vancouver.
Drug order: Cocaine, Crack, Ecstacy, Hashish, Heroin, Ice, Kat, LSD, MDA,
Morphine, Mushrooms, Opium, PCP, Peyote, Pot, Special K, Speed.

### 7c. Daily global order
```
health regen (if health<100) → general event dispatcher (day>1) →
market_step 0x4052D0 → loan (no RNG) → rank (no RNG) →
shipment processing 0x405840 (variable RNG) → rumor_step 0x404050
```
Rumor RNG cannot be reproduced by advancing only the 1530 market states; the
shipment processor consumes a variable number of draws first.

## 8. Reconciliation: model vs executable

**Confirmed correct in `dl2model/`:** §2 M, §3 drift+reselection, §4b shock
formulas + no-target-write, §4c magnitude, §5 stock step (no RNG), §6 rumor
frequency/direction, §4a 1-day-once lead time, MSVC constants.

**Discrepancies:**

| # | Where | Model has | Executable has | Class |
|---|-------|-----------|----------------|-------|
| 1 | `simulator._roll_event` | `rand_mod(100)`, cutoffs 70/90 | `rand_mod(10)`, `≤6 / 7-8 / 9` | **Planning** (tiny: 70.06→70.004%) |
| 2 | `simulator._ordinary_event_roll` | one `rand_mod(100)` (`==0`/`==1`) | `rand%50==0` then `rand%2` | Seed-sync (prob ~identical; draw count differs) |
| 3 | `simulator.world` / `step_day` | 15×17 = 255 markets | 15×**6**×17 = 1530 (5 hidden slots/city) | Seed-sync only (§9: player trades slot 0; 255 is the exact economic world) |
| 4 | `stock.is_listed` call site | drawn for every market | active slot only | Seed-sync |
| 5 | `simulator.step_day` order | shipments→markets→rumors→finance | health→event-dispatch→markets→loan→rank→shipments→rumors | Seed-sync |

**Why the seed-sync rows don't block multi-day:** a receding-horizon
Monte-Carlo planner *samples* worlds consistent with the observed OCR
(prices/rumors) and averages — it never reproduces the executable's exact RNG
stream. Exact seed recovery from live OCR is impractical anyway. The decode
validated the *transition mechanics*, which is all the sampler needs. Only
correction #1 measurably moves planning EV, by ~0.06pp.

## 9. RESOLVED — the 6 market slots (decode pass 2026-09-24)
The 6 slots are **6 persistent parallel market structures per city** — a
vestigial "neighborhood/area" feature (Austin slot 0 = `"West Lk Hills"`,
slot 1 = `"Downtown"`; other slots hold placeholders `"a".."f"`). It is **not
exposed** in DL2.2.

**City memory layout** (`0xA88` bytes/city): name `+0x00`, country `+0x40`,
coord `+0x80`, price-mult `+0x84`, then 6 slots of `0x194` at `+0x88, +0x21C,
+0x3B0, +0x544, +0x6D8, +0x86C`. Each slot: `name[64]` + 17 drug records of
`0x14` bytes = `{quantity+0x00, price+0x04, target+0x08, pending_event+0x0C,
aux+0x10}`. Record address:
`WORLD_BASE + city*0xA88 + slot*0x194 + drug*0x14`.

**The player only ever trades slot 0.** The global slot selector `0x42AD7C` is
written `= 0` once at new-game init (`0x40600B`) and **never changed** — no UI,
day, city, or travel code writes it. Buy (`0x409C80`) and Sell (`0x409FB0`) both
index `world[current_city][slot=0][drug]`. Rumors also target slot 0 of their
city (`0x404080`, `0x4041A1`). `market_step` only runs the UI/listing logic for
the slot matching `0x42AD7C` (`0x4054A2`), i.e. slot 0 of the current city.

**⇒ Economic world model is `markets[15][17]` (255), not `[15][6][17]`.** No slot
rotation; accessible prices never jump between parallel markets. Slots 1–5 still
evolve and consume RNG — they matter **only** for byte-exact seed reproduction.

**`listed` is NOT a persistent field** — there is no listing field in the `0x14`
record. It is ephemeral UI availability, rolled fresh each day for the current
city × slot 0 only (`0x4055A5`):
```python
listed_today = (pending_event != 0) or (rand() % 3 != 0)   # event ⇒ always listed; else 2/3
```
So staying in Boston two days keeps you on Boston/slot 0 both days, but a
non-event drug may show today and be missing tomorrow purely from the fresh
`rand%3` roll. **Planning consequence:** a prospective non-event buy/sell in the
city you'll be in next day is only available with prob **2/3** — the MC layer
should sample this as genuine execution risk (an event-active drug is always
available). `stock.is_listed` already encodes `event or rand%3!=0`; only its
*scope* (drawn for all 255 markets vs. current-city-only) is the seed-sync gap.

## 10. Corrections to apply (pending, after §9 resolves)
1. `simulator._roll_event`: replace `rand_mod(100)` + 70/90 with `rand_mod(10)`
   + `≤6 / 7-8 / 9` (§4a).
2. `simulator._ordinary_event_roll`: replace the single `rand_mod(100)` with
   `rand_mod(50)==0` then `rand_mod(2)` for direction (§4c) — matches draw count
   as well as probability.
Pin every §2–§6 formula with a regression test citing the address here.
