#!/usr/bin/env python3
"""Persistent origin->dest shipping-rate store, so `dl2 decide` stops
re-measuring courier rates on every run (measuring drives the screen and is the
slowest part of a decide). Rates are a flat $/unit, distance-based and
drug-independent; keyed by SHORT city name (parse.CITY_SHORT), e.g. "Toronto",
"St Petersburg".

Store file (~/code/dl2/ship_rates.json, override with DL2_SHIP_RATES):
    { "<origin>": { "<dest>": {"rate": 1068.0, "day": 34, "ts": "..."} } }

Open question this store is built to help answer: does the SAME origin->dest
rate change across game days? It clearly varies by origin (distance), but
day-to-day stability is unverified. So freshness is conservative by default
(fresh only on the same game day, or — when the game day is unknown for either
side — within DL2_RATE_MAXAGE_HOURS of wall-clock), and `dl2 decide` logs a
`rate_check` audit event whenever it re-measures a dest it already had cached,
so we can learn whether rates actually move. If they turn out stable, relax
DL2_RATE_MAXAGE_HOURS upward (or set a same-game-day-forever policy).

Nothing here raises: a store hiccup must never break a live trade.
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RATES_PATH = os.environ.get("DL2_SHIP_RATES", os.path.join(HERE, "ship_rates.json"))
# When we can't compare game days, treat a cached rate as fresh only if it was
# measured within this many wall-clock hours (a play session rarely spans more
# than a few game days, and repeated decides at one city minutes apart are the
# waste we most want to skip). Bump this once rates are shown to be stable.
MAXAGE_HOURS = float(os.environ.get("DL2_RATE_MAXAGE_HOURS", "18"))


def load_rates(path=RATES_PATH):
    """The whole store as {origin: {dest: entry}}. Missing/corrupt -> {}."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_rates(rates, path=RATES_PATH):
    """Atomic write (tmp + replace) so a crash mid-write can't truncate the store.
    Returns True on success, False on any failure (never raises)."""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rates, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def get_rate(origin, dest, rates=None):
    """The stored entry {rate, day, ts} for origin->dest, or None."""
    try:
        r = load_rates() if rates is None else rates
        return (r.get(origin) or {}).get(dest)
    except Exception:
        return None


def put_rate(origin, dest, rate, day=None, rates=None, save=True):
    """Merge one measured rate into the store. Pass an in-memory `rates` dict to
    batch several puts, then let the last one `save`. Returns the store dict."""
    try:
        r = load_rates() if rates is None else rates
        r.setdefault(origin, {})[dest] = {
            "rate": round(float(rate), 2), "day": day,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if save:
            save_rates(r)
        return r
    except Exception:
        return rates if rates is not None else {}


def _entry_epoch(entry):
    try:
        return time.mktime(time.strptime(entry["ts"], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def is_fresh(entry, current_day=None, now=None):
    """Fresh iff measured on the current game day (when both days are known), or
    — when either game day is unknown — within MAXAGE_HOURS of now. Conservative
    by design; see the module docstring."""
    if not entry:
        return False
    ed = entry.get("day")
    if current_day is not None and ed is not None:
        return ed == current_day
    now = time.time() if now is None else now
    return (now - _entry_epoch(entry)) <= MAXAGE_HOURS * 3600


def fresh_rates_for(origin, dests, current_day=None, rates=None):
    """Split `dests` by what we already know. Returns (have, missing):
    have = {dest: rate} for dests with a FRESH cached rate; missing = the rest
    (need live measurement)."""
    r = load_rates() if rates is None else rates
    have, missing = {}, []
    for d in dests:
        e = (r.get(origin) or {}).get(d)
        if is_fresh(e, current_day):
            have[d] = e["rate"]
        else:
            missing.append(d)
    return have, missing


# --------------------------------------------------------------------------- #
# `dl2 shiprates` — pretty-print the store (pure file read, no screen)
# --------------------------------------------------------------------------- #
def cmd_show(as_json=False):
    import parse as parser
    rates = load_rates()
    if as_json:
        print(json.dumps(rates, indent=2, sort_keys=True))
        return
    if not rates:
        print(f"(no shipping rates stored yet at {RATES_PATH})")
        print("Run `dl2 decide` from a city to start filling the store.")
        return
    origins = sorted(rates)
    dests = sorted({d for o in rates.values() for d in o})
    now = time.time()
    wcol = max(14, max((len(d) for d in dests), default=14) + 1)
    # matrix: origin rows x dest cols, rate/unit; "." = unknown, "~" prefix = stale
    print(f"Shipping rates $/unit  (rows = origin, cols = destination)   "
          f"[{RATES_PATH}]")
    print("  " + "".join(f"{d[:wcol-1]:>{wcol}}" for d in dests))
    for o in origins:
        cells = []
        for d in dests:
            e = rates[o].get(d)
            if not e:
                cells.append(f"{'.':>{wcol}}")
            else:
                stale = "" if is_fresh(e, None, now) else "~"
                cells.append(f"{stale + format(e['rate'], ',.0f'):>{wcol}}")
        print(f"{o[:14]:<14}" + "".join(cells))
    # gaps: destinations each origin is still missing (14 dests = all other cities)
    all_dests = [c for c in parser.CITY_SHORT]
    print("\nCoverage (measured dests per origin; . = never measured, ~ = stale):")
    for o in origins:
        known = {d for d in rates[o]}
        fresh = {d for d in rates[o] if is_fresh(rates[o][d], None, now)}
        miss = [c for c in all_dests if c != o and c not in known]
        stale = sorted(known - fresh)
        line = f"  {o:<14} {len(fresh)}/{len(all_dests)-1} fresh"
        if stale:
            line += f"; stale: {', '.join(stale)}"
        if miss:
            line += f"; never: {', '.join(miss)}"
        print(line)


def main():
    import sys
    cmd_show(as_json=("--json" in sys.argv[1:]))


if __name__ == "__main__":
    main()
