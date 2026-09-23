#!/usr/bin/env python3
"""
Append-only audit log of trade DECISIONS and their OUTCOMES, so we can review
what the tool projected vs what actually happened and tune the model (the arb
safety margin, the spike-decay / "will the destination trade it" assumptions,
shipping-cost calls, etc.).

One JSON object per line (JSONL) at ~/code/dl2/audit_log.jsonl (override with
DL2_AUDIT_LOG). Events:
  * "decision" — a `dl2 decide` run: game state + the ranked candidates + the
    recommended move + the spikes it saw. This is the PROJECTION.
  * "buy" / "ship" — an execution step (what was actually bought/shipped).
  * "sell"    — a drug actually sold, with the price and qty realized. Compared
    against the decision that sent you there, this is the OUTCOME.
  * "note"    — freeform annotation you add for later review.

Nothing here drives the game or blocks; logging failures are swallowed so the
audit trail can never break a trade.

CLI:
  python3 audit.py                 # readable timeline (most recent last)
  python3 audit.py --json          # raw entries
  python3 audit.py note "text"     # add a note
  python3 audit.py review          # projection-vs-outcome pairing per city
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_PATH = os.environ.get("DL2_AUDIT_LOG", os.path.join(HERE, "audit_log.jsonl"))


def record(event, **fields):
    """Append one event. Returns the entry (or None on failure — never raises)."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    try:
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        return None
    return entry


def load(path=AUDIT_PATH):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- #
# viewing
# --------------------------------------------------------------------------- #
def _fmt_decision(e):
    day = f" Day {e['day']}" if e.get("day") else ""
    inv = e.get("inventory") or {}
    inv_s = ", ".join(f"{q:,} {d}" for d, q in inv.items()) or "—"
    lines = [f"  at {e.get('city','?')}{day}  |  inventory: {inv_s}",
             f"  arb-safety {e.get('safety','?')}; recommended -> "
             f"{e.get('recommend','?')}  (~${e.get('recommend_value',0):,.0f})"]
    for c in (e.get("candidates") or [])[:5]:
        lines.append(f"      {c['city']:16s} combined ${c['combined']:>14,.0f} "
                     f"(inv ${c['inv_net']:>13,.0f} + arb ${c['arb_net']:>13,.0f})")
    for s in (e.get("held_spikes") or []):
        lines.append(f"      SPIKE  {s['drug']} @ {s['city']} ${s['price']:,} "
                     f"({s['mult']:.0f}x median)")
    return "\n".join(lines)


def _fmt_sell(e):
    price = e.get("price")
    qty = e.get("qty")
    rev = (price or 0) * (qty or 0) if price and qty else e.get("revenue")
    bits = [f"SOLD {e.get('drug','?')}"]
    if qty:
        bits.append(f"{qty:,} u")
    if price:
        bits.append(f"@ ${price:,}")
    if rev:
        bits.append(f"= ${rev:,.0f}")
    return f"  at {e.get('city','?')}: " + " ".join(bits)


def cmd_timeline(as_json=False):
    entries = load()
    if as_json:
        print(json.dumps(entries, indent=2))
        return
    if not entries:
        print(f"(no audit entries yet at {AUDIT_PATH})")
        return
    for e in entries:
        head = f"[{e.get('ts','?')}] {e.get('event','?').upper()}"
        if e["event"] == "decision":
            print(head + "\n" + _fmt_decision(e))
        elif e["event"] == "sell":
            print(head + "\n" + _fmt_sell(e))
        elif e["event"] in ("buy", "ship"):
            q = e.get("qty")
            qs = f"x {q:,}" if isinstance(q, (int, float)) else (f"x {q}" if q else "")
            price = f"@ ${e['price']:,}" if e.get("price") else ""
            dest = f"-> {e['dest']}" if e.get("dest") else ""
            print(f"{head}  {e.get('drug','?')} {qs} {price} {dest}".rstrip())
        elif e["event"] == "note":
            print(f"{head}  {e.get('text','')}")
        else:
            print(head + "  " + json.dumps({k: v for k, v in e.items()
                                            if k not in ("ts", "event")}))
    print(f"\n{len(entries)} entries — {AUDIT_PATH}")


def cmd_review():
    """Pair each decision's projection with the sells that followed it, per drug,
    so you can see how the realized price compared to what was projected."""
    entries = load()
    decisions = [e for e in entries if e["event"] == "decision"]
    sells = [e for e in entries if e["event"] == "sell"]
    if not decisions:
        print("(no decisions logged yet)")
        return
    for i, dec in enumerate(decisions):
        nxt_ts = decisions[i + 1]["ts"] if i + 1 < len(decisions) else "9999"
        following = [s for s in sells if dec["ts"] <= s["ts"] < nxt_ts]
        proj = {s["drug"]: s["price"] for s in dec.get("held_spikes", [])}
        # also index every projected price by (city,drug) from candidates isn't
        # stored per-drug; held_spikes is the useful projection for held drugs.
        print(f"\n[{dec['ts']}] decided at {dec.get('city')} -> "
              f"{dec.get('recommend')} (~${dec.get('recommend_value',0):,.0f})")
        if not following:
            print("   (no sells recorded yet for this decision)")
            continue
        for s in following:
            d = s.get("drug")
            pj = proj.get(d)
            got = s.get("price")
            delta = ""
            if pj and got:
                delta = f"  ({got/pj*100:.0f}% of projected ${pj:,})"
            print(f"   sold {d} @ ${got:,}{delta}" if got else f"   sold {d}")


def latest_segment(entries=None):
    """The entries belonging to the CURRENT game — everything at/after the last
    'newgame' marker. The game is a 55-day cycle; a new game resets the day and
    rerandomizes prices, so projections/sells/positions must not pair across
    games. Returns all entries when no marker exists."""
    entries = load() if entries is None else entries
    last = max((i for i, e in enumerate(entries) if e.get("event") == "newgame"),
               default=None)
    return entries if last is None else entries[last + 1:]


def main():
    a = sys.argv[1:]
    if a and a[0] == "note":
        record("note", text=" ".join(a[1:]))
        print("noted.")
    elif a and a[0] == "newgame":
        record("newgame", text=" ".join(a[1:]) or None)
        print("new game marked — decay/positions now start fresh from here.")
    elif a and a[0] == "review":
        cmd_review()
    else:
        cmd_timeline(as_json=("--json" in a))


if __name__ == "__main__":
    main()
