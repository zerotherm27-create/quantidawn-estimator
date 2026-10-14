"""The app's own reading of a marked-up PDF: no AI, no key, no cost. A person verifies every result.

1. Embedded markups: when the PDF still has live annotations or a text layer (a Bluebeam export with markups left
   on), legend rows ("label  47.3 SQ M") and measurement annotations are read exactly.
2. Colour measuring: when the page is only pixels (a flattened export), saturated colour groups are found and measured
   from the confirmed scale. They come out unlabelled ("Red markup"); a person names them.
"""
import math
import re

import fitz
import numpy as np

PT_M = 0.352778e-3                                   # one PDF point on paper, in metres
UNIT_NORM = {"SQ M": "SQ M", "SQM": "SQ M", "SQ.M": "SQ M", "M2": "SQ M", "M²": "SQ M", "M3": "M3", "M³": "M3",
             "LM": "M", "M": "M", "EA": "EA", "EACH": "EA"}
ROW_RE = re.compile(r"^(?P<label>.{3,200}?)[\s:=\-–]+(?P<q>\d{1,6}(?:\.\d+)?)\s*(?P<u>SQ\.? ?M|SQM|M2|M²|M3|M³|LM|EA|EACH|M)\.?\s*$", re.I)
QTY_RE = re.compile(r"(?P<q>\d{1,6}(?:\.\d+)?)\s*(?P<u>SQ\.? ?M|SQM|M2|M²|M3|M³|LM|EA|EACH|M)\b\.?", re.I)
HUES = [(15, "Red"), (45, "Orange"), (70, "Yellow"), (165, "Green"), (200, "Cyan"), (260, "Blue"), (330, "Purple"), (361, "Red")]


def _unit(u):
    return UNIT_NORM.get(re.sub(r"\s+", " ", u.upper().replace(".", "")).strip() if "SQ" in u.upper() else u.upper())


def _row(text):
    m = ROW_RE.match(re.sub(r"\s+", " ", text).strip())
    if not m:
        return None
    unit = _unit(m["u"])
    return {"label": m["label"].strip()[:300], "quantity": float(m["q"]), "unit": unit} if unit else None


def read_embedded(pdf, index):
    """{'source': 'text'|'annotations'|'', 'entries': [{label, quantity, unit}]} from live text and annotations."""
    doc = fitz.open(pdf)
    try:
        page = doc[index]
        entries, source = [], ""
        groups = {}
        for a in page.annots() or []:
            info = a.info or {}
            text = (info.get("content") or "").strip()
            subj = (info.get("subject") or "").strip()
            row = _row(text.replace("\n", " ")) if text else None
            if row:                                           # a free-text legend row
                entries.append(row)
                continue
            m = QTY_RE.search(text)                           # a measurement markup: "12.3 m2" with a subject
            unit = _unit(m["u"]) if m else None
            if m and unit:
                col = tuple(round(c, 1) for c in ((a.colors or {}).get("stroke") or ()))
                g = groups.setdefault((subj or "Markup", col, unit), 0.0)
                groups[(subj or "Markup", col, unit)] = g + float(m["q"])
        if groups:
            source = "annotations"
            entries += [{"label": s, "quantity": round(q, 3), "unit": u} for (s, _c, u), q in groups.items()]
        if not entries:
            text_rows = [r for r in (_row(l) for l in page.get_text("text").splitlines()) if r]
            if not text_rows:                                 # a legend whose label and quantity sit in separate lines of one block
                for b in page.get_text("blocks"):
                    r = _row(b[4].replace("\n", " "))
                    if r:
                        text_rows.append(r)
            if text_rows:
                entries, source = text_rows, "text"
        elif not source:
            source = "annotations"
        return {"source": source, "entries": entries[:200]}
    finally:
        doc.close()


def has_markup(pdf, index, min_pct=0.3, long_edge=1000):
    """Free, local test: does the sheet carry any coloured markup? Cover sheets, notes and plain drawings do not, and need no
    AI reading. Measured on the samples, sheets with a legend have at least 0.5% saturated pixels, unmarked ones 0.1-0.3%."""
    doc = fitz.open(pdf)
    try:
        page = doc[index]
        z = long_edge / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3)
    finally:
        doc.close()
    f = img.astype(np.float32) / 255
    mx = f.max(2)
    d = mx - f.min(2)
    return float(((d / np.maximum(mx, 1e-6) > 0.35) & (mx > 0.3)).mean()) * 100 >= min_pct


def _erode(m):
    e = np.zeros_like(m)
    e[1:-1, 1:-1] = m[1:-1, 1:-1] & m[:-2, 1:-1] & m[2:, 1:-1] & m[1:-1, :-2] & m[1:-1, 2:]
    return e


def measure_colours(pdf, index, ratio, long_edge=2000):
    """Measure saturated colour groups on a raster page. Returns (groups, note).
    A group: {key, name, hex, kind: area|length, quantity (m2 or m), unit}."""
    if not ratio:
        return [], "The scale of this sheet is not known yet, so colours cannot be measured."
    doc = fitz.open(pdf)
    try:
        page = doc[index]
        z = long_edge / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, 3)
    finally:
        doc.close()
    f = img.astype(np.float32) / 255
    mx, mn = f.max(2), f.min(2)
    d = mx - mn
    mask = (d / np.maximum(mx, 1e-6) > 0.35) & (mx > 0.3)         # saturated: markup ink, not grey linework or paper
    if mask.sum() < 400:
        return [], "No coloured markups were found on this sheet."
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    dd = np.maximum(d, 1e-6)
    hue = np.where(mx == r, ((g - b) / dd) % 6, np.where(mx == g, (b - r) / dd + 2, (r - g) / dd + 4)) * 60
    sat = d / np.maximum(mx, 1e-6)
    # finer than hue alone: legend items of similar hue still differ in shade, so split by hue, saturation and brightness
    key = (hue // 6).astype(np.int32) * 100 + (sat * 4).astype(np.int32) * 10 + (mx * 4).astype(np.int32)
    out = []
    for k in np.unique(key[mask]):
        m = mask & (key == k)
        n = int(m.sum())
        if n < max(250, 0.0003 * m.size):
            continue
        er = int(_erode(m).sum())
        rgb = img[m].mean(0)
        hx = "#%02x%02x%02x" % tuple(int(v) for v in rgb)
        h0 = (float(hue[m].mean()) % 360)
        name = next(nm for lim, nm in HUES if h0 < lim)
        if er / n > 0.45:                                       # filled shapes: area
            q, kind, unit = n / (z * z) * PT_M ** 2 * ratio ** 2, "area", "m2"
        else:                                                   # strokes: length from the edge pixels
            q, kind, unit = (n - er) / 2 / z * PT_M * ratio, "length", "m"
        if math.isfinite(q) and q > 0:
            out.append({"key": f"c{int(k)}", "name": name, "hex": hx, "kind": kind, "quantity": round(q, 3), "unit": unit})
    out.sort(key=lambda x: -x["quantity"])
    out = out[:40]                                              # the biggest groups; the rest is mostly noise
    return out, ("" if out else "No coloured markups were found on this sheet.")
