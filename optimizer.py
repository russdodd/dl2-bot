#!/usr/bin/env python3
"""
Drug Lord 2 trade optimizer.

Consumes a game-state JSON (produced by the OCR reader, or hand-written for
testing) and prints the profit-maximizing plan for the current turn:
what to sell now, what to buy, and which city to fly to.

Because Drug Lord 2 exposes *world* prices (every city's prices are visible),
the single-hop decision is solvable exactly. Future days' prices are randomly
regenerated, so this optimizes the current move (a 1-hop horizon) plus flags
debt / rank-up considerations. It does not pretend to predict tomorrow.

State schema (all cash values are plain integers, no $ or commas):
{
  "current_city": "New York",
  "cash": 12000,
  "debt": 2000,                       # optional
  "days_left": 27,                    # optional (advice only)
  "capacity": 100,                    # max units you can carry
  "no_scent": 10,                     # optional; cans available for flying w/ drugs
  "prices": {                         # world price grid; omit a drug a city doesn't list
     "New York": {"Cocaine": 15000, "Heroin": 8000},
     "London":   {"Cocaine": 22000, "Heroin": 6000}
  },
  "inventory": [                      # what you currently hold
     {"drug": "Cocaine", "qty": 10, "avg_buy": 9000}
  ]
}
"""
import json
import sys
from dataclasses import dataclass, field


@dataclass
class Load:
    drug: str
    qty: int
    unit_cost: int
    unit_sell: int

    @property
    def profit(self) -> int:
        return (self.unit_sell - self.unit_cost) * self.qty


@dataclass
class Plan:
    destination: str
    sell_now: list = field(default_factory=list)   # (drug, qty, unit_price, realized)
    buys: list = field(default_factory=list)        # Load objects (buy here, sell at dest)
    sell_held_at_dest: list = field(default_factory=list)  # (drug, qty, unit_price)
    projected_cash: int = 0
    note: str = ""


def _fill_load(city_prices, dest_prices, cash, capacity, stock=None):
    """
    Best set of drugs to buy in the current city and sell at `dest`.
    Constraints: cash, carrying capacity (units), and the stock available to buy in
    the current city (per drug). Selling at the destination is unlimited. With only a
    handful of drugs this is small; we solve it greedily two ways (best profit-per-unit
    and best profit-per-dollar) and keep whichever yields more.
    """
    stock = stock or {}
    candidates = []
    for drug, buy in city_prices.items():
        sell = dest_prices.get(drug)
        if sell is None or buy is None or buy <= 0:
            continue
        margin = sell - buy
        if margin > 0:
            candidates.append((drug, buy, sell, margin))

    INF = float("inf")
    unlimited_cap = capacity is None            # can ship, so carry capacity is no limit

    def greedy(key):
        remaining_cash = cash
        remaining_cap = INF if unlimited_cap else capacity
        loads = {}
        for drug, buy, sell, margin in sorted(candidates, key=key, reverse=True):
            if remaining_cap <= 0 or remaining_cash < buy:
                continue
            avail = stock.get(drug)                     # units in stock to buy here
            if unlimited_cap and avail is None:
                continue                                # need a known stock to buy it all
            if avail is None:
                avail = remaining_cap
            qty = int(min(remaining_cap, remaining_cash // buy, avail))
            if qty <= 0:
                continue
            loads[drug] = Load(drug, qty, buy, sell)
            remaining_cash -= qty * buy
            remaining_cap -= qty
        return loads

    by_unit = greedy(lambda c: c[3])                       # margin per unit
    by_dollar = greedy(lambda c: c[3] / c[1])              # margin per dollar
    best = max([by_unit, by_dollar],
               key=lambda ld: sum(l.profit for l in ld.values()))
    return list(best.values())


def optimize(state: dict) -> list:
    city = state["current_city"]
    cash = int(state["cash"])
    # With shipping, carry capacity isn't a real cap — the limit per drug is its stock.
    capacity = None if state.get("ship") else int(state["capacity"])
    prices = state["prices"]                  # sell prices, prices[city][drug]
    inventory = state.get("inventory", [])

    # You can only BUY what "The Market" in the current city lists (a subset of all
    # drugs), each capped by its available stock. Selling uses the world prices.
    market = state.get("market", {})          # {drug: {"price":p, "qty":q}}
    if market:
        buy_prices = {d: info["price"] for d, info in market.items() if info.get("price")}
        buy_stock = {d: info["qty"] for d, info in market.items() if info.get("qty")}
    else:                                     # fallback: buy at world price for current city
        buy_prices = prices.get(city, {})
        buy_stock = state.get("stock", {})
    here_sell = prices.get(city, {})          # sell prices in the current city

    plans = []
    for dest, dest_prices in prices.items():
        p = Plan(destination=dest)

        # 1) Held inventory: sell each held drug wherever it is worth more between
        #    the current city (sell before leaving) and the destination (carry & sell).
        working_cash = cash
        for item in inventory:
            drug, qty = item["drug"], int(item["qty"])
            here = here_sell.get(drug)
            there = dest_prices.get(drug)
            best_here = here if here is not None else -1
            best_there = there if there is not None else -1
            if best_here <= 0 and best_there <= 0:
                continue
            if best_here >= best_there:
                p.sell_now.append((drug, qty, here, here * qty))
                working_cash += here * qty
            else:
                p.sell_held_at_dest.append((drug, qty, there))

        # 2) Buy here, sell at dest (arbitrage). Uses cash freed by selling-now.
        loads = _fill_load(buy_prices, dest_prices, working_cash, capacity, buy_stock)
        p.buys = loads

        realized = sum(r[3] for r in p.sell_now)
        arb_profit = sum(l.profit for l in loads)
        held_dest = sum(q * price for (_, q, price) in p.sell_held_at_dest)
        # projected cash after: sell-now proceeds already in working_cash; then we
        # spend on buys and (at dest) receive buy-sells + held-at-dest proceeds.
        spend = sum(l.qty * l.unit_cost for l in loads)
        p.projected_cash = working_cash - spend + \
            sum(l.qty * l.unit_sell for l in loads) + held_dest
        p.note = f"arb +{arb_profit:,} | sell-now {realized:,} | carry&sell {held_dest:,}"
        plans.append(p)

    plans.sort(key=lambda pl: pl.projected_cash, reverse=True)
    return plans


def format_plan(state, plans, top=3, unlimited=False):
    city = state["current_city"]
    cash = int(state["cash"])
    out = []
    if unlimited and state.get("ship"):
        out.append(f"You are in {city}. Buying the full stock of every profitable drug "
                   f"(unlimited cash; ship in batches, so carry capacity is no limit).")
    elif unlimited:
        out.append(f"You are in {city}, capacity {int(state['capacity']):,} units "
                   f"(assuming unlimited cash).")
    else:
        out.append(f"You are in {city} with ${cash:,}"
                   + (f", debt ${int(state['debt']):,}" if state.get("debt") else "")
                   + (f", {state['days_left']} days left" if state.get("days_left") is not None else "")
                   + f", capacity {int(state['capacity'])} units.")
    best = plans[0]
    stay = next((p for p in plans if p.destination == city), None)
    out.append("")
    out.append("=" * 60)
    if best.destination == city:
        out.append(f"BEST MOVE: STAY in {city}")
    else:
        out.append(f"BEST MOVE: FLY to {best.destination}")
    out.append("=" * 60)
    if best.sell_now:
        out.append(f"  Sell now (in {city}):")
        for drug, qty, price, realized in best.sell_now:
            out.append(f"    - Sell {qty} {drug} @ ${price:,}  = ${realized:,}")
    if best.buys:
        out.append(f"  Buy now (in {city}):")
        for l in best.buys:
            out.append(f"    - Buy {l.qty} {l.drug} @ ${l.unit_cost:,} "
                       f"(sell in {best.destination} @ ${l.unit_sell:,}, +${l.profit:,})")
    if best.sell_held_at_dest:
        out.append(f"  Carry & sell in {best.destination}:")
        for drug, qty, price in best.sell_held_at_dest:
            out.append(f"    - {qty} {drug} @ ${price:,}")
    gain = best.projected_cash - cash
    if unlimited:
        out.append(f"  Profit from this move: +${gain:,}")
    else:
        out.append(f"  Projected cash after the move: ${best.projected_cash:,}  "
                   f"({'+' if gain >= 0 else ''}{gain:,})")
    if stay and best.destination != city:
        edge = best.projected_cash - stay.projected_cash
        out.append(f"  (Beats staying put by ${edge:,}.)")

    out.append("")
    out.append("Other destinations, ranked:")
    for p in plans[1:top+1]:
        tag = " [stay]" if p.destination == city else ""
        val = (p.projected_cash - cash) if unlimited else p.projected_cash
        label = "profit" if unlimited else "projected"
        out.append(f"  {p.destination}{tag}: {label} ${val:,}  |  {p.note}")

    # Advisories
    advisories = []
    if state.get("debt"):
        advisories.append(
            f"Debt is ${int(state['debt']):,} and compounds daily — pay it down once "
            f"cash comfortably exceeds it.")
    if state.get("days_left") is not None and int(state["days_left"]) <= 3:
        advisories.append("Few days left — liquidate inventory to cash before the game ends.")
    if advisories:
        out.append("")
        out.append("Notes:")
        for a in advisories:
            out.append(f"  * {a}")
    return "\n".join(out)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "-"
    raw = sys.stdin.read() if path == "-" else open(path).read()
    state = json.loads(raw)
    plans = optimize(state)
    if not plans:
        print("No price data in state.")
        return
    print(format_plan(state, plans))


if __name__ == "__main__":
    main()
