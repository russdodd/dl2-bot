"""Multi-day plan search — beyond today's 1-hop decision.

Searches sequences of day-by-day actions over a horizon to maximize the expected
`final_score`, composing the wave-1 EV formulas (prices/stock/shipping/finance/
rumors) and — where a real rollout helps — the p08 `simulator.Game`.

Design (kept deliberately tractable):

* **Valuation with look-ahead.** A remote destination's price is never taken at
  face value: it is decayed toward the market's normal level `M` over the actual
  arrival delay via `prices.expected_price_after_days`, and OVERRIDDEN by
  `rumors.expected_next_price_given_rumor` when a rumor names that (city, drug).
  This decayed valuation *is* the look-ahead that lets the planner prefer a spike
  it can still reach over one that will have bled out by arrival.

* **Beam search over hops.** Each day is a "hop": optionally liquidate held
  inventory (at whichever of {here-now, dest-on-arrival} pays more), buy an
  arbitrage load in the current city, and fly (or stay). A hop's arbitrage load
  is sold on arrival, so nothing is carried between hops except by explicit
  choice. We expand every candidate destination from every node, then PRUNE to
  the `BEAM_WIDTH` best nodes by projected score. Channel (carry-with-No-Scent
  vs ship) is chosen per hop from capacity/No-Scent coverage; ship legs value the
  goods over the longer expected transit and pay `shipping.expected_ship_value`.

* **Horizon is hard.** Hops run on days `0 .. H-2` (they must arrive by `H-1`);
  the final tradable day `H-1` is a forced full liquidation so we never end
  holding worthless goods. A first promotion adds `PROMO_BONUS_DAYS` (capped at
  `MAX_DAYS`).

* **Loan-to-promote is evaluated explicitly** (`evaluate_loan_to_promote`), never
  assumed: borrowing can vault the next rank threshold (keys off cash+bank) to
  unlock +5 days and a higher capacity tier, but the principal compounds
  (`finance.debt_after_days`) and the interest subtracts from score. We take it
  only when the extra days' value beats the interest.

Pure helpers (`decayed_unit_value`, `evaluate_loan_to_promote`, `_best_load`, …)
are unit-tested directly; the only test that needs a real `Game` is marked
`xfail` until p08 merges.
"""
from dataclasses import dataclass, field, replace

from . import constants as C
from . import prices, shipping, finance, rumors

# --- search knobs -----------------------------------------------------------
BEAM_WIDTH = 4
DEFAULT_SHIPPER = "Sharp Ship"
DEFAULT_SHIP_DAYS = 3
# START_DEBT is owed to Buddles (15%/day); use that unless the state says otherwise.
DEFAULT_LOAN_RATE_PCT = C.LOAN_SHARKS["Buddles"][1]


# ===========================================================================
# Valuation helpers (pure)
# ===========================================================================
def _active_rumor(rumors_list, city, drug):
    """Return a `rumors.Rumor` if `rumors_list` carries one for (city, drug)."""
    for r in rumors_list or []:
        if r.get("city") == city and r.get("drug") == drug:
            return rumors.Rumor(drug=drug, city=city, direction=r["direction"])
    return None


def decayed_unit_value(price, city, drug, arrival_days, rumor=None):
    """Expected sell price of one unit of `drug` in `city`, `arrival_days` hence.

    The price mean-reverts toward the market's normal level `M` (the target the
    game's hidden target wanders around), so a fresh spike is worth progressively
    less the longer it takes to arrive — `prices.expected_price_after_days`
    captures that linear-in-dollars decay. When a rumor is active for (city,drug)
    we instead condition on it via the rumor forward model (a 1-day-ahead
    expectation, since a rumor predicts *tomorrow's* event).
    """
    if drug not in C.BASE_PRICE or city not in C.CITY_MULT:
        # Unknown market (e.g. a synthetic test city): can't model decay.
        return float(price)
    M = C.normal_mean(drug, city)
    target = M
    if rumor is not None:
        return rumors.expected_next_price_given_rumor(price, target, M, rumor)
    if arrival_days <= 0:
        return float(price)
    return prices.expected_price_after_days(price, target, arrival_days)


def _best_load(buy_prices, buy_stock, dest_city, dest_prices, cash, capacity,
               arrival_days, rumors_list):
    """Greedy arbitrage: buy in the current city, sell (on arrival) at the
    destination's decayed / rumor-adjusted value.

    Solved greedily two ways (best margin-per-unit and best margin-per-dollar) —
    with only ~16 drugs the greedy solutions are optimal-or-close and cheap — and
    we keep whichever load is more profitable. Returns `(loads, profit)` where
    each load is `{drug, qty, unit_cost, unit_value}` and `profit` is the EV.
    """
    candidates = []
    for drug, buy in buy_prices.items():
        sell_price = dest_prices.get(drug)
        if not buy or sell_price is None:
            continue
        rumor = _active_rumor(rumors_list, dest_city, drug)
        value = decayed_unit_value(sell_price, dest_city, drug, arrival_days, rumor)
        margin = value - buy
        if margin > 0:
            candidates.append((drug, buy, value, margin))

    def greedy(key):
        remaining_cash, remaining_cap, loads = cash, capacity, {}
        for drug, buy, value, margin in sorted(candidates, key=key, reverse=True):
            if remaining_cap <= 0 or remaining_cash < buy:
                continue
            avail = buy_stock.get(drug, remaining_cap)
            qty = int(min(remaining_cap, remaining_cash // buy, avail))
            if qty <= 0:
                continue
            loads[drug] = {"drug": drug, "qty": qty,
                           "unit_cost": buy, "unit_value": value}
            remaining_cash -= qty * buy
            remaining_cap -= qty
        return loads

    def profit(loads):
        return sum((l["unit_value"] - l["unit_cost"]) * l["qty"]
                   for l in loads.values())

    by_unit = greedy(lambda c: c[3])
    by_dollar = greedy(lambda c: c[3] / c[1])
    best = by_unit if profit(by_unit) >= profit(by_dollar) else by_dollar
    return list(best.values()), profit(best)


# ===========================================================================
# Loan-to-promote (pure, evaluated explicitly)
# ===========================================================================
def _next_threshold_rank(wealth):
    """Lowest rank whose (cash+bank) threshold is strictly above `wealth`, or
    None if already at/above the top threshold."""
    for rank in C.RANKS:
        if C.RANK_THRESHOLD[rank] > wealth:
            return rank
    return None


def _cheapest_shark_for(loan_needed, cash):
    """The affordable shark (`max_loan >= loan_needed`) with the lowest daily
    rate, as `(name, rate)`, or None if none can supply it."""
    best = None
    for name, (mult, rate, due) in C.LOAN_SHARKS.items():
        if finance.max_loan(cash, name) >= loan_needed:
            if best is None or rate < best[1]:
                best = (name, rate)
    return best


def evaluate_loan_to_promote(state, value_per_extra_day, shark=None,
                             days_outstanding=None, extra_days=None):
    """Decide whether borrowing to vault the NEXT rank threshold pays off.

    A loan can push cash+bank over `RANK_THRESHOLD[next]`; sustaining that wealth
    `RANK_PROMOTE_SUSTAIN_DAYS` days promotes you, granting `+PROMO_BONUS_DAYS`
    days (once, on the first promotion to that rank) and a higher capacity tier.
    But the borrowed principal compounds daily (`finance.debt_after_days`) and the
    interest subtracts from `final_score`. Take the loan iff the extra days' value
    beats the interest.

    `value_per_extra_day` — the planner's estimate of expected profit per extra
    trading day — is a parameter so this stays pure and testable. Returns a dict
    describing the decision; `take` is the verdict.
    """
    cash = int(state.get("cash", 0))
    bank = int(state.get("bank", 0))
    wealth = cash + bank
    target_rank = _next_threshold_rank(wealth)
    result = {"take": False, "target_rank": target_rank, "loan": 0,
              "interest_cost": 0, "extra_days": 0, "benefit": 0,
              "shark": None, "rate": None, "reason": ""}
    if target_rank is None:
        result["reason"] = "already at the top rank threshold"
        return result
    if target_rank in set(state.get("promotions", [])):
        result["reason"] = f"{target_rank} already earned (no bonus days)"
        return result

    loan_needed = C.RANK_THRESHOLD[target_rank] - wealth
    if loan_needed <= 0:
        result["reason"] = "no loan needed"
        return result

    if shark is None:
        pick = _cheapest_shark_for(loan_needed, cash)
        if pick is None:
            result["reason"] = "no shark can supply the required loan"
            return result
        shark, rate = pick
    else:
        rate = C.LOAN_SHARKS[shark][1]
        if finance.max_loan(cash, shark) < loan_needed:
            result["reason"] = f"{shark} cannot supply ${loan_needed:,}"
            return result

    if days_outstanding is None:
        days_outstanding = C.RANK_PROMOTE_SUSTAIN_DAYS
    if extra_days is None:
        extra_days = C.PROMO_BONUS_DAYS

    interest_cost = finance.debt_after_days(loan_needed, rate, days_outstanding) - loan_needed
    benefit = extra_days * value_per_extra_day
    result.update(loan=loan_needed, interest_cost=interest_cost,
                  extra_days=extra_days, benefit=benefit, shark=shark, rate=rate)
    result["take"] = benefit > interest_cost
    result["reason"] = ("extra-day value beats interest" if result["take"]
                        else "interest exceeds extra-day value")
    return result


# ===========================================================================
# Beam-search node + expansion
# ===========================================================================
@dataclass
class _Node:
    day: int
    city: str
    cash: int
    bank: int
    debt: int
    rank: str
    capacity: int
    held: dict                       # drug -> qty
    actions: tuple = ()
    promotions: frozenset = frozenset()


def _choose_channel(node, dest, ctx):
    """Carry-with-No-Scent vs ship, from capacity and No-Scent coverage.

    Staying put, or a load the No-Scent cans (or the 1000-unit max cover) can
    hide, goes by carry (arrives next day). A load bigger than that is shipped
    (no airport check, but a fee and failure risk), valued over the longer
    expected transit.
    """
    if dest == node.city:
        return {"channel": "carry", "arrival_days": 1}
    covered = ctx["no_scent"] * C.NO_SCENT_UNITS_PER_CAN
    max_cover = C.NO_SCENT_MAX_CANS * C.NO_SCENT_UNITS_PER_CAN
    if node.capacity <= covered or node.capacity <= max_cover:
        return {"channel": "carry", "arrival_days": 1}
    arrival = int(round(shipping.expected_transit_days(DEFAULT_SHIP_DAYS)))
    return {"channel": "ship", "arrival_days": arrival}


def _expand(node, ctx):
    """All candidate hops out of `node`, one per reachable destination."""
    world_prices = ctx["prices"]
    here = node.city
    here_prices = world_prices.get(here, {})
    children = []

    for dest in ctx["cities"]:
        dest_prices = world_prices.get(dest, {})
        chan = _choose_channel(node, dest, ctx)
        arrival = chan["arrival_days"]

        # 1) Liquidate held inventory at the better of here-now / dest-on-arrival.
        cash = node.cash
        held_sales = []
        for drug, qty in node.held.items():
            options = []
            here_p = here_prices.get(drug)
            if here_p:
                options.append((float(here_p), here))
            if drug in dest_prices:
                rumor = _active_rumor(ctx["rumors"], dest, drug)
                options.append(
                    (decayed_unit_value(dest_prices[drug], dest, drug, arrival, rumor), dest))
            if not options:
                continue
            unit_price, where = max(options)
            cash += int(unit_price * qty)
            held_sales.append({"drug": drug, "qty": qty,
                               "at": where, "unit_price": int(unit_price)})

        # 2) Arbitrage: buy here, sell on arrival at dest.
        loads, _profit = _best_load(here_prices, ctx["stock"], dest, dest_prices,
                                    cash, node.capacity, arrival, ctx["rumors"])
        spend = sum(l["qty"] * l["unit_cost"] for l in loads)
        units = sum(l["qty"] for l in loads)
        gross = sum(l["unit_value"] * l["qty"] for l in loads)
        if chan["channel"] == "ship" and units:
            avg_val = gross / units
            proceeds = int(shipping.expected_ship_value(
                here, dest, units, avg_val, DEFAULT_SHIPPER, DEFAULT_SHIP_DAYS))
        else:
            proceeds = int(gross)
        cash = cash - spend + proceeds

        # 3) Daily costs: overhead + one day of debt compounding.
        cash -= finance.daily_overhead(node.rank)
        debt = finance.debt_after_days(node.debt, ctx["loan_rate_pct"], 1)

        # 4) Rank can rise as wealth grows (capacity follows). We adopt the higher
        #    tier when crossed; the +days bonus is handled at plan level via the
        #    explicit loan/promotion evaluation.
        reached = finance.rank_for_wealth(cash + node.bank)
        rank = reached if C.RANKS.index(reached) > C.RANKS.index(node.rank) else node.rank
        capacity = max(node.capacity, finance.capacity_for_rank(rank))

        action = {"day": node.day,
                  "action": "fly" if dest != here else "stay",
                  "dest": dest, "channel": chan["channel"],
                  "arrival_days": arrival, "sell_held": held_sales,
                  "buys": [{k: l[k] for k in ("drug", "qty", "unit_cost", "unit_value")}
                           for l in loads]}
        children.append(_Node(day=node.day + 1, city=dest, cash=cash, bank=node.bank,
                              debt=debt, rank=rank, capacity=capacity, held={},
                              actions=node.actions + (action,),
                              promotions=node.promotions))
    return children


def _liquidation_value(held, city, world_prices):
    here_prices = world_prices.get(city, {})
    return sum((here_prices.get(drug, 0) or 0) * qty for drug, qty in held.items())


def _score(node):
    """Projected final score — inventory is worthless, so only cash/bank/debt."""
    return finance.final_score(node.cash, node.bank, node.debt)


def _score_estimate(node, world_prices):
    """Pruning key: realized score plus what still-held goods would liquidate for."""
    return _score(node) + _liquidation_value(node.held, node.city, world_prices)


def _liquidate(node, world_prices, notes):
    """Final tradable day: sell everything still held in the current city."""
    here_prices = world_prices.get(node.city, {})
    cash, sales = node.cash, []
    for drug, qty in node.held.items():
        unit = here_prices.get(drug, 0) or 0
        cash += unit * qty
        sales.append({"drug": drug, "qty": qty, "unit_price": unit})
    if sales:
        notes.append(
            f"Final day: liquidate {sum(s['qty'] for s in sales)} units in {node.city}.")
    action = {"day": node.day, "action": "liquidate", "city": node.city, "sell": sales}
    return replace(node, cash=cash, held={}, actions=node.actions + (action,))


# ===========================================================================
# Value-per-day estimate (feeds the loan decision)
# ===========================================================================
def _value_per_day_estimate(here, world_prices, cities, ctx, cash, capacity):
    """Rough expected profit for one day: the best single-hop arbitrage from
    `here`, valued one day out. Conservative — it uses today's capacity, so a
    promotion (bigger capacity) can only do better than this estimate."""
    here_prices = world_prices.get(here, {})
    best = 0.0
    for dest in cities:
        if dest == here:
            continue
        _loads, profit = _best_load(here_prices, ctx["stock"], dest,
                                    world_prices.get(dest, {}), cash, capacity, 1,
                                    ctx["rumors"])
        best = max(best, profit)
    return best


# ===========================================================================
# Public API
# ===========================================================================
def plan(state: dict, horizon: int = None) -> dict:
    """Return the best multi-day plan for `state` (schema in the task doc):
    `{"actions": [...], "projected_score": int, "notes": [...]}`.

    Reuses the live optimizer's state shape plus `rank`, `bank`, `days_left`,
    `rumors` (list of `{drug, city, direction}`), `no_scent`. `horizon=None`
    defaults to `days_left`.
    """
    world_prices = state["prices"]
    cities = list(world_prices.keys())
    here = state["current_city"]
    cash = int(state.get("cash", 0))
    bank = int(state.get("bank", 0))
    debt = int(state.get("debt", 0))
    rank = state.get("rank") or finance.rank_for_wealth(cash + bank)
    capacity = int(state.get("capacity", finance.capacity_for_rank(rank)))
    days_left = int(state.get("days_left", 0))
    H = horizon if horizon is not None else days_left
    held = {it["drug"]: int(it["qty"]) for it in state.get("inventory", [])}

    ctx = {"prices": world_prices, "cities": cities,
           "stock": state.get("stock", {}), "rumors": state.get("rumors", []),
           "no_scent": int(state.get("no_scent", 0)),
           "loan_rate_pct": int(state.get("loan_rate_pct", DEFAULT_LOAN_RATE_PCT))}

    notes, actions = [], []

    # --- loan-to-promote (day 0), evaluated explicitly ---------------------
    vpd = _value_per_day_estimate(here, world_prices, cities, ctx, cash, capacity)
    loan_eval = evaluate_loan_to_promote(
        {"cash": cash, "bank": bank, "promotions": list(state.get("promotions", []))}, vpd)
    if loan_eval["take"]:
        loan = loan_eval["loan"]
        cash += loan
        debt += loan
        rank = loan_eval["target_rank"]
        capacity = max(capacity, finance.capacity_for_rank(rank))
        H = min(C.MAX_DAYS, H + loan_eval["extra_days"])
        actions.append({"day": 0, "action": "loan", "shark": loan_eval["shark"],
                        "amount": loan, "target_rank": rank,
                        "extra_days": loan_eval["extra_days"]})
        notes.append(
            f"Borrow ${loan:,} from {loan_eval['shark']} to reach {rank} "
            f"(+{loan_eval['extra_days']} days); interest ~${loan_eval['interest_cost']:,} "
            f"< extra-day value ~${loan_eval['benefit']:,.0f}.")
    else:
        notes.append(f"No promotional loan ({loan_eval['reason']}).")

    start = _Node(day=0, city=here, cash=cash, bank=bank, debt=debt, rank=rank,
                  capacity=capacity, held=held,
                  promotions=frozenset(state.get("promotions", [])))

    # --- no room to trade: liquidate immediately on the final day ----------
    if H <= 1:
        final = _liquidate(start, world_prices, notes)
        return {"actions": actions + list(final.actions),
                "projected_score": _score(final), "notes": notes}

    # --- beam search over hops (days 0 .. H-2, arriving by H-1) -------------
    beam = [start]
    for _ in range(H - 1):
        expanded = []
        for node in beam:
            expanded.extend(_expand(node, ctx))
        expanded.sort(key=lambda n: _score_estimate(n, world_prices), reverse=True)
        beam = expanded[:BEAM_WIDTH]

    best = max(beam, key=lambda n: _score_estimate(n, world_prices))
    best = _liquidate(best, world_prices, notes)   # forced final-day liquidation
    return {"actions": actions + list(best.actions),
            "projected_score": _score(best), "notes": notes}
