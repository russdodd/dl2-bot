#!/usr/bin/env python3
"""
dl2 — read the live Drug Lord 2 window and print trade advice.

v1 (no extra permissions, no clicking): from a single capture it reports
  * the current city's market with the game's own buy/sell arrows, and
  * for the drug currently selected in the World Prices window, the best city
    to fly to and sell it, with the per-unit spread.

Full cross-drug optimization (sweeping every drug) is the v2 add-on and needs
the Accessibility permission to drive the drug list; see README.

Usage: python3 dl2.py            (one reading)
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.join(HERE, "bin", "dl2read")
PNG = "/tmp/dl2_last.png"
sys.path.insert(0, HERE)
import parse as parser
try:
    import digits                       # exact fixed-font digit reader (needs numpy)
except Exception:                       # pragma: no cover - degrade to Vision text
    digits = None


def capture():
    out = subprocess.run([READER, "Drug Lord", "--save", PNG],
                         capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"capture failed: {out.stderr.strip()}")
    ocr = json.loads(out.stdout)
    if digits is not None:
        try:
            digits.refine_numbers(ocr, PNG)   # exact fixed-font digit reads
        except Exception:
            pass
    return ocr


def detect_arrows(ocr):
    """Sample the arrow glyph colour at the left of each Market row.
    Green up-arrow => price is high (sell); red down-arrow => low (buy)."""
    try:
        from PIL import Image
    except ImportError:
        return {}
    img = Image.open(PNG).convert("RGB")
    W, H = img.size
    # Market name column boxes give us each row's y; arrow sits just left of x~40.
    signals = {}
    market_name_boxes = [b for b in ocr["boxes"] if b["x"] < 160 and b["y"] > 330
                         and parser._num(b["text"]) is None]
    for b in market_name_boxes:
        drug = parser._clean_drug(b["text"])
        if not drug:
            continue
        cx0, cx1 = 12, 44
        cy = b["y"] + b["h"] // 2
        reds = greens = 0
        for x in range(cx0, min(cx1, W)):
            for y in range(max(0, cy - 8), min(H, cy + 8)):
                r, g, bl = img.getpixel((x, y))
                if r > 120 and r > g + 40 and r > bl + 40:
                    reds += 1
                elif g > 100 and g > r + 30 and g > bl + 30:
                    greens += 1
        if greens > 8 and greens >= reds:
            signals[drug] = "up"      # expensive here -> good to SELL
        elif reds > 8:
            signals[drug] = "down"    # cheap here -> good to BUY
        else:
            signals[drug] = "flat"
    return signals


def report(state, arrows):
    city = state["current_city"] or "?"
    L = []
    L.append(f"=== Drug Lord 2 — you are in {city} ===\n")

    # Selected-drug arbitrage (the one shown in World Prices)
    sel = state["selected_drug"]
    cps = state["city_prices"]
    if sel and cps:
        here = state["market"].get(sel, {}).get("price")
        ranked = sorted(cps.items(), key=lambda kv: kv[1]["price"], reverse=True)
        best_city, best = ranked[0]
        L.append(f"World Prices — {sel}:")
        if here:
            spread = best["price"] - here
            L.append(f"  Buy in {city} @ ${here:,} → sell in {best_city} @ "
                     f"${best['price']:,}  =  +${spread:,}/unit")
        else:
            L.append(f"  Highest price: {best_city} @ ${best['price']:,} "
                     f"(not sold in {city})")
        cheapest_city, cheap = ranked[-1]
        L.append(f"  (cheapest: {cheapest_city} @ ${cheap['price']:,})")
        L.append("")

    # Current-city signals from the arrows
    buys = [d for d, s in arrows.items() if s == "down"]
    sells = [d for d, s in arrows.items() if s == "up"]
    if buys:
        L.append("Cheap here now (BUY candidates, price below average):")
        for d in buys:
            L.append(f"  - {d} @ ${state['market'][d]['price']:,}")
    if sells:
        L.append("\nExpensive here now (SELL if you hold, price above average):")
        for d in sells:
            L.append(f"  - {d} @ ${state['market'][d]['price']:,}")

    L.append("\n(Tip: v1 optimizes the drug open in World Prices. For the full "
             "'best drug + best city' across everything, run the sweep — see README.)")
    return "\n".join(L)


def once():
    ocr = capture()
    state = parser.parse(ocr)
    arrows = detect_arrows(ocr)
    return report(state, arrows), state


def main():
    args = sys.argv[1:]
    if "--json" in args:
        print(json.dumps(parser.parse(capture()), indent=2))
        return
    if "--watch" in args:
        import time
        interval = 2.0
        last = None
        try:
            while True:
                text, _ = once()
                if text != last:
                    os.system("clear")
                    print(text)
                    print("\n[watching — Ctrl-C to stop]")
                    last = text
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped.")
        return
    text, _ = once()
    print(text)


if __name__ == "__main__":
    main()
