#!/usr/bin/env python3
"""
Parse a dl2read OCR capture into structured game data.

The game's world window has TWO views that the player can toggle:
  * "World Prices"  — left list = DRUGS, right panel = the selected drug's price
                      in every CITY.            (right panel header: "City")
  * "World Cities"  — left list = CITIES, right panel = the selected city's price
                      for every DRUG.           (right panel header: "Drug")
Both also show "The Market" (current city, all drugs) on the far left.

We detect the view from the "Drug Name" / "City Name" list header, locate columns
by their header labels (robust to window/scale), and return a normalized structure.

Usage: python3 parse.py capture.json
"""
import json
import re
import sys
import difflib

CANON_DRUGS = ["Cocaine", "Crack", "Ecstacy", "Hashish", "Heroin", "Ice", "Kat",
               "LSD", "MDA", "Morphine", "Mushrooms", "Opium", "PCP", "Peyote",
               "Pot", "Special K", "Speed"]

CANON_CITIES = ["Austin, USA", "Beijing, China", "Boston, USA", "Detroit, USA",
                "London, England", "Los Angeles, USA", "Miami, USA", "Moscow, Russia",
                "New York, USA", "Paris, France", "San Francisco, USA",
                "St Petersburg, Russia", "Sydney, Australia", "Toronto, Canada",
                "Vancouver, Canada"]
CITY_SHORT = [c.split(",")[0] for c in CANON_CITIES]

# Loan sharks (the five lenders). Kept here as OCR-canon names (like CANON_DRUGS/
# CANON_CITIES) so parse.py stays screen-free and dl2model-free; the caller cross-
# checks the OCR-read rate/due against dl2model.constants.LOAN_SHARKS.
CANON_SHARKS = ["Odd Lenny", "One-eyed Wilbur", "Laughing Max",
                "Strange ear Leonard", "Buddles"]

_HOMOGLYPH = str.maketrans({"Р": "P", "С": "C", "Т": "T", "А": "A", "В": "B",
                            "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
                            "Х": "X", "у": "y", "р": "p", "с": "c"})


def _num(s):
    s = s.replace(",", "").replace(" ", "").strip()
    return int(s) if re.fullmatch(r"\d+", s) else None


def _clean_drug(text):
    t = text.translate(_HOMOGLYPH)
    t = re.sub(r"^[^A-Za-z]+", "", t).strip()
    for d in CANON_DRUGS:
        if t.lower().replace(" ", "") == d.lower().replace(" ", ""):
            return d
    cand = difflib.get_close_matches(t, CANON_DRUGS, n=1, cutoff=0.6)
    if cand:
        return cand[0]
    for d in CANON_DRUGS:
        if t.lower().endswith(d.lower()):
            return d
    return None


def _clean_city(text):
    m = difflib.get_close_matches(text.strip(), CANON_CITIES, n=1, cutoff=0.5)
    if m:
        return m[0]
    m = difflib.get_close_matches(text.strip(), CITY_SHORT, n=1, cutoff=0.5)
    return CANON_CITIES[CITY_SHORT.index(m[0])] if m else None


def _clean_shark(text):
    """Fuzzy-match an OCR fragment to a canonical loan-shark name, or None."""
    t = text.translate(_HOMOGLYPH).strip()
    t = re.sub(r"^[^A-Za-z]+", "", t).strip()
    if not t:
        return None
    for s in CANON_SHARKS:
        if t.lower() == s.lower():
            return s
    m = difflib.get_close_matches(t, CANON_SHARKS, n=1, cutoff=0.6)
    return m[0] if m else None


# Loan table column x-bands, from the live "Dialog" (Places -> Finances) window.
# Header row: Name | P... (max-loan multiplier) | R.. (rate%) | Days (term) |
# Debt (owed to that shark) | Days Left. The panel prints BARE numbers (no "%"/
# "day" suffix), so columns are located by x, not by text. Bounds are the
# midpoints between the observed value columns (mult~502 rate~540 days~606
# owed~675 daysleft~806); numeric loan cells start at x>=490 (left of that is the
# main window's inventory bleeding through).
_LOAN_COL_BANDS = [("mult", 490, 521), ("rate", 521, 573), ("due", 573, 640),
                   ("owed", 640, 740), ("days_left", 740, 10 ** 9)]


def _loan_int(text):
    """Int from an OCR loan cell: keep digits/commas, drop stray glyphs ("'1"->1,
    "12,800"->12800). Returns None if there is no digit."""
    m = re.search(r"\d[\d,]*", text.translate(_HOMOGLYPH))
    return int(m.group(0).replace(",", "")) if m else None


def parse_loan_window(ocr):
    """Parse the loan-shark section of the Finances ("Dialog") window OCR.

    You can owe several sharks at once, so this reads a PORTFOLIO of loans, one row
    per shark:
      sharks[name] = {"mult", "rate", "due", "owed", "days_left"}  (any may be None)
    plus:
      total_debt   int|None   — the "Debt: N" total at the bottom of the panel
      loans        list       — [{shark, owed, rate, due, days_left}] for owed>0
      lender/principal/rate/due_in — single-loan back-compat: the MOST URGENT
                   outstanding loan (soonest days_left, then highest rate);
                   principal = total_debt.

    Columns carry BARE numbers (no "%"/"day" text), so cells are placed by x-band
    (_LOAN_COL_BANDS) and rows by nearest shark-name y. Robust to the OCR dropping
    individual cells (common on the term / days-left / multiplier columns).
    """
    boxes = ocr.get("boxes", [])

    # shark-name anchors (left column of the loan table, x ~300-360)
    anchors = [(b, _clean_shark(b["text"])) for b in boxes
               if 290 <= b["x"] <= 370 and _clean_shark(b["text"])]
    sharks = {name: {"mult": None, "rate": None, "due": None,
                     "owed": None, "days_left": None} for _, name in anchors}

    # assign each numeric loan cell (x>=490, within the table's y-span) to the
    # nearest shark row, then to a column by its x-band.
    for b in boxes:
        if b["x"] < 490:
            continue
        val = _loan_int(b["text"])
        if val is None:
            continue
        near = min(anchors, key=lambda a: abs(a[0]["y"] - b["y"]), default=None)
        if not near or abs(near[0]["y"] - b["y"]) > 12:
            continue
        col = next((c for c, lo, hi in _LOAN_COL_BANDS if lo <= b["x"] < hi), None)
        if col and sharks[near[1]][col] is None:
            sharks[near[1]][col] = val

    # total debt: the "Debt: 32,580" summary line. Require the COLON so it can't
    # match the bare "Debt" column header (which is followed by the first owed cell).
    total_debt = None
    md = re.search(r"Debt:\s*\$?([\d][\d.,]*)",
                   "  ".join(b["text"] for b in boxes))
    if md:
        total_debt = int(re.sub(r"[.,]", "", md.group(1)))

    loans = [{"shark": name, "owed": s["owed"], "rate": s["rate"],
              "due": s["due"], "days_left": s["days_left"]}
             for name, s in sharks.items() if (s["owed"] or 0) > 0]
    # most-urgent outstanding loan for the single-loan back-compat fields
    urgent = min(loans, key=lambda L: (L["days_left"] if L["days_left"] is not None else 999,
                                       -(L["rate"] or 0)), default=None)

    return {"sharks": sharks, "total_debt": total_debt, "loans": loans,
            "lender": urgent["shark"] if urgent else None,
            "principal": total_debt,
            "rate": urgent["rate"] if urgent else None,
            "due_in": urgent["days_left"] if urgent else None}


def _rows(boxes, ytol=12):
    rows = []
    for b in sorted(boxes, key=lambda b: b["y"]):
        for r in rows:
            if abs(r["y"] - b["y"]) <= ytol:
                r["items"].append(b)
                r["y"] = (r["y"] * (len(r["items"]) - 1) + b["y"]) // len(r["items"])
                break
        else:
            rows.append({"y": b["y"], "items": [b]})
    for r in rows:
        r["items"].sort(key=lambda b: b["x"])
    return rows


def _hx(boxes, label):
    b = next((b for b in boxes if b["text"].strip() == label), None)
    return (b["x"], b["y"]) if b else (None, None)


def _parse_list(boxes, name_x, qty_x, price_x, y0, x_lo, x_hi, is_city):
    """Parse a Name/Qty/Price list within an x-band, below header y0."""
    cols = [("name", name_x), ("qty", qty_x if qty_x else name_x + 150),
            ("price", price_x)]
    region = [b for b in boxes if x_lo <= b["x"] < x_hi and b["y"] > y0 + 8]
    out = []
    for r in _rows(region):
        rec = {"name": None, "qty": None, "price": None, "price_ok": False}
        for b in r["items"]:
            col = min(cols, key=lambda kv: abs(kv[1] - b["x"]))[0]
            if col == "name" and _num(b["text"]) is None:
                rec["name"] = (rec["name"] + " " + b["text"]) if rec["name"] else b["text"]
            elif _num(b["text"]) is not None and rec[col] is None:
                rec[col] = _num(b["text"])
                if col == "price":
                    # Did the fixed-font digit reader read this price confidently,
                    # or fall back to Vision? (digits.refine_numbers tags the box.)
                    rec["price_ok"] = bool(b.get("_digit_ok", False))
        if rec["price"] is not None:
            out.append(rec)
    return out


def parse(ocr):
    boxes = ocr["boxes"]

    current_city = None
    for b in boxes:
        m = re.match(r"Welcome to (.+?)!", b["text"])
        if m:
            current_city = m.group(1).strip()
            break

    # Inventory box header: "Your <stash name> (held/capacity)". The stash name
    # changes as you level up (pants pocket -> island paradise -> ...), so match
    # the parenthesized held/capacity pattern rather than a fixed name. It's the
    # only parenthesized N/N on the main window (Day "X/Y" has no parens).
    held = capacity = None
    for b in boxes:
        m = re.search(r"\(\s*([\d,]+)\s*/\s*([\d,]+)\s*\)", b["text"])
        if m:
            held = int(m.group(1).replace(",", ""))
            capacity = int(m.group(2).replace(",", ""))
            break

    # Status box (main window, bottom-right; visible only when the world window is
    # closed): Location / Cash / Day X/Y / Rank / Bank / Debt. OCR may split each
    # label from its value into separate boxes, so match over the joined text (in
    # reading order) rather than per box.
    status = {"location": None, "cash": None, "day": None, "total_days": None,
              "rank": None, "bank": None, "debt": None}
    joined = "  ".join(b["text"] for b in sorted(boxes, key=lambda b: (b["y"], b["x"])))
    m = re.search(r"Location:\s*([A-Za-z][A-Za-z .]*,\s*[A-Za-z .]+)", joined)
    if m:
        status["location"] = m.group(1).strip()
    # Money fields: the OCR renders the thousands separator inconsistently as a
    # comma OR a period (e.g. "271,339" vs "271.291", even mixed like
    # "2,722.356,320"), so accept both and strip both. Amounts are whole dollars
    # (no cents shown), so a "." is never a decimal point here.
    m = re.search(r"Cash:\s*\$?([\d.,]{2,})", joined)
    if m:
        status["cash"] = int(re.sub(r"[.,]", "", m.group(1)))
    m = re.search(r"Day:?\s*(\d+)\s*/\s*(\d+)", joined)
    if m:
        status["day"], status["total_days"] = int(m.group(1)), int(m.group(2))
    else:
        # The "Day:" label and its "NN/NN" value are separate OCR boxes, and the
        # value often sits a few px ABOVE the label, so it lands BEFORE "Day:" in
        # the y-sorted joined text and the regex above misses it. Anchor on the
        # label box instead and read the NN/NN token on its row (nearest in y),
        # which also excludes the "1/20000" inventory-capacity token far above.
        lab = next((b for b in boxes if b["text"].strip().lower().rstrip(":") == "day"), None)
        if lab:
            cand = [b for b in boxes if re.fullmatch(r"\d+\s*/\s*\d+", b["text"].strip())
                    and abs(b["y"] - lab["y"]) <= 12]
            if cand:
                best = min(cand, key=lambda b: abs(b["y"] - lab["y"]))
                d, t = re.split(r"\s*/\s*", best["text"].strip())
                status["day"], status["total_days"] = int(d), int(t)
    m = re.search(r"Rank:\s*([A-Za-z][A-Za-z -]*)", joined)
    if m:
        status["rank"] = m.group(1).strip()
    m = re.search(r"Bank:\s*\$?([\d.,]+)", joined)
    if m:
        status["bank"] = int(re.sub(r"[.,]", "", m.group(1)))
    m = re.search(r"Debt:\s*\$?([\d.,]+)", joined)
    if m:
        status["debt"] = int(re.sub(r"[.,]", "", m.group(1)))

    # view + the left navigation list header (absent when only the main window shows)
    if any(b["text"].strip() == "City Name" for b in boxes):
        view = "by_city"; list_x, list_y = _hx(boxes, "City Name")
    else:
        view = "by_drug"; list_x, list_y = _hx(boxes, "Drug Name")
    split = list_x if list_x is not None else 10 ** 9   # market/right boundary

    # ---- The Market (far-left: Name/Qty/Price columns) ----
    nx, ny = _hx(boxes, "Name")
    market = {}
    if nx is not None:
        mq = min([_hx(boxes, h)[0] for h in ("Qty", "Bty", "Oty")
                  if _hx(boxes, h)[0] is not None and _hx(boxes, h)[0] < split] or [nx + 120])
        mp = min([b["x"] for b in boxes if b["text"].strip() == "Price" and b["x"] < split] or [nx + 240])
        # include the Price column: bound below the right list if present, else past mp
        m_hi = (list_x - 20) if list_x is not None else (mp + 120)
        for rec in _parse_list(boxes, nx, mq, mp, ny, 0, m_hi, is_city=False):
            drug = _clean_drug(rec["name"]) if rec["name"] else None
            if drug:
                market[drug] = {"qty": rec["qty"], "price": rec["price"],
                                "price_ok": rec["price_ok"]}

    # ---- Right panel (the varying list) ----
    right_label = "City" if view == "by_drug" else "Drug"
    rx, ry = _hx(boxes, right_label)
    right_prices = {}
    if rx is not None:
        lb = (list_x if list_x is not None else rx - 120) + 100   # left bound of right cols
        rq = min([b["x"] for b in boxes if b["text"].strip() in ("Qty", "Bty", "Oty")
                  and b["x"] > lb] or [rx + 180])
        rp = min([b["x"] for b in boxes if b["text"].strip() == "Price" and b["x"] > lb] or [rx + 300])
        recs = _parse_list(boxes, rx, rq, rp, ry, rx - 40, 10 ** 9, is_city=(view == "by_drug"))
        if view == "by_drug":
            if len(recs) == len(CANON_CITIES):
                for canon, rec in zip(CANON_CITIES, recs):
                    right_prices[canon] = {"qty": rec["qty"], "price": rec["price"],
                                           "price_ok": rec["price_ok"]}
            else:
                for rec in recs:
                    name = _clean_city(rec["name"]) if rec["name"] else None
                    if name:
                        right_prices[name] = {"qty": rec["qty"], "price": rec["price"],
                                              "price_ok": rec["price_ok"]}
        else:  # by_city: right panel is a drug list
            for rec in recs:
                drug = _clean_drug(rec["name"]) if rec["name"] else None
                if drug:
                    right_prices[drug] = {"qty": rec["qty"], "price": rec["price"],
                                          "price_ok": rec["price_ok"]}

    # which left item is selected? (match right panel's current-city entry to market)
    selected = None
    if view == "by_drug":
        cur = next((c for c in right_prices if current_city and c.startswith(current_city)), None)
        if cur:
            p = right_prices[cur]["price"]
            selected = next((d for d, info in market.items() if info["price"] == p), None)
    else:  # by_city: if the selected city IS the current city, right == market
        if market and all(d in right_prices and right_prices[d]["price"] == market[d]["price"]
                          for d in market):
            selected = _clean_city(current_city) if current_city else None

    result = {"current_city": current_city, "market": market, "capacity": capacity,
              "held": held, "view": view, "right_prices": right_prices,
              "selected": selected, "status": status}
    # backward-compat aliases for the by_drug (World Prices) callers
    if view == "by_drug":
        result["city_prices"] = right_prices
        result["selected_drug"] = selected
    else:
        result["city_prices"] = {}
        result["selected_drug"] = None
    return result


def main():
    ocr = json.load(open(sys.argv[1]) if len(sys.argv) > 1 else sys.stdin)
    print(json.dumps(parse(ocr), indent=2))


if __name__ == "__main__":
    main()
