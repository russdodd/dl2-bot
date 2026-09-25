"""State-conditioned Monte-Carlo receding-horizon planner.

From the live OCR `state`, sample plausible futures consistent with what's on
screen (`observe.sample_world`), roll each candidate day-1 action forward with the
decode-validated `simulator.Game`, force-liquidate on the horizon, and rank the
day-1 actions by expected final profit. Only day 1 is ever executed; re-run daily
on a fresh sweep — that is the receding horizon.

This module is the *search + rollout*; every numeric formula lives in the wave-1
modules. Candidate loads reuse `strategy._best_load` (the same greedy arbitrage
the live sweep uses — we do not invent a new search); the day-0 world comes from
`observe.sample_world`; the outcome distribution comes from `montecarlo._summarize`.

Continuation policy (days 2..H inside each rollout), kept deliberately cheap so it
runs inside the N×candidate loop:

* **Width-1 greedy.** Each intermediate day, scan every (dest, drug) using the
  rollout's CURRENT simulated world prices, take the single most profitable
  affordable/carryable hop (buy here -> fly -> sell what's listed on arrival), and
  execute it. This is a "the player keeps trading sensibly" policy, not an optimum;
  because only day 1 is ever executed and we re-plan daily, an approximate
  continuation is sufficient and the day-1 EV is what matters.
* **Forced final-day liquidation.** On the horizon day we buy nothing and sell all
  still-held goods that are listed in the current city (mirrors
  `strategy._liquidate`). Anything NOT listed on the final day stays unsold and is
  worthless (`final_score` ignores inventory) — so the decoded 2/3 listing risk
  (decode §9) and spike decay (§3) surface naturally as downside mass (p_loss>0,
  p10<EV).

Outcomes are scored as **profit = final_score - starting_score** (Δ cash+bank-debt),
the same convention as `montecarlo.evaluate`, so `p_loss` is the probability the
day-1 action loses money.
"""
from . import constants as C
from . import strategy, montecarlo, observe
from . import finance
from .constants import normal_mean

# Suggested budget (documented in the task): H in 3..4, N in 500..2000.
DEFAULT_HORIZON = 3
DEFAULT_N = 800


# --------------------------------------------------------------------------- #
# state -> current-city buy side + player facts
# --------------------------------------------------------------------------- #
def _buy_side(state):
    """Return `(buy_prices, buy_stock)` for the current city: what you can buy and
    how much stock is available. Prefer the observed Market (`state["market"]`,
    buy prices + per-drug stock); fall back to the world price row + `state["stock"]`."""
    current = state.get("current_city") or state.get("city")
    market = state.get("market") or {}
    if market:
        buy_prices = {d: v["price"] for d, v in market.items() if v.get("price")}
        buy_stock = {d: v.get("qty") for d, v in market.items()
                     if v.get("qty") is not None}
        return buy_prices, buy_stock
    buy_prices = dict((state.get("prices") or {}).get(current, {}))
    buy_stock = dict((state.get("stock") or {}).get(current, {}))
    return buy_prices, buy_stock


def _held(state):
    """Starting inventory as `{drug: qty}` from either state form."""
    inv = state.get("inventory")
    if isinstance(inv, dict):
        return {d: (v["qty"] if isinstance(v, dict) else v) for d, v in inv.items()}
    return {it["drug"]: int(it.get("qty", 0)) for it in (inv or [])}


# --------------------------------------------------------------------------- #
# candidate day-1 actions
# --------------------------------------------------------------------------- #
def candidate_actions(state):
    """Enumerate the day-1 candidates the live sweep considers: for every reachable
    destination (including staying put), the greedy arbitrage load bought here and
    sold on arrival. Reuses `strategy._best_load`. Returns a list of dicts
    `{"dest", "buys": [(drug, qty)], "label"}`; `dest == current` means stay."""
    current = state.get("current_city") or state.get("city")
    world_prices = state.get("prices") or {}
    cities = list(world_prices.keys())
    if current not in cities:
        cities = [current] + cities
    cash = int(state.get("cash", 0))
    rank = state.get("rank") or finance.rank_for_wealth(
        int(state.get("cash", 0)) + int(state.get("bank", 0)))
    capacity = int(state.get("capacity") or finance.capacity_for_rank(rank))
    rumors = state.get("rumors") or []
    buy_prices, buy_stock = _buy_side(state)

    cands = []
    for dest in cities:
        dest_prices = world_prices.get(dest, {})
        # Staying sells held here today (arrival 0); flying arrives in 1 day.
        arrival = 0 if dest == current else 1
        loads, _profit = strategy._best_load(
            buy_prices, buy_stock, dest, dest_prices, cash, capacity, arrival, rumors)
        buys = [(l["drug"], l["qty"]) for l in loads if l["qty"] > 0]
        verb = "stay in" if dest == current else "fly to"
        if buys:
            top = max(loads, key=lambda l: (l["unit_value"] - l["unit_cost"]) * l["qty"])
            label = (f"{verb} {dest}: buy {top['qty']:,} {top['drug']} "
                     f"@ ${top['unit_cost']:,}" + ("" if len(buys) == 1 else " (+more)"))
        else:
            label = f"{verb} {dest}: sell held, no buy"
        cands.append({"dest": dest, "buys": buys, "label": label})
    return cands


# --------------------------------------------------------------------------- #
# rollout mechanics on a sampled Game
# --------------------------------------------------------------------------- #
def _do_buys(game, buys):
    """Execute day-1 buys in the current city, clamped to live stock & cash so a
    rounding gap can never drive cash negative or oversell a market."""
    for drug, qty in buys:
        market = game.world[game.player.city].get(drug)
        if market is None or not market.price:
            continue
        affordable = game.player.cash // market.price if market.price > 0 else 0
        q = int(min(qty, max(0, market.quantity), max(0, affordable)))
        if q > 0:
            game.buy(drug, q)


def _sell_listed_here(game):
    """Sell every held drug that the current city's market is listing today. A drug
    that isn't listed can't be sold (decode §9) and stays in inventory."""
    p = game.player
    for drug, entry in list(p.inventory.items()):
        qty = entry.get("qty", 0)
        if qty <= 0:
            continue
        market = game.world[p.city].get(drug)
        if market is not None and market.listed and market.price:
            game.sell(drug, qty)


def _greedy_hop(game):
    """Width-1 continuation: the single most profitable affordable/carryable hop
    from the current city using the rollout's CURRENT simulated world prices. Buy
    here, fly, and (the caller) sell what's listed on arrival. Returns the dest
    city we flew to, or None if no positive-margin hop was found (then stay)."""
    p = game.player
    here = p.city
    cash = p.cash
    capacity = finance.capacity_for_rank(p.rank)
    here_markets = game.world[here]
    best = None                                   # (margin_total, dest, drug, qty)
    for dest, drug_markets in game.world.items():
        if dest == here:
            continue
        for drug, buy_m in here_markets.items():
            if not buy_m.listed or not buy_m.price or buy_m.quantity <= 0:
                continue
            sell_m = drug_markets.get(drug)
            if sell_m is None or not sell_m.price:
                continue
            margin = sell_m.price - buy_m.price
            if margin <= 0:
                continue
            qty = int(min(capacity, buy_m.quantity, cash // buy_m.price))
            if qty <= 0:
                continue
            total = margin * qty
            if best is None or total > best[0]:
                best = (total, dest, drug, qty)
    if best is None:
        return None
    _total, dest, drug, qty = best
    game.buy(drug, qty)
    game.fly(dest)
    return dest


def _play(state, candidate, horizon, seed):
    """One seeded rollout of a candidate. Returns profit = Δ(final_score)."""
    game = observe.sample_world(state, seed)
    before = game.score()

    # Day 1: the candidate action (buy here, then go / stay), sell on arrival.
    _do_buys(game, candidate["buys"])
    dest = candidate["dest"]
    if horizon <= 1:
        # Degenerate horizon: no travel, immediate liquidation here (matches the
        # strategy H<=1 path). Sell whatever the current market is listing.
        _sell_listed_here(game)
        return game.score() - before

    if dest != game.player.city:
        game.fly(dest)                            # advances a day, evolves the world
    else:
        game.step_day()
    _sell_listed_here(game)

    # Days 2 .. H-1: cheap greedy continuation (see module docstring).
    for _ in range(horizon - 2):
        hopped = _greedy_hop(game)
        if hopped is None:
            game.step_day()
        _sell_listed_here(game)

    # Final day: forced liquidation — sell everything still listed, buy nothing.
    game.step_day()
    _sell_listed_here(game)
    return game.score() - before


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def plan_mc(state: dict, horizon: int = DEFAULT_HORIZON, n: int = DEFAULT_N,
            base_seed: int = 0) -> dict:
    """Rank day-1 actions for `state` by Monte-Carlo expected profit.

    For each candidate day-1 action (`candidate_actions`), run `n` seeded rollouts
    (`observe.sample_world(base_seed+i)` -> apply the action -> step to `horizon`
    -> force-liquidate) and summarize the profit distribution with
    `montecarlo._summarize`. Returns:

        {
          "horizon": H, "n": N,
          "candidates": [ {..candidate.., **summary} sorted by EV desc ],
          "best": <the #1 candidate, or None if there were no candidates>,
        }

    where each summary carries `ev, p10, p50, p90, p_loss, n` (profit = Δ final
    score, so `p_loss` is the chance the day-1 action loses money). Percentiles
    below the EV / a nonzero `p_loss` reflect the decoded 2/3 listing risk and
    spike decay realized across the sampled worlds.
    """
    cands = candidate_actions(state)
    ranked = []
    for cand in cands:
        outcomes = [_play(state, cand, horizon, base_seed + i) for i in range(n)]
        summary = montecarlo._summarize(outcomes)
        ranked.append({**cand, **summary})
    ranked.sort(key=lambda c: c["ev"], reverse=True)
    return {"horizon": horizon, "n": n, "candidates": ranked,
            "best": ranked[0] if ranked else None}
