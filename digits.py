#!/usr/bin/env python3
"""
Exact digit reader for Drug Lord 2 prices/quantities — replaces Vision OCR for
numbers.

Why: Vision occasionally *systematically* misreads a price in the game's fixed
bitmap font (e.g. 1,780 -> a huge number), and re-running the same OCR on the
same pixels just repeats the same wrong answer. Multiplied across tens of
thousands of units, one bad digit wrecks the optimizer.

The game draws every number in a FIXED bitmap font (dark digits, right-aligned,
comma thousands-separators, light background). So we can read numbers exactly by
template matching:

  1. crop the number's cell (Vision gives us the box, we only trust its LOCATION)
  2. binarize (dark ink vs light background)
  3. segment glyphs by zero-ink column gaps
  4. drop comma / period separators (short glyphs sitting low in the cell)
  5. classify each remaining glyph against learned 0-9 templates (grayscale NCC)

Templates are bootstrapped once from Vision's own *confident* reads (a capture
where Vision reads a clean digit string and our segmentation yields the same
digit count): each glyph is kept as a sharp exemplar (deduped, several per
digit) and cached to disk. After that, reads are pure pixel-match and
deterministic — the same misread never recurs. Classification is nearest
exemplar, gated on a wide margin over the runner-up (so 8-vs-0 style near-ties
are rejected, not guessed).

CLI:
  python3 digits.py bootstrap capture.json [capture.png]   # learn templates
  python3 digits.py read      capture.json [capture.png]   # read every number
  python3 digits.py show                                   # dump template stats

If capture.png is omitted it is inferred from the json's sibling (same stem) or
from a "png" key in the json.

Library:
  from digits import DigitReader
  r = DigitReader()                 # loads cached templates if present
  r.read_number(gray, box)          # -> int or None
"""
import json
import os
import re
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(HERE, "digit_templates.npz")

# Normalization canvas for a single glyph (grayscale ink map, ink = high).
CANVAS_H = 22
CANVAS_W = 16
# A glyph shorter than this fraction of the tallest glyph in the cell is treated
# as a comma / period separator, not a digit.
SEP_HEIGHT_FRAC = 0.6
# A digit is trusted when its best-matching template clears MIN_CORR *and* wins
# by at least MIN_MARGIN over the runner-up. The margin is the load-bearing test:
# the fixed font makes the correct glyph win by a wide margin every time, while a
# corrupted / ambiguous glyph (the failure we must reject, not guess) leaves the
# top two templates close. MIN_CORR is only a floor to reject noise/blank cells.
MIN_CORR = 0.55
MIN_MARGIN = 0.04
# When the top-two correlation gap is below this, re-decide the winner with the
# discriminative region test (fixes 8-vs-0 and similar close, outline-dominated calls).
TIE_MARGIN = 0.12


# --------------------------------------------------------------------------- #
# image helpers
# --------------------------------------------------------------------------- #
def load_gray(path):
    return np.asarray(Image.open(path).convert("L"), dtype=np.uint8)


def _crop(gray, box, pad=3):
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    y0, y1 = max(0, y - pad), min(gray.shape[0], y + h + pad)
    x0, x1 = max(0, x - pad), min(gray.shape[1], x + w + pad)
    return gray[y0:y1, x0:x1]


def _binarize(cell):
    """Boolean ink mask (True = dark stroke). Threshold at the midpoint between
    the cell's darkest and lightest pixel — robust because background is near
    white (255) and ink near black."""
    lo, hi = int(cell.min()), int(cell.max())
    if hi - lo < 40:            # ~uniform: no glyphs here
        return np.zeros(cell.shape, dtype=bool)
    th = (lo + hi) // 2
    return cell < th


def _main_row_band(ink):
    """Restrict the ink mask to the number's own text line. Vision's boxes for
    the tightly-stacked right-panel rows can overrun into the row below, so ink
    appears in two separated row-bands; the number is the *tallest* contiguous
    band. Returns a mask with every other band zeroed."""
    row_has = ink.any(axis=1)
    runs, i, n = [], 0, len(row_has)
    while i < n:
        if row_has[i]:
            j = i
            while j < n and row_has[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    if len(runs) <= 1:
        return ink
    a, b = max(runs, key=lambda r: r[1] - r[0])   # tallest band = the digits
    out = np.zeros_like(ink)
    out[a:b, :] = ink[a:b, :]
    return out


def _column_groups(ink):
    """Contiguous runs of columns that contain ink -> (start, end) exclusive."""
    cols = ink.any(axis=0)
    groups, i, n = [], 0, len(cols)
    while i < n:
        if cols[i]:
            j = i
            while j < n and cols[j]:
                j += 1
            groups.append((i, j))
            i = j
        else:
            i += 1
    return groups


def _glyph_canvas(ink_sub):
    """Tight-crop a glyph's ink mask and render it onto a fixed grayscale canvas,
    scaled to the canvas height (fixed font -> all digits same height), aspect
    preserved, horizontally centered. Returns float32 canvas, ink in [0,1]."""
    rows = np.where(ink_sub.any(axis=1))[0]
    cols = np.where(ink_sub.any(axis=0))[0]
    if len(rows) == 0 or len(cols) == 0:
        return None
    tight = ink_sub[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    gh, gw = tight.shape
    scale = CANVAS_H / gh
    new_w = max(1, min(CANVAS_W, int(round(gw * scale))))
    glyph = np.asarray(
        Image.fromarray((tight * 255).astype(np.uint8)).resize(
            (new_w, CANVAS_H), Image.BILINEAR),
        dtype=np.float32) / 255.0
    canvas = np.zeros((CANVAS_H, CANVAS_W), dtype=np.float32)
    x0 = (CANVAS_W - new_w) // 2
    canvas[:, x0:x0 + new_w] = glyph
    return canvas


def segment_glyphs(cell):
    """Segment a number cell into per-digit grayscale canvases, dropping comma /
    period separators. Returns the digit canvases in left-to-right order."""
    ink = _binarize(cell)
    if not ink.any():
        return []
    ink = _main_row_band(ink)
    groups = _column_groups(ink)
    if not groups:
        return []
    # height of each group (to spot short separators)
    heights = []
    for a, b in groups:
        rows = np.where(ink[:, a:b].any(axis=1))[0]
        heights.append(rows.max() - rows.min() + 1 if len(rows) else 0)
    tall = max(heights) if heights else 0
    digits = []
    for (a, b), hgt in zip(groups, heights):
        if tall and hgt < SEP_HEIGHT_FRAC * tall:
            continue                       # comma / period separator
        canvas = _glyph_canvas(ink[:, a:b])
        if canvas is not None:
            digits.append(canvas)
    return digits


# --------------------------------------------------------------------------- #
# reader
# --------------------------------------------------------------------------- #
def _corr(a, b):
    """Normalized cross-correlation of two equal-shape arrays, in [-1, 1]."""
    a = a - a.mean()
    b = b - b.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float((a * b).sum() / (na * nb))


# Cap on stored exemplars per digit (keeps the cache small; more than enough to
# cover the font's rendering variants).
MAX_EXEMPLARS = 12
# Two exemplars this similar are treated as duplicates when learning.
DUP_CORR = 0.985


class DigitReader:
    def __init__(self, path=TEMPLATE_PATH):
        self.path = path
        self.templates = {}          # digit char -> list[float32 canvas] exemplars
        self._load()

    # ---- persistence ----
    def _load(self):
        if os.path.exists(self.path):
            data = np.load(self.path)
            for k in data.files:            # keys like "d8_3"
                ch = k[1]
                self.templates.setdefault(ch, []).append(data[k])

    def save(self):
        arrays = {}
        for ch, exs in self.templates.items():
            for i, ex in enumerate(exs):
                arrays[f"d{ch}_{i}"] = ex
        np.savez_compressed(self.path, **arrays)

    @property
    def ready(self):
        return all(self.templates.get(str(d)) for d in range(10))

    # ---- classification ----
    def _mean(self, ch):
        """Cached average exemplar for a digit."""
        if not hasattr(self, "_means"):
            self._means = {}
        if ch not in self._means:
            self._means[ch] = np.mean(self.templates[ch], axis=0)
        return self._means[ch]

    def _discriminate(self, canvas, a, b):
        """Decide between two close candidates by comparing the glyph to each ONLY
        in the region where their templates differ most (e.g. the middle crossbar
        that separates 8 from 0). Whole-glyph correlation is dominated by the shared
        outline, so a faint distinguishing stroke gets outvoted — this looks only
        where they actually differ. Returns the winning char."""
        ta, tb = self._mean(a), self._mean(b)
        diff = np.abs(ta - tb)
        if diff.max() <= 0:
            return a
        mask = diff > diff.max() * 0.4
        if mask.sum() < 3:
            return a
        da = float(np.sum((canvas[mask] - ta[mask]) ** 2))
        db = float(np.sum((canvas[mask] - tb[mask]) ** 2))
        return a if da <= db else b

    def classify(self, canvas):
        """Nearest-exemplar match with a discriminative tie-break on close calls.
        Returns (digit_char, best_corr, margin). `margin` is the top-two correlation
        gap — kept as-is so a genuinely close read still reads as low-confidence
        (and gets flagged), but the WINNING digit is re-decided by `_discriminate`
        when the gap is small, which fixes the 8-vs-0 (crossbar) confusion."""
        best = {}                           # char -> best corr to that digit
        for ch, exs in self.templates.items():
            best[ch] = max(_corr(canvas, ex) for ex in exs)
        if not best:
            return None, 0.0, 0.0
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        best_d, best_c = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        margin = best_c - second
        if len(ranked) >= 2 and margin < TIE_MARGIN:
            win = self._discriminate(canvas, best_d, ranked[1][0])
            if win != best_d:                # crossbar/region test overrode the outline
                best_d, best_c = win, best[win]
        return best_d, best_c, margin

    def read_number(self, gray, box, pad=3):
        """Read the integer in `box`. Returns int, or None if unreadable / a
        digit fell below the confidence threshold."""
        digits = segment_glyphs(_crop(gray, box, pad))
        if not digits or not self.ready:
            return None
        out = []
        for canvas in digits:
            d, corr, margin = self.classify(canvas)
            if d is None or corr < MIN_CORR or margin < MIN_MARGIN:
                return None
            out.append(d)
        try:
            return int("".join(out))
        except ValueError:
            return None

    def read_debug(self, gray, box, pad=3):
        """Like read_number but returns per-digit diagnostics."""
        digits = segment_glyphs(_crop(gray, box, pad))
        info = []
        for canvas in digits:
            info.append(self.classify(canvas))
        val = None
        if info and self.ready and all(
                c >= MIN_CORR and m >= MIN_MARGIN for _, c, m in info):
            val = int("".join(d for d, _, _ in info))
        return val, info

    # ---- bootstrapping ----
    def _add_exemplar(self, ch, canvas):
        """Store a glyph as a new exemplar unless it duplicates one we already
        have (keeps the set diverse and small)."""
        exs = self.templates.setdefault(ch, [])
        if any(_corr(canvas, ex) >= DUP_CORR for ex in exs):
            return False
        if len(exs) >= MAX_EXEMPLARS:
            return False
        exs.append(canvas.astype(np.float32))
        return True

    def learn_from_capture(self, gray, boxes, pad=3):
        """Accumulate exemplars from Vision boxes whose text is a clean number
        and whose segmentation yields exactly that many digits. Keeps sharp
        per-digit exemplars (deduped) rather than one blurred average. Returns
        the count of number cells used."""
        used = 0
        for b in boxes:
            txt = b["text"].strip()
            if not re.fullmatch(r"[\d,]{1,13}", txt):
                continue
            label = txt.replace(",", "")
            if not label.isdigit():
                continue
            glyphs = segment_glyphs(_crop(gray, b, pad))
            if len(glyphs) != len(label):
                continue        # segmentation disagrees -> don't trust this one
            for ch, canvas in zip(label, glyphs):
                self._add_exemplar(ch, canvas)
            used += 1
        return used


# --------------------------------------------------------------------------- #
# pipeline integration
# --------------------------------------------------------------------------- #
_SHARED_READER = None


def get_reader():
    """Process-wide cached DigitReader (templates load once)."""
    global _SHARED_READER
    if _SHARED_READER is None:
        _SHARED_READER = DigitReader()
    return _SHARED_READER


def refine_numbers(ocr, png_path, reader=None):
    """In-place: read every numeric box exactly from the fixed-font pixels.

    Philosophy (hard rule): NEVER drop a value and NEVER hide uncertainty. Every
    number cell keeps a value, and each is tagged VERIFIED or not:
      * VERIFIED when the template reader's glyphs win by a clear margin, OR when
        the template read (even at a narrow margin) AGREES with Vision — two
        independent readers concurring is strong confirmation. (This is what stops
        real spikes like a $25,844 price from being thrown away over a 0.03 margin.)
      * NOT VERIFIED when the template read is shaky and Vision disagrees, or the
        glyphs can't be read at all. We still keep the best available value, but
        flag it loudly so it is checked — never silently discarded.

    Tags per box: `b["_digit_ok"]` (True = verified), `b["_vision"]` (original text
    when changed). `ocr["_digits"]` carries counts plus `unverified` — a list of
    {x, y, text, read} for every cell that could not be verified, so callers can
    surface exactly what to double-check.
    """
    reader = reader or get_reader()
    stats = {"read": 0, "changed": 0, "verified": 0, "unverified": []}
    if not reader.ready:
        ocr["_digits"] = stats
        return ocr
    gray = load_gray(png_path)
    for b in ocr.get("boxes", []):
        txt = b["text"].strip()
        if not re.fullmatch(r"[\d,]{1,13}", txt):
            continue
        stats["read"] += 1
        strict, info = reader.read_debug(gray, b)     # strict passes the margin gate
        # relaxed read: glyphs classify with decent correlation, ignoring the margin
        relaxed = None
        if info and all(c >= MIN_CORR for _, c, _ in info):
            try:
                relaxed = int("".join(d for d, _, _ in info))
            except ValueError:
                relaxed = None
        vision = int(txt.replace(",", "")) if txt.replace(",", "").isdigit() else None

        if strict is not None:                        # template read, clear margin
            chosen, verified = strict, True
        elif relaxed is not None and relaxed == vision:  # both readers agree
            chosen, verified = relaxed, True
        elif relaxed is not None:                     # template read, but shaky & unconfirmed
            chosen, verified = relaxed, False
        else:                                         # glyphs unreadable -> keep Vision
            chosen, verified = vision, False

        b["_digit_ok"] = verified
        if verified:
            stats["verified"] += 1
        else:
            stats["unverified"].append({"x": b["x"], "y": b["y"], "text": txt,
                                        "read": chosen})
        if chosen is not None and str(chosen) != txt.replace(",", ""):
            b["_vision"] = b["text"]
            b["text"] = str(chosen)
            stats["changed"] += 1
    ocr["_digits"] = stats
    return ocr


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _infer_png(json_path, data):
    if data.get("png") and os.path.exists(data["png"]):
        return data["png"]
    stem = os.path.splitext(json_path)[0]
    for cand in (stem + ".png", stem + ".jpg"):
        if os.path.exists(cand):
            return cand
    raise SystemExit("could not find capture PNG; pass it explicitly")


def _load_capture(argv):
    json_path = argv[0]
    data = json.load(open(json_path))
    png = argv[1] if len(argv) > 1 else _infer_png(json_path, data)
    return data, load_gray(png), png


def cmd_bootstrap(argv):
    data, gray, png = _load_capture(argv)
    r = DigitReader()
    n = r.learn_from_capture(gray, data["boxes"])
    missing = [str(d) for d in range(10) if str(d) not in r.templates]
    r.save()
    print(f"learned from {n} confident number cells in {os.path.basename(png)}")
    print(f"templates now cover: {sorted(r.templates)}")
    if missing:
        print(f"STILL MISSING digits {missing} — bootstrap another capture")
    else:
        print(f"all 10 digits covered -> {r.path}")


def cmd_read(argv):
    data, gray, png = _load_capture(argv)
    r = DigitReader()
    if not r.ready:
        raise SystemExit("templates incomplete — run `digits.py bootstrap` first")
    agree = disagree = unread = 0
    for b in sorted(data["boxes"], key=lambda b: (b["y"], b["x"])):
        txt = b["text"].strip()
        if not re.fullmatch(r"[\d,]{1,13}", txt):
            continue
        val = r.read_number(gray, b)
        vis = int(txt.replace(",", "")) if txt.replace(",", "").isdigit() else None
        if val is None:
            unread += 1
            flag = "UNREAD"
        elif val == vis:
            agree += 1
            flag = "ok"
        else:
            disagree += 1
            flag = f"DIFFER (vision={vis})"
        if flag != "ok":
            print(f"  [{flag:22}] digit-read={val!s:>9}  vision='{txt}'")
    print(f"\n{agree} agree, {disagree} differ, {unread} unreadable "
          f"(of {agree + disagree + unread} number cells)")


def cmd_show(argv):
    r = DigitReader()
    print(f"templates: {r.path}")
    print(f"covered: {sorted(r.templates)}  ready={r.ready}")
    for c in sorted(r.templates):
        print(f"  '{c}': {len(r.templates[c])} exemplars")


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd, argv = sys.argv[1], sys.argv[2:]
    {"bootstrap": cmd_bootstrap, "read": cmd_read, "show": cmd_show}.get(
        cmd, lambda a: (_ for _ in ()).throw(SystemExit(f"unknown cmd {cmd}")))(argv)


if __name__ == "__main__":
    main()
