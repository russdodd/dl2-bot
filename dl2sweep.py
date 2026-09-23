#!/usr/bin/env python3
"""
dl2sweep — build the FULL world price matrix and print the optimal plan.

Clicks each drug in the World Prices "Drug Name" list and reads its City List,
assembling prices[city][drug], then runs optimizer.py.

Needs: Screen Recording (capture) + Accessibility (to post clicks) for Terminal.
Keep the World Prices window frontmost & unobstructed during the ~15s sweep.

Usage: python3 dl2sweep.py --cash 20000 [--capacity 100]
"""
import json
import os
import subprocess
import sys
import time
from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.join(HERE, "bin", "dl2read")
INPUT = os.path.join(HERE, "bin", "dl2input")
WINDOWS = os.path.join(HERE, "bin", "dl2windows")
sys.path.insert(0, HERE)
import parse as parser
import optimizer
import audit                            # append-only decision/outcome trail
import shiprates                        # persistent origin->dest courier-rate store
import decay                            # exact-model spike-decay EV helpers
from dl2model import shipping as _shipping   # exact shipping cost + failure EV
from dl2model import stock as _stock         # remote-stock estimate
from dl2model import finance as _finance     # rank -> capacity
from dl2model import risk as _risk           # carry-vs-ship channel advisory
from dl2model import constants as _C
try:
    import digits                       # exact fixed-font digit reader (needs numpy)
except Exception:                       # pragma: no cover - degrade to Vision text
    digits = None

# Wine renders the window to an off-screen surface, so capture pixels don't map to
# screen coordinates by a simple scale. We calibrate the affine map each run by
# moving the cursor to two known screen points and finding it in the capture.
BASE_PNG = "/tmp/dl2_base.png"
CUR_PNG = "/tmp/dl2_cur.png"

# Drug Name list order (alphabetical, fixed) — the order Down walks through.
SWEEP_DRUGS = ["Cocaine", "Crack", "Ecstacy", "Hashish", "Heroin", "Ice", "Kat",
               "LSD", "MDA", "Morphine", "Mushrooms", "Opium", "PCP", "Peyote",
               "Pot", "Special K", "Speed"]


DIGIT_PNG = "/tmp/dl2_digits.png"   # scratch capture used for exact digit reads


def _read(cursor=False, save=None, window="World"):
    # Always keep a PNG of this capture so the digit-template reader can replace
    # Vision's numeric guesses with exact reads (fixed-font template match).
    png = save or DIGIT_PNG
    cmd = [READER, window, "--save", png]
    if cursor:
        cmd.append("--cursor")
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        return None
    ocr = json.loads(out.stdout)
    if digits is not None:
        try:
            digits.refine_numbers(ocr, png)
        except Exception:
            pass                # never let digit-reading break a capture
    st = parser.parse(ocr)
    st["_ocr"] = ocr
    return st


def _cursor_tip(base_png, cur_png):
    a = Image.open(base_png).convert("RGB")
    b = Image.open(cur_png).convert("RGB")
    diff = ImageChops.difference(a, b).convert("L")
    W, H = diff.size
    px = diff.load()
    pts = [(x, y) for y in range(0, H, 2) for x in range(0, W, 2) if px[x, y] > 55]
    if not pts:
        return None
    ys = min(p[1] for p in pts)
    xs = min(p[0] for p in pts if p[1] <= ys + 6)
    return (xs, ys)


def calibrate(fr, window="World", tries=3):
    """Move the cursor to two known screen points; find it in the capture; solve the
    affine map so we can turn any capture-pixel into the screen point to click.

    Retries: a one-off cursor-detection miss can put both probes at the same image
    point, giving a zero scale that later divides-by-zero mid-drive. We validate the
    solved scale is non-degenerate and retry, rather than return a poisoned map."""
    probes = [(fr["x"] + 80, fr["y"] + 100), (fr["x"] + fr["w"] - 100, fr["y"] + fr["h"] - 60)]
    for _ in range(tries):
        _read(save=BASE_PNG, window=window)
        tips, ok = [], True
        for (sx, sy) in probes:
            subprocess.run([INPUT, "move", str(sx), str(sy)], capture_output=True)
            time.sleep(0.25)
            _read(cursor=True, save=CUR_PNG, window=window)
            tip = _cursor_tip(BASE_PNG, CUR_PNG)
            if tip is None:
                ok = False; break
            tips.append(((sx, sy), tip))
        if not ok or len(tips) < 2:
            time.sleep(0.3); continue
        (s1, t1), (s2, t2) = tips
        if s2[0] == s1[0] or s2[1] == s1[1]:
            continue
        mx = (t2[0] - t1[0]) / (s2[0] - s1[0]); cx = t1[0] - mx * s1[0]
        my = (t2[1] - t1[1]) / (s2[1] - s1[1]); cy = t1[1] - my * s1[1]
        if abs(mx) > 0.1 and abs(my) > 0.1:      # non-degenerate -> good
            return (mx, cx, my, cy)
        time.sleep(0.3)                          # cursor mis-located -> retry
    sys.exit("calibration failed after retries: couldn't reliably locate the cursor. "
             "Make sure the game window is frontmost, unobscured, and hands are off, "
             "then re-run.")


def screen_of(img_x, img_y, cal):
    mx, cx, my, cy = cal
    return int((img_x - cx) / mx), int((img_y - cy) / my)


def window_capture(tries=20):
    """A capture that just needs the world list present (not a populated right panel),
    so we can focus + Home even if the window opened with nothing selected."""
    for _ in range(tries):
        st = _read()
        if st and any(b["text"].strip() in ("Drug Name", "City Name")
                      for b in st["_ocr"]["boxes"]):
            return st
        time.sleep(0.07)
    return None


def _key(rp):
    return tuple(sorted((c, v["price"]) for c, v in rp.items()))


def clean_capture(tries=20):
    """Return a capture only once two consecutive reads agree (rejects blank/partial)."""
    prev = None
    for _ in range(tries):
        st = _read()
        if st and st["right_prices"]:
            key = _key(st["right_prices"])
            if prev is not None and key == prev[0]:
                return st
            prev = (key, st)
        time.sleep(0.07)
    return prev[1] if prev else None


def capture_distinct(prev_key, budget=60, resend=None, reads=3):
    """Collect several FRESH reads of the right panel (differing from the previous
    item, so we know the Down registered) and return a CONSENSUS: each drug's price/qty
    is the majority value across the reads, so a one-off OCR misread gets outvoted."""
    from collections import Counter
    samples, last = [], None
    for attempt in range(budget):
        st = _read()
        if st and st["right_prices"]:
            k = _key(st["right_prices"])
            if k != prev_key:
                samples.append(st["right_prices"]); last = st
                # stop once we have `reads` and the top value for every drug is agreed
                if len(samples) >= reads:
                    break
        if resend and attempt == budget // 2 and not samples:
            resend(); time.sleep(0.15)             # the Down probably didn't land
        time.sleep(0.05)
    if not samples:
        return None, prev_key
    names = set().union(*[set(r) for r in samples])
    merged = {}
    for name in names:
        prices = [r[name]["price"] for r in samples if name in r]
        qtys = [r[name]["qty"] for r in samples if name in r and r[name].get("qty") is not None]
        mp = Counter(prices).most_common(1)[0][0]
        # Preserve the digit-reader confidence through the consensus: the merged
        # price is "confident" if the fixed-font reader confidently read it in a
        # sample that agreed on the winning value. (Without this the flag would
        # default False and the outlier filter would distrust every price.)
        price_ok = any(r[name].get("price_ok") for r in samples
                       if name in r and r[name]["price"] == mp)
        merged[name] = {"price": mp, "price_ok": price_ok,
                        "qty": Counter(qtys).most_common(1)[0][0] if qtys else None}
    last["right_prices"] = merged
    return last, _key(merged)


def first_list_item(ocr, view):
    """Image (x,y) of the TOP item in the left navigation list (a drug in by_drug
    view, a city in by_city view) — used for the single focus click."""
    if view == "by_city":
        list_hdr = next(b["x"] for b in ocr["boxes"] if b["text"].strip() == "City Name")
        right_hdr = next(b["x"] for b in ocr["boxes"] if b["text"].strip() == "Drug")
        clean = parser._clean_city
    else:
        list_hdr = next(b["x"] for b in ocr["boxes"] if b["text"].strip() == "Drug Name")
        right_hdr = next(b["x"] for b in ocr["boxes"] if b["text"].strip() == "City")
        clean = parser._clean_drug
    items = []
    for b in ocr["boxes"]:
        if list_hdr - 12 < b["x"] < right_hdr - 20 and clean(b["text"]):
            items.append((b["y"], b["x"] + b.get("w", 40) / 2, b["y"] + b.get("h", 12) / 2))
    items.sort()
    top = items[0]
    return top[1], top[2]


def _left_list_rows(ocr, view):
    """Every row in the left navigation list: (canonical_name, cx, cy, box) in
    IMAGE coords. The list shows all cities/drugs at once (no scroll), so we can
    click any item directly instead of arrow-walking."""
    if view == "by_city":
        list_hdr = next((b["x"] for b in ocr["boxes"] if b["text"].strip() == "City Name"), None)
        right_hdr = next((b["x"] for b in ocr["boxes"] if b["text"].strip() == "Drug"), None)
        clean = parser._clean_city
    else:
        list_hdr = next((b["x"] for b in ocr["boxes"] if b["text"].strip() == "Drug Name"), None)
        right_hdr = next((b["x"] for b in ocr["boxes"] if b["text"].strip() == "City"), None)
        clean = parser._clean_drug
    if list_hdr is None or right_hdr is None:
        return []
    rows = []
    for b in ocr["boxes"]:
        if list_hdr - 12 < b["x"] < right_hdr - 20:
            name = clean(b["text"])
            if name:
                rows.append((name, b["x"] + b.get("w", 40) / 2,
                             b["y"] + b.get("h", 12) / 2, b))
    return rows


def _highlighted_item(png_path, ocr, view):
    """Which left-list row is SELECTED, read from the selection bar. The selected
    row is painted with a blue bar BEHIND the text (spanning the whole name column);
    the rest are on a plain light-gray background. So we count blue pixels across
    each row's name-column width and return the row with by far the most. This makes
    each capture self-labeling: we know the ACTUAL selected item, so a dropped
    keystroke can be detected and can never silently mislabel a city's prices.
    Returns None when no row is blue (e.g. the window is unfocused / bar not drawn)."""
    rows = _left_list_rows(ocr, view)
    if not rows:
        return None
    if view == "by_city":
        lh = next((b["x"] for b in ocr["boxes"] if b["text"].strip() == "City Name"), None)
        rh = next((b["x"] for b in ocr["boxes"] if b["text"].strip() == "Drug"), None)
    else:
        lh = next((b["x"] for b in ocr["boxes"] if b["text"].strip() == "Drug Name"), None)
        rh = next((b["x"] for b in ocr["boxes"] if b["text"].strip() == "City"), None)
    if lh is None or rh is None:
        return None
    try:
        img = Image.open(png_path).convert("RGB")
    except Exception:
        return None
    px, (W, H) = img.load(), img.size
    x0, x1 = max(0, int(lh + 2)), min(W, int(rh - 25))

    def blue_count(y):
        if not (0 <= y < H):
            return 0
        return sum(1 for x in range(x0, x1)
                   if px[x, y][2] > px[x, y][0] + 25 and px[x, y][2] > px[x, y][1] + 15
                   and px[x, y][2] > 110)

    best, best_blue = None, 0
    for name, cx, cy, b in rows:
        yc = int(b["y"] + b.get("h", 12) / 2)
        blue = max(blue_count(yc), blue_count(yc - 4), blue_count(yc + 4))
        if blue > best_blue:
            best_blue, best = blue, name
    return best if best_blue >= 30 else None


def recover_right_prices(ocr, png_path, view):
    """Read EVERY row of the right panel's Qty/Price by fixed GEOMETRY, independent
    of whether Vision boxed each cell. The panel always lists the full fixed set in
    fixed alphabetical order on evenly-spaced rows, so we fit the row positions from
    the name boxes we can read (least-squares over each name's known index) and
    digit-read the Qty and Price cell at every row. This is how a cell Vision never
    even detected (the Austin/Ice bug) still gets read. Returns
    {canonical_name: {"qty": int|None, "price": int|None}} for the full list, or
    None if the panel headers can't be located."""
    if digits is None:
        return None
    boxes = ocr["boxes"]
    # The RIGHT panel is drugs in World-Cities view (header "Drug") and cities in
    # World-Prices view (header "City").
    if view == "by_city":
        order, clean, name_hdr = parser.CANON_DRUGS, parser._clean_drug, "Drug"
    else:
        order, clean, name_hdr = parser.CANON_CITIES, parser._clean_city, "City"
    dh = next((b["x"] for b in boxes if b["text"].strip() == name_hdr), None)
    if dh is None:
        return None
    qh = next((b["x"] for b in boxes if b["text"].strip() in ("Qty", "Bty", "Oty") and b["x"] > dh), None)
    ph = next((b for b in boxes if b["text"].strip() == "Price" and b["x"] > dh), None)
    if ph is None:
        return None
    pw = ph.get("w", 40)
    # Column x-bands (right-aligned numbers). Qty is optional.
    price_band = (int(ph["x"] - 52), int(ph["x"] + pw + 8))
    qty_band = (int(qh - 48), int(qh + 40)) if qh else None
    # Fit each visible name row to its fixed index: y ≈ y0 + idx*dy.
    pts = []
    for b in boxes:
        if dh - 15 <= b["x"] < dh + 140:
            nm = clean(b["text"])
            if nm in order:
                pts.append((order.index(nm), b["y"], b.get("h", 16)))
    if len(pts) < 3:
        return None
    import statistics
    n = len(pts)
    sx = sum(p[0] for p in pts); sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts); sxy = sum(p[0] * p[1] for p in pts)
    den = n * sxx - sx * sx
    if den == 0:
        return None
    dy = (n * sxy - sx * sy) / den
    y0 = (sy - dy * sx) / n
    if not (10 <= dy <= 40):                     # implausible spacing -> don't trust the fit
        return None
    h = int(statistics.median(p[2] for p in pts)) or 16
    reader = digits.get_reader()
    if not reader.ready:
        return None
    gray = digits.load_gray(png_path)
    out = {}
    for idx, name in enumerate(order):
        y = int(round(y0 + idx * dy))
        box = {"x": price_band[0], "y": y - 2, "w": price_band[1] - price_band[0], "h": h + 4}
        price = reader.read_number(gray, box)
        qty = None
        if qty_band:
            qbox = {"x": qty_band[0], "y": y - 2, "w": qty_band[1] - qty_band[0], "h": h + 4}
            qty = reader.read_number(gray, qbox)
        out[name] = {"qty": qty, "price": price}
    return out


def recover_market_prices(ocr, png_path):
    """Read The Market (current city, the BUY side) Qty/Price by geometry for every
    drug name present. Vision sometimes fails to box a market price (e.g. a crashed
    $17,240 that came through as "$1" and poisoned the whole arb ranking); reading
    the price column directly at each drug's row fixes that. The buyable set varies,
    so we read at each visible name row (no fixed list). Returns {drug: {qty, price}}."""
    if digits is None:
        return None
    boxes = ocr["boxes"]
    nh = next((b for b in boxes if b["text"].strip() == "Name" and b["x"] < 400), None)
    qh = next((b for b in boxes if b["text"].strip() in ("Qty", "Bty", "Oty") and b["x"] < 400), None)
    ph = next((b for b in boxes if b["text"].strip() == "Price" and b["x"] < 400), None)
    if ph is None or nh is None:
        return None
    price_band = (int(ph["x"] - 52), int(ph["x"] + ph.get("w", 40) + 8))
    qty_band = (int(qh["x"] - 48), int(qh["x"] + 40)) if qh else None
    reader = digits.get_reader()
    if not reader.ready:
        return None
    gray = digits.load_gray(png_path)
    out = {}
    for b in boxes:
        if b["x"] < 170:
            d = parser._clean_drug(b["text"])
            if d and d not in out:
                y, h = b["y"], b.get("h", 16)
                pbox = {"x": price_band[0], "y": y - 2, "w": price_band[1] - price_band[0], "h": h + 4}
                price = reader.read_number(gray, pbox)
                qty = None
                if qty_band:
                    qbox = {"x": qty_band[0], "y": y - 2, "w": qty_band[1] - qty_band[0], "h": h + 4}
                    qty = reader.read_number(gray, qbox)
                out[d] = {"qty": qty, "price": price}
    return out


def click(x, y):
    r = subprocess.run([INPUT, "click", str(x), str(y)], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"click failed: {r.stderr.strip()} — is Accessibility granted to Terminal?")


def sweep():
    st0 = window_capture()
    if not st0:
        sys.exit("could not read the world window — is it open and visible?")
    view = st0["view"]
    order = list(parser.CANON_CITIES) if view == "by_city" else list(SWEEP_DRUGS)
    axis = "city" if view == "by_city" else "drug"
    fr = st0["_ocr"]["frame"]
    # Bring the world window forward WITHOUT activating the CrossOver app (which can
    # surface its launcher). Click its far-right title bar.
    click(fr["x"] + fr["w"] - 30, fr["y"] + 10)
    time.sleep(0.35)
    cal = calibrate(fr)

    # One calibrated click focuses the left list on the top item; Home to be sure
    # we're at the top. Then WALK the list with the Down arrow — but LABEL each stop
    # by the city the highlight actually shows (self-labeling), not by a step counter.
    # That's the fix: a dropped Down just means the highlight didn't move, which we
    # detect and re-press; it can never mislabel a city's prices (the old drift bug).
    fix, fiy = first_list_item(st0["_ocr"], view)
    click(*screen_of(fix, fiy, cal)); time.sleep(0.3)
    subprocess.run([INPUT, "key", "115"], capture_output=True); time.sleep(0.3)   # Home
    # Park the cursor off the list so it can't hover/obscure a row (captures hide the
    # cursor, but a hover can still tint a row and skew the highlight read).
    subprocess.run([INPUT, "move", str(fr["x"] + fr["w"] - 20), str(fr["y"] + fr["h"] - 20)],
                   capture_output=True); time.sleep(0.15)

    def _down():
        subprocess.run([INPUT, "key", "125"], capture_output=True); time.sleep(0.22)

    def _refocus():
        # Re-activate the window (title bar) AND re-focus the list (top item), so a
        # lost keyboard focus — which makes the selection bar vanish — is recovered.
        click(fr["x"] + fr["w"] - 30, fr["y"] + 10); time.sleep(0.2)
        click(*screen_of(fix, fiy, cal)); time.sleep(0.2)
        subprocess.run([INPUT, "move", str(fr["x"] + fr["w"] - 20),
                        str(fr["y"] + fr["h"] - 20)], capture_output=True); time.sleep(0.1)

    def _stable_panel(tries=8):
        prev = None
        for _ in range(tries):
            st = _read()
            if st and st["right_prices"]:
                k = _key(st["right_prices"])
                if prev and prev[0] == k:            # two consecutive reads agree
                    return st
                prev = (k, st)
            time.sleep(0.08)
        return prev[1] if prev else None

    market_acc = dict(st0.get("market") or {})       # current city's buyable drugs (union)
    seen = {}                                         # canonical item -> best capture st
    savedir = os.environ.get("DL2_SAVE_CAPS")
    prev_sel = None
    N = len(order)
    steps, MAX_STEPS = 0, N * 4                       # budget absorbs missed/extra keys
    stuck = 0
    while len(seen) < N and steps < MAX_STEPS:
        steps += 1
        cand = _stable_panel()
        sel = _highlighted_item(DIGIT_PNG, cand["_ocr"], view) if cand else None
        if sel is None:                              # no selection bar -> lost focus
            _refocus(); stuck += 1
            if stuck >= 4:
                break
            continue
        stuck = 0
        # Read EVERY row by fixed GEOMETRY and make that the PRIMARY source: the
        # evenly-spaced fitted positions are robust to a single mis-located name box,
        # whereas Vision's box-parse can pin a price to the wrong row (the PCP==Peyote
        # duplicate). So: geometry value wins; box fills only cells geometry couldn't
        # read; agreement -> verified; disagreement -> keep geometry but FLAG it.
        geo = None
        try:
            geo = recover_right_prices(cand["_ocr"], DIGIT_PNG, view)
        except Exception:
            geo = None
        if geo:
            box_rp = cand["right_prices"]
            merged = {}
            for name, g in geo.items():
                gp, gq = g.get("price"), g.get("qty")
                b = box_rp.get(name) or {}
                bp, bq = b.get("price"), b.get("qty")
                if gp is not None and bp is not None:
                    merged[name] = {"price": gp, "qty": gq or bq,
                                    "price_ok": (gp == bp)}   # agree -> verified
                elif gp is not None:
                    merged[name] = {"price": gp, "qty": gq, "price_ok": True, "_geom": True}
                elif bp is not None:
                    merged[name] = {"price": bp, "qty": bq,
                                    "price_ok": bool(b.get("price_ok"))}
            cand["right_prices"] = merged
        n = len(cand["right_prices"])
        # Keep the MOST COMPLETE capture we've seen for this (correctly-labeled) item.
        if sel not in seen or n > len(seen[sel]["right_prices"]):
            seen[sel] = cand
            if savedir and sel in order:             # forensics: save this item's frame
                import shutil
                tag = f"{order.index(sel)+1:02d}_{sel.split(',')[0].replace(' ', '')}"
                try:
                    shutil.copy(DIGIT_PNG, os.path.join(savedir, tag + ".png"))
                    json.dump(cand["_ocr"], open(os.path.join(savedir, tag + ".json"), "w"))
                except Exception:
                    pass
        if sel == prev_sel:
            _down()                                  # highlight didn't move -> nudge
        else:
            prev_sel = sel
            if len(seen) < N:
                _down()                              # advance to the next row

    missing = [c for c in order if c not in seen]
    if missing:
        sys.exit("sweep aborted: could not cleanly read "
                 f"{', '.join(m.split(',')[0] for m in missing)} after walking the list. "
                 "Keep the World Cities window fully visible and frontmost, hands off, "
                 "then re-run — better to stop than emit incomplete prices.")

    matrix, qtys, conf = {}, {}, {}                  # prices, stock, digit confidence
    for idx, item in enumerate(order):
        st = seen[item]
        label = item.split(",")[0] if axis == "city" else item
        for d, info in (st.get("market") or {}).items():   # keep The Market fresh/complete
            market_acc[d] = info
        rp = st["right_prices"]
        if axis == "city":                      # this capture = one city, all drugs
            for drug, info in rp.items():
                matrix.setdefault(item, {})[drug] = info["price"]
                qtys.setdefault(item, {})[drug] = info.get("qty")
                conf.setdefault(item, {})[drug] = info.get("price_ok", False)
        else:                                   # this capture = one drug, all cities
            for city, info in rp.items():
                matrix.setdefault(city, {})[item] = info["price"]
                qtys.setdefault(city, {})[item] = info.get("qty")
                conf.setdefault(city, {})[item] = info.get("price_ok", False)
        print(f"  [{idx+1:2d}/{N}] {label:12s} {len(rp):2d} "
              f"{'drugs' if axis == 'city' else 'cities'} ✓", file=sys.stderr)
    return st0.get("capacity"), matrix, qtys, market_acc, cal, fr, conf


def _find_btn(boxes, keys):
    for b in boxes:
        t = b["text"].lower()
        if len(t) <= 10 and any(k in t for k in keys):
            return b
    return None


def _intl_cost(ocr):
    """Cost on the International Couriers row of the Select-a-shipper table."""
    row = next((b for b in ocr["boxes"] if "International" in b["text"] or "Couriers" in b["text"]), None)
    if not row:
        return None
    nums = [b for b in ocr["boxes"] if abs(b["y"] - row["y"]) <= 8 and b["x"] > 760
            and b["text"].replace(",", "").strip().isdigit()]
    return int(nums[0]["text"].replace(",", "")) if nums else None


def open_shipping():
    """Main window: Places... -> Shipping... (item 4 of 5 in the popup)."""
    for _ in range(3):
        if any("shipping" in w["title"].lower() for w in _game_windows()):
            return True
        base = _read(window="Drug Lord")
        if not base:
            time.sleep(0.2); continue
        mcal = calibrate(base["_ocr"]["frame"], "Drug Lord")
        mx, cx, my, cy = mcal
        places = _find_btn(base["_ocr"]["boxes"], ["places", "laces"])
        if not places:
            continue
        click(int((places["x"] + places.get("w", 40) / 2 - cx) / mx),
              int((places["y"] + places.get("h", 12) / 2 - cy) / my))
        time.sleep(0.6)
        menu = _popup_menu()
        if not menu:
            continue
        b = menu["bounds"]                       # Finances, Shopping, Hospital, Vault, Shipping(=4)
        click(int(b["x"] + b["w"] * 0.5), int(b["y"] + b["h"] * (4 + 0.5) / 5))
        time.sleep(0.9)
    return any("shipping" in w["title"].lower() for w in _game_windows())


def measure_ship_rates(current_short, day=None):
    """In one Shipping session: load a small sample of stock, set Overnight, and read
    the International Couriers cost to EVERY destination -> flat $/unit rate per city.
    Returns {full_city_name: rate}. Needs a little stock as the sample.

    Every rate read is also written into the persistent store (shiprates.json),
    keyed by short origin/dest name and stamped with the game `day`, so future
    decides can skip this slow screen session."""
    if not open_shipping():
        return {}
    ship = _read(window="Shipping")
    cal = calibrate(ship["_ocr"]["frame"], "Shipping")
    mx, cx, my, cy = cal

    def sof(bx):
        return int((bx["x"] + bx.get("w", 40) / 2 - cx) / mx), int((bx["y"] + bx.get("h", 12) / 2 - cy) / my)

    def find(pred):
        return next((b for b in _read(window="Shipping")["_ocr"]["boxes"] if pred(b)), None)

    ocr = ship["_ocr"]
    # 'Your stock' drug names sit in the RIGHTMOST drug-name column (the left column
    # is a different list); match by column position, not a fixed x-band, so it
    # survives the window being a different width.
    _names = [b for b in ocr["boxes"] if b["y"] < 470 and parser._clean_drug(b["text"])]
    _mx = max((b["x"] for b in _names), default=0)
    stock = sorted([b for b in _names if b["x"] > _mx - 60], key=lambda b: b["y"])
    if not stock:
        cancel = find(lambda b: b["text"].strip() == "Cancel")
        if cancel:
            click(*sof(cancel))
        return {}                                # no sample to measure with
    sample = stock[0]
    sq = next((b for b in ocr["boxes"] if abs(b["y"] - sample["y"]) <= 8 and b["x"] > 490
               and b["text"].strip().isdigit()), None)
    sample_qty = int(sq["text"]) if sq else 1
    sx, sy = sof(sample)
    subprocess.run([INPUT, "dclick", str(sx), str(sy)], capture_output=True); time.sleep(0.6)
    subprocess.run([INPUT, "key", "36"], capture_output=True); time.sleep(0.6)   # Return = OK

    over = find(lambda b: "Overnight" in b["text"])
    if over:
        click(*sof(over)); time.sleep(0.4)

    dest_short = [c for c in parser.CITY_SHORT if c != current_short]
    full_of = {c.split(",")[0]: c for c in parser.CANON_CITIES}
    first = find(lambda b: b["text"].strip() == dest_short[0])
    if first:
        click(*sof(first)); time.sleep(0.2)
    subprocess.run([INPUT, "key", "115"], capture_output=True); time.sleep(0.2)   # Home

    rates, prev, store = {}, 0, shiprates.load_rates()
    for i, cs in enumerate(dest_short):
        for _ in range(i - prev):
            subprocess.run([INPUT, "key", "125"], capture_output=True); time.sleep(0.1)
        prev = i
        time.sleep(0.25)
        cost = _intl_cost(_read(window="Shipping")["_ocr"])
        if cost is not None:
            rate = cost / sample_qty
            rates[full_of.get(cs, cs)] = rate
            shiprates.put_rate(current_short, cs, rate, day, rates=store, save=False)
        print(f"    ship {cs:14s} rate/unit ${cost/sample_qty:,.0f}" if cost else f"    ship {cs}: ?",
              file=sys.stderr)
    shiprates.save_rates(store)          # persist the whole session's reads at once

    cancel = find(lambda b: b["text"].strip() == "Cancel")
    if cancel:
        click(*sof(cancel)); time.sleep(0.3)
    return rates


def buy_drug(drug, qty, cal):
    """Buy `drug`: double-click it in The Market. qty=None buys the MAX (the dialog
    pre-fills the largest amount that fits in inventory — just press OK, so it can never
    overflow). A number clears the field and types that specific amount instead."""
    mx, cx, my, cy = cal
    base = _read(window="Drug Lord")
    row = next((b for b in base["_ocr"]["boxes"] if b["x"] < 160
                and parser._clean_drug(b["text"]) == drug), None)
    if not row:
        print(f"    {drug}: not in The Market (can't buy)", file=sys.stderr)
        return False
    x = int((row["x"] + row.get("w", 40) / 2 - cx) / mx)
    y = int((row["y"] + row.get("h", 12) / 2 - cy) / my)
    subprocess.run([INPUT, "dclick", str(x), str(y)], capture_output=True); time.sleep(0.9)
    if qty is not None:
        for _ in range(9):
            subprocess.run([INPUT, "key", "51"], capture_output=True); time.sleep(0.03)   # Backspace
        subprocess.run([INPUT, "num", str(int(qty))], capture_output=True); time.sleep(0.3)
    subprocess.run([INPUT, "key", "36"], capture_output=True); time.sleep(0.9)             # Return = OK
    audit.record("buy", drug=drug, qty=(int(qty) if qty is not None else "max"),
                 price=(base.get("market") or {}).get(drug, {}).get("price"),
                 day=(base.get("status") or {}).get("day"))
    return True


def _inventory_drugs(ocr):
    """Drugs currently held — the right-hand box ('Your island paradise' / other name)."""
    seen = []
    for b in sorted(ocr["boxes"], key=lambda b: b["y"]):
        if b["x"] > 640 and 300 < b["y"] < 700:
            d = parser._clean_drug(b["text"])
            if d and d not in seen:
                seen.append(d)
    return seen


def _inventory_with_qty(ocr):
    """Held drugs -> quantity, read from the right-hand inventory box."""
    rows, inv = {}, {}
    for b in ocr["boxes"]:
        if b["x"] > 640 and 300 < b["y"] < 700:
            rows.setdefault(b["y"] // 18, []).append(b)
    for items in rows.values():
        name = next((parser._clean_drug(b["text"]) for b in items
                     if b["x"] < 830 and parser._clean_drug(b["text"])), None)
        qty = next((int(b["text"].replace(",", "")) for b in items
                    if 900 < b["x"] < 1050 and b["text"].replace(",", "").isdigit()), None)
        if name and qty:
            inv[name] = qty
    return inv


def decide_main():
    """`dl2 decide` — what to do with your inventory, honest about the game's rules.

    REAL MECHANICS (learned the hard way):
      * You can only SELL a held drug where that city's MARKET currently lists it.
        World-Cities prices are REFERENCE — a listed world price does NOT mean the
        local dealer will trade it. We only know the CURRENT city's Market.
      * You cannot see another city's Market until you physically arrive, so every
        travel/ship play is a bet on the destination trading your drug on arrival.
      * Price spikes usually persist 3+ days (occasionally gone overnight), so
        chasing a spike is favorable-EV — not a mirage, and not a guarantee.

    So this reports: (1) what you can sell HERE right now (certain), (2) where your
    held drugs are SPIKING as travel targets, (3) the best city to fly to and
    liquidate everything (potential), and (4) the best buy-here/sell-there arb.
    Travel numbers are POTENTIAL (upside if the spike holds and the dest trades it),
    never presented as certain."""
    if not world_is_open():
        open_world_cities(); time.sleep(0.4)
    capacity, matrix, qtys, market, cal, fr, conf = sweep()
    close_world(cal)
    status = read_status_from_main()
    current = (status.get("location") or "").split(",")[0]
    main_st = _read(window="Drug Lord")
    inv = _inventory_with_qty(main_st["_ocr"])
    total = sum(inv.values())
    # Re-read The Market (the BUY side) by geometry from this clean main-window
    # frame. The sweep's market comes from world-window frames where Vision can
    # miss/garble a price (a crashed $17,240 read as "$1" poisons every arb), so
    # geometry here is authoritative for buy prices.
    try:
        gm = recover_market_prices(main_st["_ocr"], DIGIT_PNG)
    except Exception:
        gm = None
    if gm:
        for d, cell in gm.items():
            if cell.get("price"):
                market[d] = {"price": cell["price"], "qty": cell.get("qty"),
                             "price_ok": True}
    cash = status.get("cash")
    print(f"\nAt {current}. Cash: "
          + (f"${cash:,}" if cash is not None else "?")
          + f". Inventory ({total:,} units): "
          + ", ".join(f"{q:,} {d}" for d, q in inv.items()), file=sys.stderr)

    # Shipping rates: read the persistent store FIRST and only re-measure the
    # slow way if we're missing fresh rates for some destination. combined_
    # destinations wants full-city keys; the store is short-keyed, so convert.
    day = status.get("day")
    full_of = {c.split(",")[0]: c for c in parser.CANON_CITIES}
    dest_short = [c for c in parser.CITY_SHORT if c != current]
    have, missing = shiprates.fresh_rates_for(current, dest_short, day)
    rates = {full_of.get(cs, cs): r for cs, r in have.items()}
    if not missing:
        print(f"  shipping rates: all {len(have)} destinations cached fresh — "
              f"skipping the Shipping window", file=sys.stderr)
    else:
        print(f"  shipping rates: {len(have)} cached fresh, {len(missing)} "
              f"missing/stale — measuring live...", file=sys.stderr)
        # measure_ship_rates needs a unit of stock to sample the courier cost.
        # With an empty inventory (e.g. everything's still in transit) there's
        # nothing to sample. So buy ONE unit of the cheapest Market drug as a
        # probe — but ONLY when we actually have to measure (skip it otherwise).
        if total == 0 and market:
            cheapest = min((d for d in market if market[d].get("price")),
                           key=lambda d: market[d]["price"], default=None)
            if cheapest:
                print(f"  inventory empty — buying 1 {cheapest} @ "
                      f"${market[cheapest]['price']:,} as a shipping-cost probe",
                      file=sys.stderr)
                probe = _read(window="Drug Lord")
                if probe:
                    buy_drug(cheapest, 1, calibrate(probe["_ocr"]["frame"], "Drug Lord"))
                    time.sleep(0.4)
        pre = {cs: (e or {}).get("rate")
               for cs, e in (shiprates.load_rates().get(current) or {}).items()}
        fresh = measure_ship_rates(current, day)   # writes the store, returns full-keyed
        # cached-vs-fresh: log any re-measured dest whose stored rate had differed,
        # so we can learn whether the same origin->dest rate moves day to day.
        checks = [{"dest": full.split(",")[0], "cached": pre[full.split(",")[0]],
                   "fresh": round(fr, 2)}
                  for full, fr in fresh.items()
                  if pre.get(full.split(",")[0]) is not None
                  and abs(pre[full.split(",")[0]] - fr) > 1e-6]
        rates.update(fresh)                        # freshly measured wins over cache
        if checks:
            print(f"  ⚠ {len(checks)} cached rate(s) differed from fresh "
                  f"(logged for the stability study):", file=sys.stderr)
            for c in checks:
                print(f"      {c['dest']:14s} cached ${c['cached']:,.0f} -> "
                      f"fresh ${c['fresh']:,.0f}", file=sys.stderr)
            audit.record("rate_check", origin=current, day=day, changes=checks)
    # (Deliberately do NOT reopen World Cities — the rest is pure computation, and
    # you asked to be left on the main window, not have the world window popped back
    # up at the end.)

    # Exact-model cross-check + fill (dl2model.shipping via shiprates). The measured
    # rate stays authoritative: we (b) FLAG any measured rate that disagrees with the
    # model (logged, never overridden) and (a) FILL a destination we still couldn't
    # measure with the model rate, clearly labelled an estimate.
    model_checks = []
    for full_c, r in list(rates.items()):
        disc = shiprates.rate_discrepancy(current, full_c.split(",")[0], r)
        if disc:
            model_checks.append(disc)
    if model_checks:
        print(f"  ⚠ {len(model_checks)} measured rate(s) differ from the exact model "
              f"(measured kept; logged):", file=sys.stderr)
        for c in model_checks:
            print(f"      {c['dest']:14s} measured ${c['measured']:,.2f}/u vs "
                  f"model ${c['model']:,.2f}/u (Δ${c['delta']:,.2f})", file=sys.stderr)
        audit.record("rate_check", origin=current, day=day, source="model",
                     changes=model_checks)
    still_missing = [c for c in dest_short
                     if full_of.get(c, c) not in rates and c not in rates]
    filled = shiprates.fill_missing_from_model(current, still_missing)
    if filled:
        print(f"  ℹ {len(filled)} rate(s) FILLED from the exact model (estimate, "
              f"not measured): {', '.join(sorted(filled))}", file=sys.stderr)
        for cs, r in filled.items():
            rates.setdefault(full_of.get(cs, cs), r)

    # HARD RULE: never drop a price, never hide uncertainty. We keep EVERY read and
    # loudly surface (a) prices the digit reader could not verify and (b) cities
    # whose drug list came back short (a price row that failed to parse would
    # otherwise vanish silently — the Austin/Ice bug). Nothing is nulled.
    import statistics
    unverified = []
    for city, cp in matrix.items():
        for drug, price in cp.items():
            if price and not conf.get(city, {}).get(drug, False):
                unverified.append({"city": city, "drug": drug, "value": price,
                                   "held": bool(inv.get(drug))})
    if unverified:
        print(f"\n⚠ {len(unverified)} price(s) KEPT but NOT verified by the digit reader "
              f"— double-check, nothing dropped:", file=sys.stderr)
        for u in sorted(unverified, key=lambda u: (not u["held"], u["city"])):
            hold = "  <-- YOU HOLD THIS" if u["held"] else ""
            print(f"    {u['drug']:9s} @ {u['city']:20s} ${u['value']:>10,}{hold}",
                  file=sys.stderr)

    # Every city ALWAYS lists all drugs, so anything below the full count means a
    # price row went unread even after geometry recovery — flag it, never hide it.
    full = len(parser.CANON_DRUGS)
    counts = {c: len([1 for v in cp.values() if v]) for c, cp in matrix.items()}
    short = {c: n for c, n in counts.items() if n < full}
    if short:
        print(f"\n⚠ cities MISSING prices (should be {full} drugs each — a row went "
              f"unread even after geometry recovery):", file=sys.stderr)
        for c, n in sorted(short.items(), key=lambda kv: kv[1]):
            absent = [d for d in parser.CANON_DRUGS if not matrix[c].get(d)]
            print(f"    {c:20s} {n:2d} drugs  (absent/unread: {', '.join(absent)})",
                  file=sys.stderr)
    # Save the buyable Market too (buy prices + per-drug stock) — the planner and
    # affordability checks (and the 2-hop view) can then reason offline without a
    # fresh sweep. (Previously only prices+rates were saved; the Market was lost.)
    json.dump({"current": current, "prices": matrix, "rates": rates,
               "market": market, "unverified": unverified, "short_cities": short},
              open("/tmp/dl2_state.json", "w"))

    # Cross-city median per drug (to spot spikes). Uses the trusted matrix.
    med = {}
    for drug in {d for c in matrix.values() for d in c}:
        vals = [c[drug] for c in matrix.values() if c.get(drug)]
        if vals:
            med[drug] = statistics.median(vals)
    SPIKE = 3.0                        # world price >= SPIKE x median = a spike

    cur_full = next((c for c in matrix if c.split(",")[0] == current), current)

    print("\n" + "=" * 66)
    print(f"DECISION at {current} — inventory {total:,} units")
    print("=" * 66)

    # (1) SELL HERE NOW — certain: only held drugs the CURRENT Market is buying.
    sell_now = sorted(
        ((d, q, market[d]["price"], q * market[d]["price"])
         for d, q in inv.items() if market.get(d) and market[d].get("price")),
        key=lambda t: -t[3])
    sell_now_total = sum(t[3] for t in sell_now)
    print(f"(1) SELL HERE NOW — certain (only what {current}'s Market is buying today):")
    if sell_now:
        for d, q, p, v in sell_now:
            print(f"      {d:9s} {q:>7,} u x ${p:>7,} = ${v:>14,}")
        print(f"      {'sellable here today':>29} = ${sell_now_total:>14,}")
    else:
        print(f"      NOTHING — {current}'s Market isn't buying any of your held drugs")
        print(f"      ({', '.join(inv)}). They're stuck until the Market rotates them")
        print(f"      in here, or you travel to a city that trades them.")

    # (2) COMBINED per-destination ranking: fly to ONE city and, in one turn, sell
    #     your existing inventory THERE *and* whatever you bought in this Market and
    #     brought along. Inventory + arb are not separate plans — you pick one city,
    #     so the optimum is their sum at that city. POTENTIAL: needs the spike to
    #     hold and the destination's Market to trade the drug on arrival.
    # Free plane-carry is capped by INVENTORY SIZE — the total units you can hold
    # (small early game, ~10; grows as you level up). Read it from the main window
    # (the sweep runs with the world window covering the inventory box, so its
    # capacity is unreliable). Fall back to the sweep value, then DEFAULT_CARRY.
    cap = main_st.get("capacity") or capacity
    carry = min(DEFAULT_CARRY, cap) if cap else DEFAULT_CARRY
    if not cap:
        print("  ⚠ couldn't read inventory size — free-carry cap unknown, assuming "
              f"{carry:,}. Overflow-shipping estimate may be off.", file=sys.stderr)
    # Rumor OCR (reading "X will be scarce in <city>" off-screen) is a SEPARATE
    # later task; until it lands there are no rumors here, so pass None -> ordinary
    # decay. combined_destinations already conditions on any rumor it IS given.
    state_rumors = None
    dests = combined_destinations(current, matrix, market, inv, rates, cash=cash,
                                  carry=carry, rumors=state_rumors, ship=False)
    print("\n(2) BEST DESTINATION — fly there, sell inventory + Boston buys (POTENTIAL,")
    print("    if spikes hold & that Market trades your drugs on arrival). Each plan")
    print("    trades a BASKET — every inventory drug sellable there + every Boston")
    print("    drug whose spread beats shipping; 'driver' is just the biggest line.")
    print(f"    (buy-arb only counts trades that still profit after a {ARB_SAFETY:.0%} "
          f"sell-price drop — set DL2_ARB_SAFETY to change.)")
    if cash is not None:
        print(f"    buy-arb is CASH-CAPPED at ${cash:,} (drug+shipping paid up front); "
              f"quantities shown are what you can afford, best profit-per-$ first.")
    print(f"    up to {carry:,} units fly FREE on the plane (no shipping); overflow ships "
          f"at the per-unit rate. 'ship' column below = units over the free carry.")
    print("    EV(exact-model) = the SAME plan with destination spikes decayed to "
          "what you'll realize;")
    print("    the headline COMBINED keeps the observed prices. EV never replaces it.")
    print(f"      {'city':13} {'COMBINED net':>15} {'EV net':>15} {'inv-sell':>12} "
          f"{'buy-arb':>12} {'#drugs':>6} {'carry/ship':>12}  top driver")
    for r in dests[:6]:
        n_inv = sum(1 for d in inv if matrix[r['city']].get(d))
        n_arb = len(r["arb"])
        # the single biggest line item, and whether it's a spike, for context
        inv_line = max(((d, inv[d] * (matrix[r['city']].get(d) or 0), matrix[r['city']].get(d))
                        for d in inv if matrix[r['city']].get(d)),
                       key=lambda t: t[1], default=(None, 0, 0))
        arb_line = r["arb"][0] if r["arb"] else None
        if inv_line[1] >= (arb_line[4] if arb_line else 0):
            d, val, p = inv_line
            spike = med.get(d) and p and p >= SPIKE * med[d]
            drv = f"sell {inv[d]:,} {d} @${p:,}" + (f" SPIKE {p/med[d]:.0f}x" if spike else "")
        else:
            d, qty, buy, sell, tot = arb_line
            spike = med.get(d) and sell >= SPIKE * med[d]
            drv = f"buy {qty:,} {d} ${buy:,}->${sell:,}" + (f" SPIKE {sell/med[d]:.0f}x" if spike else "")
        cship = f"{r.get('carried',0):,}/{r.get('shipped',0):,}"
        print(f"      {r['city'].split(',')[0]:13} ${r['combined']:>14,.0f} "
              f"${r.get('ev_combined', r['combined']):>14,.0f} "
              f"${r['inv_net']:>11,.0f} ${r['arb_net']:>11,.0f} {n_inv+n_arb:>6} "
              f"{cship:>12}  {drv}")

    # (3) TWO-TURN PLAYS — reposition first, THEN arb. Higher variance than §2: the
    #     buy leg is a bet on a city we're NOT in (can't see its Market/stock until
    #     we land) and it eats an extra travel day. Gated to SPIKE sell-legs, which
    #     persist ~3 days and so survive the round trip. Additive — never replaces §2.
    repo = reposition_arbs(current, matrix, med, market, cash=cash, carry=carry,
                           spike_mult=SPIKE, held_units=total)
    # EXACT-model EV: a 2-hop realizes the sell ~2 travel days out, so decay the
    # spike sell price over n=2 days (decay.expected_sell_price). This REPLACES
    # the old fitted-curve EV as the honest valuation; the fitted collector
    # (`dl2 decay`) stays as a cross-check (printed below when it has enough data).
    REPO_DAYS = 2
    def _repo_ev(r):
        ev_sell = None
        try:
            ev_sell = decay.expected_sell_price(r["sell"], r["drug"], r["sell_city"],
                                                n=REPO_DAYS)
        except Exception:
            ev_sell = None
        if ev_sell is None:
            return None
        return r["units"] * (ev_sell - r["buy"])
    dmodel = _load_decay_model()
    ratio2 = _decay_ratio(dmodel, 2)                 # fitted cross-check (or None)
    print("\n" + "=" * 66)
    print("(3) TWO-TURN PLAYS (higher variance) — fly to a BUY city, then to the")
    print("    SPIKE city. Surfaces plays the 1-hop view CAN'T: cheap stock that's a")
    print("    flight away from where you stand. Two travel days from here.")
    print("    ⚠ CONDITIONAL: the buy price is the buy-city's WORLD price — we can't")
    print("      see that city's Market or stock until we land, so each row is a BET")
    print("      it sells the drug near this price. Gated to true spikes (sell ≥")
    print(f"      {SPIKE:.0f}× median), which last ~3 days and so outlive the 2-day trip.")
    print(f"    EV(~2d) = the exact market model decaying the spike over ~{REPO_DAYS} "
          f"days (~$16k/day),")
    print("      shown ALONGSIDE the un-decayed headline profit — never replacing it.")
    if ratio2 is not None:
        print(f"      (fitted cross-check available: e^(-{dmodel['k']}·days), "
              f"half-life {dmodel.get('half_life_days')}d.)")
    if not repo:
        print("    — no two-turn spike plays right now (no gated spike has cheaper")
        print("      buyable stock in another city, or free plane-carry is full).")
    else:
        hdr = (f"      {'buy in':13} {'drug':9} {'buy@world':>10}    {'sell in':13} "
               f"{'spike':>9} {'mult':>5} {'units':>6} {'profit':>13}     EV(~2d)")
        print(hdr)
        for r in repo[:6]:
            ev_profit = _repo_ev(r)
            ev = f"  ${ev_profit:>11,.0f}" if ev_profit is not None else ""
            print(f"      {r['buy_city'].split(',')[0]:13} {r['drug']:9} "
                  f"${r['buy']:>9,} -> {r['sell_city'].split(',')[0]:13} "
                  f"${r['sell']:>8,} {r['mult']:>4.0f}x {r['units']:>6,} "
                  f"${r['profit']:>12,.0f}{ev}")
        # spell the top play out as an explicitly-conditional sentence
        t = repo[0]
        line = (f"    ➤ IF {t['buy_city'].split(',')[0]} sells {t['drug']} near "
                f"${t['buy']:,}: buy {t['units']:,}, fly to "
                f"{t['sell_city'].split(',')[0]}, sell ${t['sell']:,} "
                f"≈ ${t['profit']:,.0f}")
        _tev = _repo_ev(t)
        if _tev is not None:
            line += f"  (EV ≈ ${_tev:,.0f} after ~{REPO_DAYS}-day decay)"
        print(line)
        if t["alts"]:
            alt = ", ".join(f"{c.split(',')[0]} ${p:,}" for c, p in t["alts"])
            print(f"      (other buy cities for {t['drug']}: {alt} — cheapest wins)")
        print("      Carry-bounded: units = your free plane slots (inventory size −")
        print("      held). Sell/drop held stock, or level up, to carry more.")

    # (4) CHANNEL + SHIPPER ADVISORY (advisory only — never changes the default
    #     ship action of International Couriers overnight). For the top destination's
    #     biggest bought load: (a) is a cheaper/safer shipper higher-EV? (folding in
    #     the all-or-nothing delivery probability) and (b) carry vs ship, reporting
    #     the No-Scent detection probability (combat-loss magnitude is UNKNOWN in the
    #     model, so it is reported, never invented). Degrades silently if absent.
    no_scent = main_st.get("no_scent") or status.get("no_scent")
    if dests and dests[0].get("arb"):
        b = dests[0]
        d0, units0, buy0, sell0, _p0 = b["arb"][0]
        dshort = b["city"].split(",")[0]
        adv = ship_advisory(current, dshort, units0, sell0)
        chan = channel_advice(units0, sell0, no_scent, current, dshort)
        if adv or chan:
            print("\n" + "=" * 66)
            print(f"(4) CHANNEL ADVISORY for {units0:,} {d0} -> {dshort} (advisory only):")
        if adv:
            dfl = adv["default"]
            if dfl:
                print(f"    default {dfl[0]} (p={dfl[1]:.2f}): "
                      f"EV ${dfl[2]:,.0f}")
            if adv["better"]:
                bt = adv["better"]
                print(f"    ⓘ higher-EV shipper available: {bt[0]} (p={bt[1]:.2f}) "
                      f"EV ${bt[2]:,.0f} — advisory, default unchanged.")
            else:
                print(f"    default shipper is already the highest-EV choice here.")
        if chan and chan.get("carry"):
            c = chan["carry"]
            sev = chan.get("ship", {}).get("expected_value")
            print(f"    carry: {c['cans_used']}/{c['cans_needed']} No-Scent cans, "
                  f"detection prob {c['detection_prob']:.2%} "
                  f"(bust loss UNKNOWN — combat resolution undecoded).")
            if sev is not None:
                print(f"    ship:  EV ${sev:,.0f} (delivery-failure priced in).")

    # ---------- honest bottom line ----------
    print("\n" + "-" * 66)
    if sell_now_total > 0:
        print(f"• Certain now: bank ${sell_now_total:,} selling into {current}'s Market.")
    else:
        print(f"• Certain now: nothing — {current} won't buy your held drugs today.")
    if dests:
        b = dests[0]
        print(f"➤ Best move: FLY TO {b['city'].split(',')[0].upper()} — "
              f"~${b['combined']:,.0f} potential "
              f"(${b['inv_net']:,.0f} inventory + ${b['arb_net']:,.0f} buy-arb).")
    print("  POTENTIAL, not a lock: spikes usually last 3+ days (favorable) but you")
    print("  can't confirm the destination trades your drugs until you land.")

    # --- audit trail: log this PROJECTION so we can later compare it to outcomes ---
    held_spikes = []
    for d in inv:
        cands = [(c, cp[d]) for c, cp in matrix.items()
                 if c.split(",")[0] != current and cp.get(d)]
        if not cands:
            continue
        c, p = max(cands, key=lambda t: t[1])
        if med.get(d) and p >= SPIKE * med[d]:
            held_spikes.append({"drug": d, "city": c, "price": p, "mult": p / med[d]})
    # Arb projections: the buy-here/sell-there sell prices for the top destinations.
    # These are what we actually trade (held_spikes only covers inventory drugs), so
    # log them too — `dl2 decay` pairs each realized sell against its prior projection.
    arb_targets = [{"city": r["city"].split(",")[0], "drug": d,
                    "buy": buy, "projected_sell": sell}
                   for r in dests[:6] for (d, qty, buy, sell, prof) in r["arb"]]
    audit.record(
        "decision", city=current, day=status.get("day"),
        inventory=inv, safety=ARB_SAFETY, sell_now_total=sell_now_total,
        recommend=(dests[0]["city"] if dests else None),
        recommend_value=(dests[0]["combined"] if dests else 0),
        candidates=[{"city": r["city"], "combined": round(r["combined"]),
                     "inv_net": round(r["inv_net"]), "arb_net": round(r["arb_net"])}
                    for r in dests[:6]],
        held_spikes=held_spikes, arb_targets=arb_targets)


def sell_all_inventory(current_short):
    """At your current city, double-click each held drug that the local market will buy
    and OK the Selling dialog. Skips drugs the market won't buy today (very common — the
    market rotates), and drugs with no Selling popup. Returns (sold, skipped)."""
    base = _read(window="Drug Lord")
    ocr = base["_ocr"]
    day = (base.get("status") or {}).get("day")   # game day, to compute decay elapsed
    inv = _inventory_drugs(ocr)
    cal = calibrate(ocr["frame"], "Drug Lord")
    mx, cx, my, cy = cal
    # What the local Market is actually buying today (you can only sell a drug the
    # Market lists). Check this BEFORE attempting a sale, so we never repeatedly
    # double-click a held drug the market won't take (e.g. PCP/Cocaine).
    sell_price = {d: info.get("price") for d, info in (base.get("market") or {}).items()
                  if info.get("price")}
    sellable = set(sell_price)
    held_qty = _inventory_with_qty(ocr)
    not_listed = [d for d in inv if d not in sellable]
    if not_listed:
        print(f"  Market isn't buying (skip without trying): {', '.join(not_listed)}",
              file=sys.stderr)
    print(f"  inventory: {inv}  |  Market buys: {sorted(sellable)}", file=sys.stderr)

    def _dialog():
        return next((w for w in _game_windows()
                     if "sell" in w["title"].lower() or "buy" in w["title"].lower()), None)

    d = _dialog()                                    # clear any stray dialog first
    if d:
        subprocess.run([INPUT, "key", "53"], capture_output=True); time.sleep(0.3)

    sold, skipped, processed = [], [], set()
    for drug in inv:
        if drug in processed:
            continue
        if drug not in sellable:                     # Market won't buy it — don't try
            skipped.append(drug); processed.add(drug)
            continue
        boxes = _read(window="Drug Lord")["_ocr"]["boxes"]
        row = next((b for b in boxes if b["x"] > 640 and 300 < b["y"] < 700
                    and parser._clean_drug(b["text"]) == drug), None)
        if not row:
            continue
        bx = row["x"] + row.get("w", 40) / 2
        by = row["y"] + row.get("h", 12) / 2
        done = False
        for dy in [0, 7, -7, 14, -14, 21]:           # nudge vertically to beat aim error
            subprocess.run([INPUT, "dclick", str(int((bx - cx) / mx)),
                            str(int((by + dy - cy) / my))], capture_output=True); time.sleep(0.9)
            dlg = _dialog()
            title = dlg["title"] if dlg else ""
            if "selling" in title.lower():
                actual = title.split("Selling", 1)[-1].strip() or drug   # which drug it opened
                subprocess.run([INPUT, "key", "36"], capture_output=True); time.sleep(0.8)  # OK
                sold.append(actual); processed.add(actual); done = True
                p, q = sell_price.get(actual), held_qty.get(actual)
                audit.record("sell", city=current_short, drug=actual, price=p, qty=q,
                             revenue=(p * q if p and q else None), day=day)
                print(f"    SOLD {actual}"
                      + (f" ~{q:,} u @ ${p:,}" if p and q else ""), file=sys.stderr)
                break
            if dlg:                                  # a non-Sell popup -> Escape, stop nudging
                subprocess.run([INPUT, "key", "53"], capture_output=True); time.sleep(0.2)
                break
        if not done:
            skipped.append(drug); processed.add(drug)
            print(f"    skip {drug} — not sellable here today", file=sys.stderr)
    return sold, skipped


def sell_drug(drug, qty, cal):
    """Sell `drug` in the current city: click it in The Market, click '<< Sell', then —
    ONLY after verifying the popup is a SELLING dialog (not a mis-clicked Buy) — press
    OK. qty=None sells the whole held amount (pre-filled). Returns True on a real sell,
    'aborted' if it detected a Buy dialog (and Escapes it), False if it couldn't act."""
    mx, cx, my, cy = cal
    row = sell = None
    for _ in range(6):                                   # retry: Sell-button OCR is flaky
        boxes = _read(window="Drug Lord")["_ocr"]["boxes"]
        row = next((b for b in boxes if b["x"] < 160 and parser._clean_drug(b["text"]) == drug), None)
        sell = next((b for b in boxes if "sel" in b["text"].lower() and b["x"] > 480
                     and len(b["text"]) < 12), None)   # OCR renders "<< Sell" as "s< Sel" etc.
        if row and sell:
            break
        time.sleep(0.15)
    if not row:
        print(f"    {drug}: not in The Market", file=sys.stderr); return False
    if not sell:
        print("    couldn't find the Sell button after retries", file=sys.stderr); return False
    click(int((row["x"] + row.get("w", 40) / 2 - cx) / mx),
          int((row["y"] + row.get("h", 12) / 2 - cy) / my)); time.sleep(0.3)
    click(int((sell["x"] + sell.get("w", 40) / 2 - cx) / mx),
          int((sell["y"] + sell.get("h", 12) / 2 - cy) / my)); time.sleep(0.9)
    # SAFETY: confirm the popup is a Sell dialog. The buy/sell dialog is its own window.
    dlg = next((w for w in _game_windows()
                if "sell" in w["title"].lower() or "buy" in w["title"].lower()
                or "moving" in w["title"].lower()), None)
    title = (dlg["title"].lower() if dlg else "")
    if "buy" in title or "moving" in title:
        subprocess.run([INPUT, "key", "53"], capture_output=True)   # Escape — wrong dialog
        print(f"    ABORT: got a '{dlg['title']}' dialog, not Sell — Escaped, bought nothing",
              file=sys.stderr)
        return "aborted"
    if "sell" not in title:
        subprocess.run([INPUT, "key", "53"], capture_output=True)   # unknown -> Escape, be safe
        print(f"    ABORT: couldn't confirm a Sell dialog (title={dlg['title'] if dlg else None}) — Escaped",
              file=sys.stderr)
        return "aborted"
    if qty is not None:
        for _ in range(9):
            subprocess.run([INPUT, "key", "51"], capture_output=True); time.sleep(0.03)
        subprocess.run([INPUT, "num", str(int(qty))], capture_output=True); time.sleep(0.3)
    subprocess.run([INPUT, "key", "36"], capture_output=True); time.sleep(0.9)     # OK
    return True


def _held(tries=5):
    """Held-units count, majority-voted over a few reads (the box OCRs flakily)."""
    from collections import Counter
    vals = []
    for _ in range(tries):
        ocr = _read(window="Drug Lord")
        if ocr:
            h = parser.parse(ocr["_ocr"]).get("held")
            if h is not None:
                vals.append(h)
        time.sleep(0.08)
    return Counter(vals).most_common(1)[0][0] if vals else None


def _market_qty(drug):
    st = _read(window="Drug Lord")
    return (parser.parse(st["_ocr"])["market"].get(drug, {}).get("qty") or 0) if st else 0


def buy_and_ship_full(order_drugs, current_short, dest_short):
    """Buy and ship a whole order, one drug at a time. For each drug: buy the MAX that
    fits (just OK — the game caps it at inventory space, so no overflow), ship all of it,
    and repeat until that drug's market stock is exhausted. This also ships any stuck
    stock already in inventory, and needs no capacity arithmetic."""
    for drug in order_drugs:
        cyc = 0
        while True:
            cyc += 1
            mkt = _market_qty(drug)
            base = _read(window="Drug Lord")
            cal = calibrate(base["_ocr"]["frame"], "Drug Lord")
            if mkt > 0:
                print(f"  {drug}: cycle {cyc} — buy max (market has {mkt:,}), then ship all",
                      file=sys.stderr)
                buy_drug(drug, None, cal)                 # buy the pre-filled MAX
            else:
                print(f"  {drug}: cycle {cyc} — market empty, ship any remaining stock",
                      file=sys.stderr)
            ship_drugs([(drug, None)], current_short, dest_short,
                       (base.get("status") or {}).get("day"))       # ship all we hold
            if _market_qty(drug) <= 0:
                break                                      # nothing left to buy
            if cyc > 12:
                print(f"  {drug}: stopping after {cyc} cycles (safety)", file=sys.stderr); break
    print("  ✓ order complete — all drugs bought and shipped.", file=sys.stderr)


def buy_plan(trades):
    """trades = [(drug, qty), ...]. Buys each from The Market (world window must be
    closed so The Market is visible). Returns count bought."""
    base = _read(window="Drug Lord")
    if not base:
        print("  can't see the main window to buy", file=sys.stderr); return 0
    cal = calibrate(base["_ocr"]["frame"], "Drug Lord")
    before = _held()
    ok = 0
    for drug, qty in trades:
        q = None if qty is None else int(qty)          # None = buy the pre-filled MAX
        if buy_drug(drug, q, cal):
            ok += 1
            print(f"    bought {'MAX' if q is None else format(q, ',')} {drug}", file=sys.stderr)
    after = _held()
    if before is not None and after is not None:
        print(f"  held units: {before:,} -> {after:,}", file=sys.stderr)
    return ok


def ship_drugs(trades, current_short, dest_short, day=None):
    """Actually SHIP: open Shipping, load each (drug, qty) into 'What you are shipping'
    at the set quantity, pick the destination, Overnight delivery, select International
    Couriers (most expensive/most reliable), then OK — which ships and closes the window."""
    if not open_shipping():
        return False
    ship = _read(window="Shipping")
    cal = calibrate(ship["_ocr"]["frame"], "Shipping")
    mx, cx, my, cy = cal

    def sof(bx):
        return int((bx["x"] + bx.get("w", 40) / 2 - cx) / mx), int((bx["y"] + bx.get("h", 12) / 2 - cy) / my)

    def find(pred):
        return next((b for b in _read(window="Shipping")["_ocr"]["boxes"] if pred(b)), None)

    # 1) Load each drug (double-click stock -> Moving dialog -> OK). qty=None ships the
    #    WHOLE stock: the dialog pre-fills the full amount, so we just hit OK (faster).
    #    A number clears the field and types that amount instead.
    loaded = []
    for drug, qty in trades:
        # Retry the row read: a one-off OCR miss on a stock row used to silently
        # drop a drug (e.g. Ice got left behind). Re-read a few times before
        # concluding it's genuinely not in stock.
        row = None
        for _ in range(6):
            o = _read(window="Shipping")["_ocr"]
            # 'Your stock' is the rightmost drug-name column; pick the target drug's
            # match with the largest x (survives a different window width).
            cands = [b for b in o["boxes"] if b["y"] < 470
                     and parser._clean_drug(b["text"]) == drug]
            row = max(cands, key=lambda b: b["x"]) if cands else None
            if row:
                break
            time.sleep(0.2)
        if not row:
            print(f"    {drug}: not in Your stock after retries (skipped)", file=sys.stderr)
            continue
        x, y = sof(row)
        subprocess.run([INPUT, "dclick", str(x), str(y)], capture_output=True); time.sleep(0.7)
        if qty is not None:
            for _ in range(9):
                subprocess.run([INPUT, "key", "51"], capture_output=True); time.sleep(0.03)   # Backspace
            subprocess.run([INPUT, "num", str(int(qty))], capture_output=True); time.sleep(0.3)
        subprocess.run([INPUT, "key", "36"], capture_output=True); time.sleep(0.6)             # OK
        audit.record("ship", drug=drug, qty=("all" if qty is None else int(qty)),
                     dest=dest_short, src=current_short, day=day)
        loaded.append(drug)
        print(f"    loaded {'all' if qty is None else format(int(qty), ',')} {drug}", file=sys.stderr)

    if not loaded:
        print("    nothing loaded to ship — aborting this shipment", file=sys.stderr)
        return []

    # 2) Destination (list excludes the current city): click first, Home, Down to target.
    dest_order = [c for c in parser.CITY_SHORT if c != current_short]
    first = find(lambda b: b["text"].strip() == dest_order[0])
    if first:
        click(*sof(first)); time.sleep(0.2)
    subprocess.run([INPUT, "key", "115"], capture_output=True); time.sleep(0.2)            # Home
    for _ in range(dest_order.index(dest_short)):
        subprocess.run([INPUT, "key", "125"], capture_output=True); time.sleep(0.12)       # Down
    time.sleep(0.3)

    # 3) Overnight, 4) select International Couriers, 5) OK (ships + closes window).
    over = find(lambda b: "Overnight" in b["text"])
    if over:
        click(*sof(over)); time.sleep(0.3)
    intl = find(lambda b: "International" in b["text"] or "Couriers" in b["text"])
    if intl:
        click(*sof(intl)); time.sleep(0.3)
    ok = find(lambda b: b["text"].strip() == "OK")
    if not ok:
        print("    couldn't find OK button", file=sys.stderr); return False
    click(*sof(ok)); time.sleep(1.0)
    shipped = not any("shipping" in w["title"].lower() for w in _game_windows())
    print(f"    shipped {loaded} to {dest_short} via International Couriers (overnight): "
          f"{'window closed OK' if shipped else 'window still open?'}", file=sys.stderr)
    return loaded if shipped else []


# A buy->sell arb is only worth it if it survives the overnight reroll. We keep a
# trade only when it stays profitable after the SELL price falls by ARB_SAFETY,
# i.e. sell*(1-ARB_SAFETY) - buy - rate > 0. This drops razor-thin trades (e.g.
# $16/unit) that a small price drop would flip into a loss on already-shipped
# goods. Tunable via the DL2_ARB_SAFETY env var (a fraction, e.g. 0.15).
ARB_SAFETY = float(os.environ.get("DL2_ARB_SAFETY", "0.15"))

# Units you'll bring on the PLANE for free (no shipping cost) when you fly to the
# destination — capped by inventory capacity at call time. Overflow beyond this
# pays the flat per-unit shipping rate. Tunable via DL2_CARRY.
DEFAULT_CARRY = int(os.environ.get("DL2_CARRY", "500"))


def _arb_ok(buy, sell, rate, safety=None):
    """Keep the trade only if it still profits after the sell price drops `safety`."""
    s = ARB_SAFETY if safety is None else safety
    return sell * (1 - s) - buy - rate > 0


# The shipper/speed the bot actually uses when it ships (see README `dl2 ship`).
# Everything below prices a plan with THIS default and only SURFACES a cheaper
# alternative as an advisory — it never changes the default ship action.
DEFAULT_SHIPPER = "International Couriers"      # 0.99 delivery
DEFAULT_SHIP_DAYS = 1                            # overnight


def shipper_ev_ranking(origin, dest, units, unit_value, days=DEFAULT_SHIP_DAYS):
    """EV of shipping `units` each worth `unit_value` at the destination, per
    shipper, via the EXACT model (shipping.expected_ship_value folds in the
    all-or-nothing delivery probability and the sunk fee). Returns
    [(shipper, success_prob, ev)] best-EV first. `origin`/`dest` are SHORT city
    names (must be in constants.CITY_POS). Advisory only — the default ship
    action stays DEFAULT_SHIPPER/DEFAULT_SHIP_DAYS regardless of this ranking."""
    out = []
    for shipper in _C.SHIPPERS:
        try:
            ev = _shipping.expected_ship_value(origin, dest, units, unit_value,
                                               shipper, days)
        except Exception:
            continue
        out.append((shipper, _shipping.success_prob(shipper), ev))
    out.sort(key=lambda t: -t[2])
    return out


def ship_advisory(origin, dest, units, unit_value, days=DEFAULT_SHIP_DAYS):
    """Compare the DEFAULT shipper's EV to the best-EV shipper. Returns
    {default, best, better} where `better` is a cheaper/safer shipper that beats
    the default (or None if the default is already best). Advisory — surface it,
    do NOT change the default ship action."""
    ranking = shipper_ev_ranking(origin, dest, units, unit_value, days)
    if not ranking:
        return None
    default = next((r for r in ranking if r[0] == DEFAULT_SHIPPER), None)
    best = ranking[0]
    better = best if (default is None or best[0] != DEFAULT_SHIPPER) and \
        (default is None or best[2] > default[2]) else None
    return {"default": default, "best": best, "better": better, "ranking": ranking}


def estimate_remote_stock(observed_price, drug, city, rank):
    """ESTIMATE of the buyable stock at a REMOTE destination you can't see yet:
    stock.stock_target(observed_price, M, C) with C = capacity_for_rank(rank) and
    M = normal_mean. Labelled an estimate everywhere it's used — you only KNOW a
    market once you arrive. Returns None on unknown drug/city/rank."""
    try:
        if not decay.in_model(drug, city):
            return None
        M = _C.normal_mean(drug, city.split(",")[0])
        cap = _finance.capacity_for_rank(rank)
        return max(_stock.stock_target(observed_price, M, cap), 0)
    except Exception:
        return None


def channel_advice(units, unit_value, no_scent, origin, dest,
                   shipper=DEFAULT_SHIPPER, days=DEFAULT_SHIP_DAYS):
    """Carry-vs-ship advisory for the chosen load (risk.channel_recommendation).
    Combat-loss magnitude is UNKNOWN in the model, so bust_cost_hook stays None
    and the carry side reports the No-Scent detection probability only. Advisory
    only — never changes the default ship action. Returns None on unknown city."""
    try:
        return _risk.channel_recommendation(
            units=units, unit_value=unit_value, cans_available=(no_scent or 0),
            origin=origin, dest=dest, shipper=shipper, days=days,
            bust_cost_hook=None)
    except Exception:
        return None


def _arb_legs(buy_market, sell_prices, rate, safety, cash, free):
    """The buy-here/sell-there greedy, factored out of combined_destinations so the
    2-hop reposition search reuses the EXACT same economics (safety filter, cash
    cap, free-carry-then-ship phases).

      buy_market  : {drug: {"price": buy, "qty": stock}} you can BUY — the current
                    Market for 1-hop; a synthetic world-price market for a
                    reposition city in 2-hop.
      sell_prices : {drug: sell} at the destination.
      rate        : flat per-unit ship cost for units past the free carry.
      safety      : sell-drop the trade must still profit after (ARB_SAFETY-style).
      cash        : up-front budget (buy per unit, +rate per shipped unit); None = ∞.
      free        : free plane-carry slots for BOUGHT goods (after inventory took
                    its share).

    Returns (arb, carried, shipped): arb = [(drug, units, buy, sell, profit)]
    ranked by profit; carried/shipped = unit totals. Pure — no screen, no I/O."""
    s = safety
    legs = [(d, info.get("qty") or 0, info.get("price"), sell_prices.get(d))
            for d, info in buy_market.items()
            if info.get("price") and sell_prices.get(d) and (info.get("qty") or 0) > 0]
    # rank by best-case (carried, no rate) profit-per-dollar, safety-adjusted
    legs.sort(key=lambda l: -((l[3] * (1 - s) - l[2]) / l[2]) if l[2] > 0 else 0)
    budget, arb, cc, cs = cash, [], 0, 0
    for d, qty, buy, sell in legs:
        carried = shipped = 0
        # phase 1: FREE plane-carry slots — must clear the safety drop (no rate)
        if free > 0 and sell * (1 - s) - buy > 0:
            aff = qty if budget is None else (int(budget // buy) if buy > 0 else qty)
            take = min(qty, free, aff)
            if take > 0:
                carried, free, qty = take, free - take, qty - take
                if budget is not None:
                    budget -= take * buy
        # phase 2: SHIPPED overflow — must clear the safety drop AFTER shipping
        if qty > 0 and sell * (1 - s) - buy - rate > 0:
            outlay = buy + rate
            aff = qty if budget is None else (int(budget // outlay) if outlay > 0 else 0)
            take = min(qty, aff)
            if take > 0:
                shipped = take
                if budget is not None:
                    budget -= take * outlay
        units = carried + shipped
        if units > 0:
            profit = carried * (sell - buy) + shipped * (sell - buy - rate)
            arb.append((d, units, buy, sell, profit))
            cc += carried; cs += shipped
    arb.sort(key=lambda a: -a[4])
    return arb, cc, cs


def combined_destinations(current, matrix, market, inv, ship_rates, safety=None,
                          cash=None, carry=0, rumors=None, ship=False, ship_days=1):
    """Rank each destination by the COMBINED value of flying there and selling
    EVERYTHING you can there in one turn: your existing inventory PLUS goods you
    buy in the current Market and bring along. You fly to one city, so the two
    aren't separate scenarios — the optimum is their sum at a single destination.

    Per city: combined_net = inventory_sell_net + buy_here_sell_there_arb_net.
      * inventory_sell_net = sum(held qty x that city's price) - shipping any inv
        that overflows the free plane carry. (Selling inventory generates cash
        only AFTER you arrive, so it does NOT fund the up-front buy below.)
      * arb_net = profit from buying current-Market drugs, then selling there.

    `carry` = units you bring on the PLANE for FREE (no shipping) when you fly —
    a single pool per destination (you fly once), bounded by inventory capacity.
    Held inventory rides free first; then bought goods fill the remaining free
    slots; anything beyond `carry` pays the flat per-unit `rate`. A carried unit
    saves exactly `rate` (uniform across drugs), so a thin trade that can't clear
    shipping can still be worth CARRYING. carry=0 = ship everything (old behavior).

    `cash` caps the arb: a carried unit costs `buy` up front, a shipped unit
    `buy+rate` (both paid before you sell). Budget is spent greedily on the best
    profit-per-dollar legs first. cash=None = unlimited.

    A trade is only kept if it survives an ARB_SAFETY sell-price drop (see
    _arb_ok) — stricter for shipped units (they also eat `rate`). Reported profit
    uses the headline sell price; the safety margin is a filter, not a haircut.
    Every number is POTENTIAL: it needs the spike to hold and the destination's
    Market to actually trade the drug on arrival (unknowable until you land).

    EXACT-MODEL EV (additive, dl2model wiring): alongside the headline `combined`,
    each row also carries `ev_net`/`ev_combined` and `arb_ev` — the SAME plan
    valued with decay.expected_sell_price, i.e. the destination sell prices
    decayed to what you'll realize `n` days out (n=1 flown, else the shipment's
    expected transit), and conditioned on any (drug, city) rumor. The headline
    ranking is UNCHANGED; EV is an added column, never a replacement (owner rule).
    Sells you'll realize at the destination are the only ones decayed."""
    s = ARB_SAFETY if safety is None else safety
    n_days = decay.sell_horizon_days(ship=ship, ship_days=ship_days)
    inv_total = sum(inv.values())
    out = []
    for city, cp in matrix.items():
        if city.split(",")[0] == current:
            continue
        rate = ship_rates.get(city, 0)
        free = carry
        # held inventory rides the plane first: free up to `free`, the rest ships
        inv_rev = sum(inv.get(d, 0) * (cp.get(d) or 0) for d in inv)
        inv_carried = min(inv_total, free)
        inv_net = inv_rev - rate * (inv_total - inv_carried)
        free -= inv_carried

        # buy-here/sell-there arb for this city — the shared greedy (see _arb_legs)
        arb, cc, cs = _arb_legs(market, cp, rate, s, cash, free)
        arb_net = sum(a[4] for a in arb)

        # --- exact-model EV of the same rows (headline sell -> decayed EV sell) ---
        def _ev(observed, drug):
            try:
                return decay.expected_sell_price(
                    observed, drug, city, n=n_days,
                    rumor=decay.find_rumor(rumors, drug, city))
            except Exception:
                return None
        arb_ev = []
        arb_ev_net = 0.0
        for d, units, buy, sell, profit in arb:
            ev_sell = _ev(sell, d)
            if ev_sell is None:
                ev_sell, ev_profit = sell, profit          # fall back to observed
            else:
                # profit = units*(sell-buy) - shipped*rate; swapping sell->ev_sell
                # shifts every unit by the per-unit haircut, shipping cost intact.
                ev_profit = profit - units * (sell - ev_sell)
            arb_ev.append((d, units, buy, ev_sell, ev_profit))
            arb_ev_net += ev_profit
        # inventory carried to the destination is also realized n days out
        inv_ev_rev = sum(inv.get(d, 0) * ((_ev(cp[d], d) if cp.get(d) else None) or (cp.get(d) or 0))
                         for d in inv)
        inv_ev_net = inv_ev_rev - rate * (inv_total - inv_carried)

        out.append({"city": city, "combined": inv_net + arb_net, "inv_net": inv_net,
                    "arb_net": arb_net, "arb": arb, "rate": rate,
                    "ev_net": arb_ev_net, "inv_ev_net": inv_ev_net,
                    "ev_combined": inv_ev_net + arb_ev_net, "arb_ev": arb_ev,
                    "carried": inv_carried + cc,
                    "shipped": (inv_total - inv_carried) + cs})
    out.sort(key=lambda r: -r["combined"])
    return out


def _load_decay_model():
    """The fitted spike-decay model (decay.py writes it once enough pairs exist),
    or None. Never raises — a missing/garbled model just means 'decay unmodelled'."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "decay_model.json")) as f:
            m = json.load(f)
        return m if m.get("model") == "exp" and m.get("k") is not None else None
    except Exception:
        return None


def _decay_ratio(model, days):
    """Expected realized/projected after `days`, per the fitted model, or None."""
    if not model:
        return None
    import math
    return math.exp(-model["k"] * days)


def reposition_arbs(current, matrix, med, own_market, safety=None, cash=None,
                    carry=0, spike_mult=3.0, held_units=0, top_alt=2):
    """Spike-gated TWO-HOP ("reposition-then-arb") search: fly to a BUY city C, buy
    a drug there, fly to a SPIKE city B, sell into the spike. Two travel days from
    `current` — it surfaces plays the 1-hop view is blind to, where the cheap stock
    is a flight away from where you stand (e.g. Heroin spiking in Miami, cheap in
    Austin, while you're in Miami: fly Austin, buy, fly back, sell).

    HARD CAVEAT (why this is a separate, higher-variance section): we only ever
    KNOW the current city's real Market. City C's buy price here is its WORLD price
    (reference) and its stock is UNKNOWN, so every row is a BET that C sells the
    drug near this price when you land. We therefore GATE to genuine spike sell-legs
    (sell >= spike_mult * cross-city median) — those persist ~3 days and so survive
    the 2-day round trip; ordinary margins reroll away and are left to the 1-hop tool.

    Bought goods ride the plane FREE up to `carry` minus held inventory (which rides
    first, same as 1-hop). We do NOT model shipped overflow: C->B courier rates
    aren't in the store (rates are measured from the city you're standing in), so a
    2-hop play is carry-bounded — honest about what we can actually confirm.

    Drugs the CURRENT Market already sells are skipped: those are a 1-hop play (§2
    already finds them), not a reposition. Returns rows ranked by headline profit."""
    s = ARB_SAFETY if safety is None else safety
    free = max(0, carry - held_units)
    rows = []
    for B, cp in matrix.items():
        for d, sell in cp.items():
            if not (sell and med.get(d) and sell >= spike_mult * med[d]):
                continue                                  # gate: spike sell-legs only
            # Skip ONLY if the current Market already sells this drug cheaply enough
            # to carry straight to B — that's a 1-hop §2 play, not a reposition. A
            # drug LISTED here at the spike price (e.g. Heroin buyable at $54k while
            # it also sells at $54k) is NOT cheap-to-arb, so it stays: the spike is
            # at home and the cheap stock is a flight away — the whole point of §3.
            here = own_market.get(d, {}).get("price")
            if here and sell * (1 - s) - here > 0:
                continue
            # BUY candidates: any city but where you stand and but the sell city,
            # cheapest world price first; keep only those the spike arb clears.
            cands = sorted(
                ((c, cp2[d]) for c, cp2 in matrix.items()
                 if cp2.get(d) and c.split(",")[0] != current
                 and c.split(",")[0] != B.split(",")[0]),
                key=lambda t: t[1])
            cands = [(c, p) for c, p in cands if sell * (1 - s) - p > 0]
            if not cands:
                continue
            C, buy = cands[0]
            # reuse the 1-hop economics: a synthetic single-drug market at C, carry-
            # bounded (rate=0 but qty=free means no overflow ever ships), cash-capped.
            arb, cc, cs = _arb_legs({d: {"price": buy, "qty": free}}, {d: sell},
                                    0, s, cash, free)
            if not arb:
                continue
            _, units, _, _, profit = arb[0]
            rows.append({"drug": d, "buy_city": C, "buy": buy, "sell_city": B,
                         "sell": sell, "mult": sell / med[d], "units": units,
                         "profit": profit, "alts": cands[1:1 + top_alt]})
    rows.sort(key=lambda r: -r["profit"])
    return rows


def best_net_plan(market, matrix, ship_rates, safety=None, origin=None):
    """For each destination with a measured rate, keep only drugs whose spread beats
    the flat per-unit shipping cost AND survives an ARB_SAFETY sell-price drop (so a
    razor-thin trade the overnight reroll would flip negative is excluded). Returns
    every city's net plan, ranked by net (headline, unchanged).

    When `origin` (short city name) is given, each plan also carries `ship_ev` —
    the SAME trades valued with shipping.expected_ship_value (the default shipper's
    all-or-nothing delivery probability priced in), added ALONGSIDE the headline
    `total`. Unknown cities just omit it. The default ship action never changes."""
    plans = []
    for city, rate in ship_rates.items():
        short = city.split(",")[0]
        trades = []
        for drug, info in market.items():
            buy, qty = info.get("price"), info.get("qty") or 0
            sell = matrix.get(city, {}).get(drug)
            if not buy or not sell:
                continue
            net_u = sell - buy - rate
            if net_u > 0 and qty > 0 and _arb_ok(buy, sell, rate, safety):
                trades.append((drug, buy, sell, net_u, qty, net_u * qty))
        plan = {"city": city, "rate": rate, "total": sum(t[5] for t in trades),
                "trades": sorted(trades, key=lambda t: -t[5])}
        if origin:
            # EV with delivery-failure priced in: EV of proceeds - buy outlay,
            # per drug, summed. unit_value is the (observed) destination sell.
            ship_ev = 0.0
            for d, buy, sell, net_u, qty, tot in trades:
                try:
                    ev = _shipping.expected_ship_value(
                        origin, short, qty, sell, DEFAULT_SHIPPER, DEFAULT_SHIP_DAYS)
                    ship_ev += ev - qty * buy
                except Exception:
                    ship_ev += tot          # fall back to headline net for this leg
            plan["ship_ev"] = ship_ev
        plans.append(plan)
    plans.sort(key=lambda p: -p["total"])
    return plans


def _find_info(boxes):
    for b in boxes:                              # OCR often renders "Info..." as "Into.."
        t = b["text"].lower()
        if len(t) <= 8 and ("nfo" in t or "nto" in t):
            return b
    return None


def _game_windows():
    try:
        return json.loads(subprocess.run([WINDOWS], capture_output=True, text=True).stdout)
    except Exception:
        return []


def world_is_open():
    return any("world" in w["title"].lower() for w in _game_windows())


def _popup_menu():
    return next((w for w in _game_windows()
                 if w["title"] == "" and w["bounds"]["w"] < 260 and w["bounds"]["h"] < 220), None)


def open_world_cities():
    """From the main window: click Info..., then the 'World cities...' item in the
    popup menu (a separate small window we click by real screen position). Verifies
    the menu actually opened and retries, so a missed Info-click doesn't fail silently."""
    for attempt in range(3):
        if world_is_open():
            return True
        base = _read(window="Drug Lord")
        if not base:
            time.sleep(0.2); continue
        mcal = calibrate(base["_ocr"]["frame"], window="Drug Lord")
        mx, cx, my, cy = mcal
        info = _find_info(base["_ocr"]["boxes"])
        if not info:
            continue
        click(int((info["x"] + info.get("w", 40) / 2 - cx) / mx),
              int((info["y"] + info.get("h", 12) / 2 - cy) / my))
        time.sleep(0.6)
        menu = _popup_menu()
        if not menu:                            # Info click missed — retry
            print(f"  (Info menu didn't open, retry {attempt+1})", file=sys.stderr)
            continue
        b = menu["bounds"]                      # items: Vaults, World drug prices,
        n, idx = 5, 2                           # World cities(=2), Shipment status, History
        click(int(b["x"] + b["w"] * 0.5), int(b["y"] + b["h"] * (idx + 0.5) / n))
        time.sleep(0.7)
        if world_is_open():
            return True
    return False


def read_status_from_main(tries=15):
    """Read the Status box (Location/Cash/Day/Rank) directly — the main window is up
    and the world window is closed, so the box is visible."""
    for _ in range(tries):
        main = _read(window="Drug Lord")
        if main and (main.get("status") or {}).get("location"):
            return main["status"]
        time.sleep(0.1)
    return {}


def close_world(world_cal):
    """Click the world window's Close button so the main window (Status box + Places
    button) is accessible. Returns True once the world window is gone."""
    st = window_capture()
    close = next((b for b in st["_ocr"]["boxes"] if b["text"].strip() == "Close"), None) if st else None
    if not close:
        return not world_is_open()
    click(*screen_of(close["x"] + close.get("w", 40) / 2,
                     close["y"] + close.get("h", 12) / 2, world_cal))
    time.sleep(1.0)
    return not world_is_open()


def match_current_city(market, matrix):
    """The current city is the one whose world prices equal 'The Market' (buyable
    drugs of the city you're in). Unique because per-city prices differ."""
    best, best_score = None, -1
    for city, drugs in matrix.items():
        score = sum(1 for d, info in market.items()
                    if info.get("price") is not None and drugs.get(d) == info["price"])
        if score > best_score:
            best, best_score = city, score
    return best


def main():
    args = sys.argv[1:]
    override_cap = int(args[args.index("--capacity") + 1]) if "--capacity" in args else None

    # Ensure the world window is open for the sweep (works from either starting window).
    if not world_is_open():
        print("  opening World Cities...", file=sys.stderr)
        if not open_world_cities():
            sys.exit("Could not open the World Cities window. Open it manually and rerun.")
        time.sleep(0.4)

    capacity, matrix, qtys, market, cal, fr, conf = sweep()
    capacity = override_cap or capacity or 20000

    # Close the world window so the main window (Status box + Places button) is usable;
    # read Location/Cash/Day. We reopen World Cities at the very end.
    close_world(cal)
    status = read_status_from_main()
    loc = status.get("location")
    current_key = None
    if loc:                                  # prefer the Location label, mapped to a matrix key
        short = loc.split(",")[0]
        current_key = next((c for c in matrix if c.startswith(short)), loc)
    if not current_key:                      # fall back to auto-match
        current_key = match_current_city(market, matrix) or next(iter(matrix), "?")
    extra = ""
    if status.get("day") is not None:
        extra = f"  |  Day {status['day']}/{status['total_days']}, rank {status.get('rank')}"
    print(f"  current city: {current_key}  |  buyable here: {len(market)} drugs{extra}",
          file=sys.stderr)

    # Prices to SELL at any city come from the world sweep; BUYING is restricted to
    # The Market (buyable subset) and capped by each drug's STOCK (not carry capacity,
    # since you can buy -> ship -> buy more). Unlimited cash assumed.
    state = {"current_city": current_key, "cash": 10**15, "capacity": capacity,
             "prices": matrix, "market": market, "inventory": [], "ship": True}
    if status.get("day") is not None and status["total_days"] - status["day"] <= 3:
        state["days_left"] = status["total_days"] - status["day"]
    with open("/tmp/dl2_state.json", "w") as f:
        json.dump(state, f, indent=2)
    print()
    header = ""
    if status.get("cash") is not None:
        header += f"Cash on hand: ${status['cash']:,}"
    if status.get("day") is not None:
        header += f"   Day {status['day']}/{status['total_days']}   Rank: {status.get('rank')}"
    if header:
        print(header + "\n")
    print(optimizer.format_plan(state, optimizer.optimize(state), unlimited=True))

    if "--no-ship" in args:
        return
    # Factor in shipping: measure the flat $/unit rate to every city (one session, main
    # window is accessible now), keep only drugs that still profit, pick best net city.
    print("\n" + "-" * 60, file=sys.stderr)
    print("  measuring shipping costs (one session)...", file=sys.stderr)
    rates = measure_ship_rates(current_key.split(",")[0], status.get("day"))
    if not rates:
        print("\n(no stock sample found — buy a few units of any drug so I can measure "
              "shipping, then rerun. Showing pre-shipping plan above.)")
        return
    plans = best_net_plan(market, matrix, rates)
    best = plans[0]
    # Save the recommended net plan so `dl2 buy` can execute it after you sign off.
    json.dump({"city": best["city"], "rate": best["rate"],
               "trades": [[t[0], t[4]] for t in best["trades"]]},
              open("/tmp/dl2_plan.json", "w"), indent=2)
    print()
    print("=" * 60)
    print(f"AFTER SHIPPING — BEST MOVE: fly/ship to {best['city']}  "
          f"(${best['rate']:,.0f}/unit shipping)")
    print("=" * 60)
    print(f"  Buy in {current_key} and ship (only drugs that clear shipping):")
    for drug, buy, sell, net_u, qty, prof in best["trades"]:
        print(f"    - {qty:>6,} {drug:10s} buy ${buy:,} sell ${sell:,}  "
              f"net ${net_u:,.0f}/u  = ${prof:,.0f}")
    print(f"  Net profit after shipping: ${best['total']:,.0f}")

    # Contenders: the leaders BEFORE shipping (gross) and AFTER (net), side by side, so
    # you can see how shipping reshuffles them (e.g. Beijing leads gross but not net).
    gross = {p["city"]: p for p in best_net_plan(market, matrix, {c: 0 for c in matrix})}
    netby = {p["city"]: p for p in plans}

    def top_share(plan):                          # % of net profit in the single top drug
        return (plan["trades"][0][5] / plan["total"]) if plan["trades"] and plan["total"] > 0 else 0

    top_gross = sorted(gross.values(), key=lambda p: -p["total"])[:4]
    cities = list(dict.fromkeys([p["city"] for p in plans[:4]] + [p["city"] for p in top_gross]))
    cities.sort(key=lambda c: -(netby[c]["total"] if c in netby else 0))

    print("\n  Contenders (gross = before shipping, net = after):")
    print(f"    {'city':20s} {'gross':>14s} {'net':>14s} {'ship/u':>8s} {'drugs':>6s} {'top-drug':>9s}")
    for c in cities:
        g = gross.get(c, {}).get("total", 0)
        n = netby.get(c)
        nt = n["total"] if n else 0
        share = top_share(n) if n else 0
        mark = "  <-- BEST" if n is best else ""
        print(f"    {c:20s} ${g:>13,.0f} ${nt:>13,.0f} ${n['rate'] if n else 0:>6,.0f} "
              f"{len(n['trades']) if n else 0:>6d} {share*100:>7.0f}%{mark}")
    print("  (top-drug = share of net profit in your single biggest drug — lower is less "
          "risky if a shipment is delayed, a price drops, or a drug can't be sold on arrival.)")
    print(f"\n  To buy this plan once you approve it:  ~/code/dl2/bin/dl2 buy")


def buy_main():
    """`dl2 buy` — execute the saved recommended plan, or a custom set of trades.
      dl2 buy                      buy the plan from the last `dl2 plan`
      dl2 buy Cocaine:14000 Kat:5  buy specific drug:qty pairs instead
    The Market (left of the main window) is visible even with World Cities open."""
    args = [a for a in sys.argv[1:] if a != "buy"]
    trades = []
    for a in args:
        if ":" in a:
            d, q = a.rsplit(":", 1)
            q = q.strip().lower()
            # "max"/"all" (or a bare "Drug:") buys the pre-filled MAX the game
            # offers — the whole affordable stock — so we never leave units behind
            # from a hand-typed guess. A number buys exactly that many.
            trades.append((d.strip(), None if q in ("max", "all", "") else int(q.replace(",", ""))))
    if not trades:
        try:
            plan = json.load(open("/tmp/dl2_plan.json"))
        except FileNotFoundError:
            sys.exit("No saved plan — run `dl2 plan` first, or pass drug:qty pairs.")
        trades = [(d, q) for d, q in plan["trades"]]
        print(f"Buying the recommended plan for {plan['city']} — {len(trades)} drugs:",
              file=sys.stderr)
    for d, q in trades:
        print(f"    {d}: {'MAX' if q is None else format(q, ',')}", file=sys.stderr)
    n = buy_plan(trades)
    print(f"\nDone — placed {n}/{len(trades)} buys. Open Places -> Shipping to send them "
          f"to the destination.")


def sell_main():
    """`dl2 sell` — at your current city, sell every held drug the local market will buy
    (double-click -> OK), skipping the rest. Re-run daily as the market rotates."""
    status = read_status_from_main()
    current = (status.get("location") or "?").split(",")[0]
    print(f"Selling inventory at {current or '?'}...", file=sys.stderr)
    all_sold, last_skipped = [], []
    for _ in range(6):                               # repeat: jitter-missed drugs retry each pass
        sold, last_skipped = sell_all_inventory(current)
        all_sold += sold
        if not sold:                                 # a full pass sold nothing -> done
            break
    print(f"\nSold: {', '.join(dict.fromkeys(all_sold)) if all_sold else '(nothing sellable here today)'}")
    if last_skipped:
        print(f"Still held (won't sell here today — try again after a day or two): "
              f"{', '.join(last_skipped)}")


def _chunks(qty, n):
    """Split qty into n as-equal-as-possible positive integers (sum == qty)."""
    base, rem = divmod(qty, n)
    return [base + (1 if i < rem else 0) for i in range(n)]


def ship_main():
    """`dl2 ship <City> [Drug ...] [--split N | --batch SIZE]` — ship held inventory
    to <City>, keeping only drugs that STILL profit there after an ARB_SAFETY
    (default 15%) sell-price drop (price*(1-safety) - ship_rate > 0). Uses prices +
    rates from the last `dl2 decide`.

    Shipments get delayed independently, and a delay on your ONE big position is
    what hurts. So by DEFAULT this splits into 3 shipments of NEAR-EQUAL VALUE:
    each shipment gets ~1/3 of every drug's units, hence ~1/3 of the total dollar
    value (value = qty x destination price), so a delay strands only ~a third.
      --split N   use N near-equal-value shipments instead of 3 (--split 1 = one
                  shipment of the full stock).
      --batch S   ignore the value split; cap each shipment at S units instead.
    Optional drug names restrict what to ship."""
    args = [a for a in sys.argv[1:] if a != "ship"]
    nsplit, batch = 3, None                    # default: 3 near-equal-VALUE shipments
    if "--split" in args:
        i = args.index("--split"); nsplit = max(1, int(args[i + 1])); del args[i:i + 2]
    if "--batch" in args:
        i = args.index("--batch"); batch = int(args[i + 1].replace(",", "")); del args[i:i + 2]
    if not args:
        sys.exit("usage: dl2 ship <City> [Drug ...] [--split N | --batch SIZE]")
    dest_arg, only = args[0], {a for a in args[1:]}
    try:
        state = json.load(open("/tmp/dl2_state.json"))
    except FileNotFoundError:
        sys.exit("No saved prices — run `dl2 decide` first.")
    matrix, rates = state["prices"], (state.get("rates") or {})
    dest_key = next((c for c in matrix if c.lower().startswith(dest_arg.lower())), None)
    if not dest_key:
        sys.exit(f"Unknown city '{dest_arg}'. Options: {', '.join(sorted(matrix))}")
    rate = rates.get(dest_key)
    if rate is None:
        sys.exit(f"No shipping rate to {dest_key} in the last decide — re-run `dl2 decide`.")

    # The World Cities window (left open by `decide`) physically covers the main
    # window's inventory box AND the Places button, so close it before shipping.
    if world_is_open():
        print("  closing World Cities so the main window is usable...", file=sys.stderr)
        w = _read(window="World")
        if w:
            close_world(calibrate(w["_ocr"]["frame"]))
        time.sleep(0.4)

    base = _read(window="Drug Lord")
    if not base:
        sys.exit("Can't read the main window — bring Drug Lord frontmost and retry.")
    inv = _inventory_with_qty(base["_ocr"])
    if not inv:
        sys.exit("No inventory detected to ship.")
    _status = read_status_from_main()
    current_short = ((_status.get("location") or state.get("current") or "").split(",")[0])
    ship_day = _status.get("day")
    dest_short = dest_key.split(",")[0]
    cp = matrix[dest_key]

    ship_list, skip = [], []
    for d, q in inv.items():
        if only and d not in only:
            continue
        p = cp.get(d)
        if p and p * (1 - ARB_SAFETY) - rate > 0:
            ship_list.append((d, q, p, p * (1 - ARB_SAFETY) - rate))
        else:
            skip.append((d, q, p))
    print(f"Shipping to {dest_short} (rate ${rate:,.0f}/u; keep if profitable after a "
          f"{ARB_SAFETY:.0%} price drop):", file=sys.stderr)
    for d, q, p, netu in ship_list:
        print(f"  SHIP {d:9s} {q:>7,} u  ({dest_short} ${p:,}, net/u after drop+ship ${netu:,.0f})",
              file=sys.stderr)
    for d, q, p in skip:
        why = f"${p:,} won't clear ${rate:,.0f} ship after drop" if p else "no price there"
        print(f"  skip {d:9s} {q:>7,} u  ({why})", file=sys.stderr)
    if not ship_list:
        sys.exit("Nothing clears the margin rule — nothing to ship.")
    want = [d for d, _, _, _ in ship_list]
    price_of = {d: p for d, _, p, _ in ship_list}
    qty_of = {d: q for d, q, _, _ in ship_list}        # full held qty (for qty=None shipments)

    # Build the shipment rounds so each draws an INDEPENDENT delay roll:
    #   default / --split N -> N shipments of NEAR-EQUAL VALUE. Every drug is
    #     split into N near-equal unit chunks (_chunks) and chunk r goes to
    #     shipment r, so each shipment carries ~1/N of every drug = ~1/N of the
    #     total dollar value. A delay then strands only ~1/N of the position.
    #   --batch S -> cap each shipment at S units (a drug spanning >S units splits
    #     into ceil(qty/S) rounds); values are not balanced.
    #   --split 1 -> a single shipment of the full stock.
    if batch:
        plans = {d: _chunks(q, max(1, -(-q // batch))) for d, q, _, _ in ship_list}
        rounds = max(len(v) for v in plans.values())
        shipments = [[(d, plans[d][r]) for d in want
                      if r < len(plans[d]) and plans[d][r] > 0]
                     for r in range(rounds)]
    elif nsplit > 1:
        plans = {d: _chunks(q, nsplit) for d, q, _, _ in ship_list}
        shipments = [[(d, plans[d][r]) for d in want if plans[d][r] > 0]
                     for r in range(nsplit)]
    else:
        shipments = [[(d, None) for d in want]]        # --split 1 = full stock, one go
    shipments = [s for s in shipments if s]            # drop any empty round

    print(f"\nSending {len(shipments)} shipment(s) to {dest_short} "
          f"(each draws its own delay roll):", file=sys.stderr)
    loaded_all = []
    for i, trades in enumerate(shipments, 1):
        val = sum((qty_of.get(d, 0) if q is None else q) * price_of.get(d, 0) for d, q in trades)
        desc = ", ".join(f"{d} {'all' if q is None else format(q, ',')}" for d, q in trades)
        print(f"  shipment {i}/{len(shipments)}: {desc}  (~${val:,.0f} value)",
              file=sys.stderr)
        loaded_all += ship_drugs(trades, current_short, dest_short, ship_day)

    shipped_drugs = set(loaded_all)
    missed = [d for d in want if d not in shipped_drugs]
    print(f"\n{'✓ sent' if loaded_all else 'shipping FAILED'} "
          f"{len(shipments)} shipment(s) to {dest_short}; drugs moved: "
          f"{', '.join(dict.fromkeys(loaded_all)) or '(none)'}")
    if missed:
        print(f"⚠ NOT shipped (still at {current_short}): {', '.join(missed)} — "
              f"re-run `dl2 ship {dest_short} {' '.join(missed)}` to retry.")
    else:
        print("Now FLY there, then `dl2 sell`.")


if __name__ == "__main__":
    cmd = sys.argv[1:]
    if "audit" in cmd:
        import audit as _audit
        sys.argv = [sys.argv[0]] + [a for a in cmd if a != "audit"]
        _audit.main()
    elif "ship" in cmd:
        ship_main()
    elif "buy" in cmd:
        buy_main()
    elif "sell" in cmd:
        sell_main()
    elif "decide" in cmd:
        decide_main()
    else:
        main()
