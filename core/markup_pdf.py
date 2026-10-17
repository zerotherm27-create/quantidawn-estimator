"""Draw the shapes a person measured in the app onto copies of the original sheets, so the work can leave as a marked-up PDF.

The look follows the way an estimator marks up a plan in Bluebeam: a large translucent disc for each counted item, a thick coloured line for a
length, a tinted fill for an area, the measured value in red beside it, and a legend column listing every item on the sheet with its colour and quantity."""
import re
import zlib

import fitz

PALETTE = [(0.85, 0.33, 0.12), (0.10, 0.45, 0.80), (0.15, 0.60, 0.30), (0.60, 0.25, 0.70), (0.80, 0.55, 0.05), (0.05, 0.55, 0.60)]
RED = (0.82, 0.0, 0.0)
MAX_SHAPES, MAX_POINTS, MAX_LEGEND = 5000, 2000, 400


def colour_of(label, hex_colour=None):
    """A takeoff item's own colour when it has one (so the PDF matches the screen), else a stable pick from the label."""
    if hex_colour and re.match(r"^#[0-9a-fA-F]{6}$", hex_colour):
        return tuple(int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return PALETTE[zlib.crc32(label.encode()) % len(PALETTE)]


def clean_shapes(raw):
    """Keep only well-formed shapes; the browser sends these, so nothing is trusted."""
    out = []
    for s in (raw if isinstance(raw, list) else [])[:MAX_SHAPES]:
        try:
            kind, page = s["kind"], int(s["page"])
            pts = [(float(x), float(y)) for x, y in s["pts"][:MAX_POINTS]]
        except (KeyError, TypeError, ValueError):
            continue
        if kind not in ("count", "len", "area") or page < 0 or len(pts) < (1 if kind == "count" else 2):
            continue
        out.append({"kind": kind, "page": page, "pts": pts, "label": str(s.get("label", ""))[:40], "text": str(s.get("text", ""))[:40],
                    "color": str(s.get("color", ""))[:7]})
    return out


def clean_legend(raw):
    """The items to list in the legend column: {label, name, color, kind, qty}. `label` ties an entry to the shapes that carry it."""
    out = []
    for e in (raw if isinstance(raw, list) else [])[:MAX_LEGEND]:
        if not isinstance(e, dict) or e.get("kind") not in ("count", "len", "area"):
            continue
        out.append({"label": str(e.get("label", ""))[:40], "name": str(e.get("name", ""))[:80], "color": str(e.get("color", ""))[:7],
                    "kind": e["kind"], "qty": str(e.get("qty", ""))[:24]})
    return out


def build(page_sources, shapes, note="", legend=None):
    """page_sources: [(pdf path, 0-based page index)] for every sheet in order. Returns the marked-up PDF bytes."""
    out = fitz.open()
    try:
        for path, idx in page_sources:
            src = fitz.open(str(path))
            try:
                out.insert_pdf(src, from_page=idx, to_page=idx)
            finally:
                src.close()
        for s in shapes:
            if s["page"] < len(out):
                _draw(out[s["page"]], s)
        for pg in sorted({s["page"] for s in shapes if s["page"] < len(out)}):
            used = {s["label"] for s in shapes if s["page"] == pg}
            rows = [e for e in (legend or []) if e["label"] in used]      # only the items drawn on this sheet
            if rows:
                _legend(out[pg], rows)
        if note and len(out):
            _stamp(out[0], note)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def _text(shape, page, at, text, size, colour, font="helv"):
    """Write text at a point given in what the reader sees; the page may be rotated."""
    shape.insert_text(at * page.derotation_matrix, text, fontname=font, fontsize=size, color=colour, rotate=page.rotation)


def _box(shape, page, x0, y0, x1, y1):
    m = page.derotation_matrix
    c = [fitz.Point(x0, y0), fitz.Point(x1, y0), fitz.Point(x1, y1), fitz.Point(x0, y1)]
    shape.draw_polyline([p * m for p in c] + [c[0] * m])


def _draw(page, s):
    m = page.derotation_matrix                       # shapes are stored in the unrotated page's coordinates
    pts_v = [fitz.Point(x, y) for x, y in s["pts"]]
    pts = [p * m for p in pts_v]
    col = colour_of(s["label"], s.get("color"))
    sh = page.new_shape()
    if s["kind"] == "count":
        r = max(8.0, page.rect.width / 105)           # a big translucent disc, as drawn in Bluebeam
        sh.draw_circle(pts[0], r)
        sh.finish(color=col, fill=col, fill_opacity=0.42, stroke_opacity=0.9, width=1.0)
    else:
        sh.draw_polyline(pts + ([pts[0]] if s["kind"] == "area" else []))
        sh.finish(color=col, fill=col if s["kind"] == "area" else None, fill_opacity=0.30, stroke_opacity=0.9,
                  width=3.2 if s["kind"] == "len" else 2.2, closePath=False, lineJoin=1, lineCap=1)
    sh.commit()
    if s["text"]:                                    # the measured value in red, e.g. "2.330 M"
        at = pts_v[len(pts_v) // 2] + fitz.Point(7, -7)
        w = fitz.get_text_length(s["text"], fontname="hebo", fontsize=9) + 6
        bg = page.new_shape()
        _box(bg, page, at.x - 2, at.y - 10, at.x + w, at.y + 3)
        bg.finish(color=None, fill=(1, 1, 1), fill_opacity=0.85)
        _text(bg, page, fitz.Point(at.x + 1, at.y), s["text"], 9, RED, "hebo")
        bg.commit()


def _quietest_corner(page, W, H, bw, bh):
    """Top-left of a bw x bh box, in what the reader sees, at the spot on the sheet with the least text and line work under it.
    A grid of spots is tried; the top right wins ties, because that is where an estimator puts the legend."""
    m = page.derotation_matrix
    words = [fitz.Rect(w[:4]) for w in page.get_text("words")]
    lines = [d["rect"] for d in page.get_drawings() if d.get("rect") is not None and d["rect"].width < W * 0.6 and d["rect"].height < H * 0.6]
    if len(lines) > 8000:
        lines = lines[::len(lines) // 8000 + 1]              # a sample is enough to tell busy from quiet
    xs = [8 + (W - bw - 16) * i / 7 for i in range(7, -1, -1)]       # right to left
    ys = [8 + (H - bh - 42) * j / 7 for j in range(8)]               # top to bottom, keeping clear of the footer
    best = None
    for yi, y in enumerate(ys):
        for xi, x in enumerate(xs):
            box = fitz.Rect(x, y, x + bw, y + bh) * m          # the box in the page's own coordinates, where the words and lines are reported
            score = sum(1 for r in words if r.intersects(box)) * 4 + sum(1 for r in lines if r.intersects(box)) + 0.01 * (xi + yi)
            if best is None or score < best[0]:
                best = (score, x, y)
    return best[1], best[2]


def _legend(page, rows):
    """A column (or a few) in the top right of the sheet: a swatch in the item's colour, its name, and its quantity."""
    size, rh, pad, sw = 6.8, 9.6, 5.0, 9.0
    W, H = page.rect.width, page.rect.height          # page.rect is already what the reader sees, rotation included
    per_col = max(8, int((H * 0.92 - 2 * pad) // rh))
    cols = [rows[i:i + per_col] for i in range(0, len(rows), per_col)]
    widths = []
    for c in cols:
        w = max(fitz.get_text_length(e["name"][:46], fontname="helv", fontsize=size) +
                fitz.get_text_length(e["qty"], fontname="hebo", fontsize=size) + 14 for e in c)
        widths.append(sw + 5 + w)
    total = sum(widths) + pad * (len(cols) + 1)
    bw, bh = total, 2 * pad + rh * min(per_col, len(rows))
    x0, y0 = _quietest_corner(page, W, H, bw, bh)
    y1 = y0 + bh
    bg = page.new_shape()
    _box(bg, page, x0, y0, W - 8, y1)
    bg.finish(color=(0.55, 0.55, 0.55), fill=(1, 1, 1), fill_opacity=0.94, width=0.6)
    bg.commit()
    x = x0 + pad
    for c, cw in zip(cols, widths):
        for i, e in enumerate(c):
            col = colour_of(e["label"], e["color"])
            cy = y0 + pad + rh * i + rh / 2
            sh = page.new_shape()
            m = page.derotation_matrix
            if e["kind"] == "count":
                sh.draw_circle(fitz.Point(x + sw / 2, cy) * m, sw / 2 - 0.5)
                sh.finish(color=col, fill=col, fill_opacity=0.5, width=0.8)
            elif e["kind"] == "len":
                sh.draw_line(fitz.Point(x, cy) * m, fitz.Point(x + sw, cy) * m)
                sh.finish(color=col, width=3, lineCap=1)
            else:
                _box(sh, page, x, cy - 3.5, x + sw, cy + 3.5)
                sh.finish(color=col, fill=col, fill_opacity=0.35, width=0.8)
            _text(sh, page, fitz.Point(x + sw + 5, cy + 2.3), e["name"][:46], size, (0.1, 0.1, 0.1))
            q = fitz.get_text_length(e["qty"], fontname="hebo", fontsize=size)
            _text(sh, page, fitz.Point(x + cw - q, cy + 2.3), e["qty"], size, (0.1, 0.1, 0.1), "hebo")
            sh.commit()
        x += cw + pad


def _stamp(page, note):
    H = page.rect.height                                                           # the reader's height
    sh = page.new_shape()
    _text(sh, page, fitz.Point(14, H - 10), note, 7, (0.35, 0.35, 0.35))
    sh.commit()
