#!/usr/bin/env python3
"""`dl2 decay` — pure analysis of the audit log to COLLECT spike-decay data.

A price spike decays over the days it takes to fly/ship to a city and sell, so
what you realize is below what `dl2 decide` projected. We measured that the decay
is a function of DAYS ELAPSED ONLY (selling 4,120 Morphine did not move the price
— it's time-decay of the spike, not market impact), so this pairs each realized
`sell` with the most recent PRIOR projection for that (city, drug) and reports:

    realized / projected   vs   days elapsed

We deliberately DO NOT hardcode a decay coefficient — with only a handful of
points there's nothing to fit. This command accumulates clean pairs; once there
are enough (>= MIN_PAIRS with a known day gap) it fits ratio = e^(-k*days) and
writes decay_model.json. Until then it prints the rows and says so.

Projections come from a `decision` event's `arb_targets` (buy-here/sell-there —
what we actually trade) and `held_spikes` (inventory liquidation). Realized
prices come from `sell` events. Cities are matched on their short name.

No screen, no game: this only reads ~/code/dl2/audit_log.jsonl.
"""
import json
import math
import os
import sys
import time

import audit
from dl2model import prices, shipping, rumors
from dl2model.constants import normal_mean

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.environ.get("DL2_DECAY_MODEL", os.path.join(HERE, "decay_model.json"))
MIN_PAIRS = 10                       # clean pairs (known day gap) needed to fit


# --------------------------------------------------------------------------- #
# Exact-model spike-decay EV (the dl2model wiring).
#
# The `dl2 decay` collector above FITS a percentage curve from realized trades;
# it stays, now as a cross-check. The valuation the live bot actually uses is the
# decompiled market's exact rule: a price drifts toward its hidden target by
# rand()%gap each ordinary day, and rand() maxes at 32767, so a big spike sheds
# at most $32,767/day (expected $16,383.50) — LINEAR in dollars, not exponential
# in percent. The old fit under-valued spikes; this does not. These helpers value
# a price you'll REALIZE `n` days from now with that exact model.
#
# HARD RULE (owner): the EV is an honest column ALONGSIDE, never replacing, the
# observed headline price. Callers keep the raw observed price and ADD the EV;
# when a drug/city isn't in the model tables (e.g. an OCR misspelling like
# "Ecstasy"), expected_sell_price returns None and the caller falls back to the
# observed price — nothing is ever dropped.
# --------------------------------------------------------------------------- #

def in_model(drug, city) -> bool:
    """True iff (drug, city) are both known to the exact model (so normal_mean
    won't KeyError). Lets callers add an EV only where it's meaningful."""
    from dl2model import constants as C
    return drug in C.BASE_PRICE and city.split(",")[0] in C.CITY_MULT


def market_target(drug, city, target=None):
    """The hidden target a price decays toward. The true target isn't visible in
    one OCR read, so default to M = normal_mean(drug, city) — the market's normal
    level. Pass `target` once a better estimate exists (future work: infer it
    from successive audit-log readings of the same city/drug)."""
    return normal_mean(drug, city.split(",")[0]) if target is None else target


def ev_after_days(observed_price, target, n):
    """prices.expected_price_after_days generalized to a fractional `n`. The exact
    model iterates WHOLE days; for a fractional horizon (a shipment's expected
    transit, e.g. 1.25 days) we linearly interpolate between the whole-day
    results. n<=0 -> the observed price unchanged (a sale realized today)."""
    if n <= 0:
        return float(observed_price)
    lo = int(n)
    ev_lo = prices.expected_price_after_days(observed_price, target, lo)
    frac = n - lo
    if frac <= 0:
        return ev_lo
    ev_hi = prices.expected_price_after_days(observed_price, target, lo + 1)
    return ev_lo + frac * (ev_hi - ev_lo)


def sell_horizon_days(ship=False, ship_days=1):
    """Days until you realize a sell: 1 for a flown (carried) trade; else the
    shipment's EXPECTED transit (shipping.expected_transit_days, incl. slip)."""
    if not ship:
        return 1
    return shipping.expected_transit_days(ship_days)


def _rumor_direction(rumor):
    """Pull a 'scarce'/'abundant' direction out of a rumor mapping/object, else
    None (absent/unrecognised rumor -> ordinary decay)."""
    if not rumor:
        return None
    d = rumor.get("direction") if isinstance(rumor, dict) \
        else getattr(rumor, "direction", None)
    return d if d in ("scarce", "abundant") else None


def find_rumor(rumor_list, drug, city):
    """The state rumor matching (drug, city) from a `state['rumors']` list, or
    None. Tolerates short or full city names. Absent list -> None (back-compat)."""
    if not rumor_list:
        return None
    short = (city or "").split(",")[0]
    for r in rumor_list:
        rd = r.get("drug") if isinstance(r, dict) else getattr(r, "drug", None)
        rc = r.get("city") if isinstance(r, dict) else getattr(r, "city", None)
        if rd == drug and (rc == city or (rc or "").split(",")[0] == short):
            return r
    return None


def expected_sell_price(observed_price, drug, city, n=1, target=None, rumor=None):
    """Exact-model EV of the price you'll realize `n` days from now for `drug` at
    `city`. Returned as a column ALONGSIDE `observed_price`, NEVER in place of it
    (owner hard rule). Returns None when (drug, city) aren't in the model tables
    so the caller falls back to the observed price.

    target: hidden market target; defaults to M = normal_mean(drug, city).
    rumor:  optional {"direction": "scarce"|"abundant"} (dict or object). When
            present, tomorrow's price is conditioned on the rumor (rumors.py) —
            the strongest route signal in the game — instead of ordinary decay.
            A rumor is a NEXT-DAY signal, applied to the immediate valuation
            regardless of n. Absent/unrecognised -> ordinary decay."""
    if not in_model(drug, city):
        return None
    short = city.split(",")[0]
    M = normal_mean(drug, short)
    tgt = M if target is None else target
    direction = _rumor_direction(rumor)
    if direction is not None:
        r = rumors.Rumor(drug=drug, city=short, direction=direction)
        return rumors.expected_next_price_given_rumor(observed_price, tgt, M, r)
    return ev_after_days(observed_price, tgt, n)


def _short(city):
    return (city or "").split(",")[0].strip()


def pairs():
    """Walk the audit log in time order, tracking the latest projection per
    (city, drug), and emit one row per `sell` that has a prior projection.
    Row: {drug, city, projected, realized, ratio, days, proj_day, sell_day,
    proj_ts, sell_ts, source}."""
    # only the current game — a new game resets the day and rerandomizes prices,
    # so a fresh sell must not pair with a dead game's projection.
    entries = sorted(audit.latest_segment(), key=lambda e: e.get("ts", ""))
    proj = {}                        # (city_short, drug) -> {price, day, ts, source}
    rows = []
    for e in entries:
        ev = e.get("event")
        if ev == "decision":
            for a in e.get("arb_targets") or []:
                key = (_short(a.get("city")), a.get("drug"))
                if a.get("projected_sell"):
                    proj[key] = {"price": a["projected_sell"], "day": e.get("day"),
                                 "ts": e.get("ts"), "source": "arb"}
            for s in e.get("held_spikes") or []:
                key = (_short(s.get("city")), s.get("drug"))
                if s.get("price"):
                    # keep an arb projection over a held_spike if both exist this
                    # decision (arb is the trade we're actually pricing); else use it
                    if proj.get(key, {}).get("ts") != e.get("ts"):
                        proj[key] = {"price": s["price"], "day": e.get("day"),
                                     "ts": e.get("ts"), "source": "held_spike"}
        elif ev == "sell":
            key = (_short(e.get("city")), e.get("drug"))
            p = proj.get(key)
            realized = e.get("price")
            if not p or not realized:
                continue
            pj = p["price"]
            pd, sd = p.get("day"), e.get("day")
            days = (sd - pd) if (pd is not None and sd is not None) else None
            rows.append({"drug": e.get("drug"), "city": key[0],
                         "projected": pj, "realized": realized,
                         "ratio": (realized / pj if pj else None), "days": days,
                         "proj_day": pd, "sell_day": sd,
                         "proj_ts": p.get("ts"), "sell_ts": e.get("ts"),
                         "source": p.get("source")})
    return rows


def fit_exp(clean):
    """Fit ratio = e^(-k*days) by least squares on ln(ratio) = -k*days (forced
    through ratio=1 at days=0). `clean` = rows with days>0 and ratio>0. Returns
    {model, k, half_life_days, n, r2} or None if it can't fit."""
    pts = [(r["days"], r["ratio"]) for r in clean
           if r["days"] and r["days"] > 0 and r["ratio"] and r["ratio"] > 0]
    if len(pts) < 2:
        return None
    sxx = sum(d * d for d, _ in pts)
    if sxx == 0:
        return None
    k = -sum(d * math.log(rt) for d, rt in pts) / sxx
    # R^2 in ln space against the no-intercept model y = -k*d
    ys = [math.log(rt) for _, rt in pts]
    ybar = sum(ys) / len(ys)
    ss_tot = sum((y - ybar) ** 2 for y in ys)
    ss_res = sum((math.log(rt) - (-k * d)) ** 2 for d, rt in pts)
    r2 = 1 - ss_res / ss_tot if ss_tot else None
    return {"model": "exp", "k": round(k, 5),
            "half_life_days": (round(math.log(2) / k, 3) if k > 0 else None),
            "n": len(pts), "r2": (round(r2, 3) if r2 is not None else None)}


def cmd_decay(as_json=False):
    rows = pairs()
    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        print("(no projection→sell pairs yet — trade and re-run. `dl2 decide` logs")
        print(" projections; `dl2 sell` logs realized prices with the game day.)")
        return

    print("Spike decay: realized vs projected sell price, by (city, drug)")
    print("=" * 78)
    print(f"  {'drug':9s} {'city':13s} {'projected':>11s} {'realized':>11s} "
          f"{'ratio':>6s} {'days':>5s}  src")
    for r in sorted(rows, key=lambda r: r["sell_ts"] or ""):
        days = "?" if r["days"] is None else str(r["days"])
        ratio = f"{r['ratio']*100:.0f}%" if r["ratio"] is not None else "?"
        print(f"  {r['drug'] or '?':9s} {r['city'][:13]:13s} "
              f"${r['projected']:>10,} ${r['realized']:>10,} {ratio:>6s} {days:>5s}"
              f"  {r.get('source','?')}")

    clean = [r for r in rows if r["days"] is not None and r["ratio"] is not None]
    with_gap = [r for r in clean if r["days"] and r["days"] > 0]
    print("-" * 78)
    print(f"{len(rows)} pair(s); {len(clean)} with a known game-day stamp on both "
          f"ends, {len(with_gap)} with a >0 day gap.")

    # ratio distribution bucketed by whole days elapsed
    if clean:
        buckets = {}
        for r in clean:
            buckets.setdefault(r["days"], []).append(r["ratio"])
        print("\n  by days elapsed:   n   mean-ratio   min    max")
        for d in sorted(buckets):
            v = buckets[d]
            print(f"    {d:>3} day(s)      {len(v):>3d}   {sum(v)/len(v)*100:>7.0f}%  "
                  f"{min(v)*100:>4.0f}% {max(v)*100:>4.0f}%")

    if len(with_gap) < MIN_PAIRS:
        print(f"\n⚠ insufficient data to fit a decay model "
              f"({len(with_gap)}/{MIN_PAIRS} pairs with a day gap) — keep trading.")
        print("  (Day gaps need the game 'Day X/Y' to be read at both projection and")
        print("   sale; if it shows '?' above, the Status box day didn't parse.)")
        return

    model = fit_exp(with_gap)
    if not model:
        print("\n(could not fit a curve to the current pairs — keep trading.)")
        return
    model["pairs_used"] = len(with_gap)
    model["fitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        with open(MODEL_PATH, "w") as f:
            json.dump(model, f, indent=2)
    except Exception:
        pass
    hl = f"{model['half_life_days']} days" if model["half_life_days"] else "n/a"
    print(f"\n✓ FIT  ratio ≈ e^(-{model['k']}·days)   (half-life {hl}, "
          f"n={model['n']}, R²={model['r2']})")
    print(f"  wrote {MODEL_PATH} — `dl2 decide` can later show an honest-EV column")
    print(f"  alongside (never replacing) the headline price.")


def main():
    cmd_decay(as_json=("--json" in sys.argv[1:]))


if __name__ == "__main__":
    main()
