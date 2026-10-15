"""Draw the shapes a person measured in the app onto copies of the original sheets, so the work can leave as a marked-up PDF."""
import re
import zlib

import fitz

PALETTE = [(0.85, 0.33, 0.12), (0.10, 0.45, 0.80), (0.15, 0.60, 0.30), (0.60, 0.25, 0.70), (0.80, 0.55, 0.05), (0.05, 0.55, 0.60)]
MAX_SHAPES, MAX_POINTS = 5000, 2000


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


def build(page_sources, shapes, note=""):
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
        if note and len(out):
            _stamp(out[0], note)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def _draw(page, s):
    m = page.derotation_matrix                       # shapes are stored in the unrotated page's coordinates
    pts_v = [fitz.Point(x, y) for x, y in s["pts"]]
    pts = [p * m for p in pts_v]
    col = colour_of(s["label"], s.get("color"))
    sh = page.new_shape()
    if s["kind"] == "count":
        sh.draw_circle(pts[0], 5)
        sh.finish(color=(1, 1, 1), fill=col, fill_opacity=0.85, width=1.2)
    else:
        sh.draw_polyline(pts + ([pts[0]] if s["kind"] == "area" else []))
        sh.finish(color=col, fill=col if s["kind"] == "area" else None, fill_opacity=0.16, width=1.8, closePath=False,
                  lineJoin=1, lineCap=1)
    sh.commit()
    tag = " ".join(x for x in (s["label"], s["text"]) if x)
    if tag:
        at = pts_v[len(pts_v) // 2] + fitz.Point(6, -6)           # label placed in what the reader sees, then mapped back
        w = fitz.get_text_length(tag, fontname="helv", fontsize=8) + 6
        corners = [fitz.Point(at.x - 2, at.y - 9), fitz.Point(at.x + w, at.y - 9), fitz.Point(at.x + w, at.y + 3), fitz.Point(at.x - 2, at.y + 3)]
        bg = page.new_shape()
        bg.draw_polyline([c * m for c in corners] + [corners[0] * m])
        bg.finish(color=None, fill=(1, 1, 1), fill_opacity=0.85)
        bg.insert_text(fitz.Point(at.x + 1, at.y) * m, tag, fontname="helv", fontsize=8, color=col, rotate=page.rotation)
        bg.commit()


def _stamp(page, note):
    r = page.rect
    sh = page.new_shape()
    sh.insert_text(fitz.Point(r.x0 + 14, r.y1 - 10), note, fontname="helv", fontsize=7, color=(0.35, 0.35, 0.35))
    sh.commit()
