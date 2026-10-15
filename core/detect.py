"""Auto-detect on a vector plan: what the drawing itself says, offered as suggestions for a person to accept.

- rooms: the plan prints each room's name and size ("BED 2  3330 x 3150"). The label gives a point inside the room; the closed walls give its outline.
- outline: the building's outer boundary, from the walls.
- doors: a door swing is a quarter-circle arc whose radius is the door leaf, so the arc gives the door's width.

Nothing here decides what a wall is made of or how high it is: the file does not say. Coordinates are points in the page as shown."""
import math
import re

import fitz
import numpy as np

from core import geometry as G

SIZE_RE = re.compile(r"^(\d{3,5})\s*[xX×]\s*(\d{3,5})$")
DOOR_R = (17.0, 30.0)          # a door leaf is 700 to 1000 mm: 20 to 29 pt at 1:100, 14 to 20 at 1:50, so the radius is read in mm instead
DOOR_MM = (600, 1100)
ZOOM = 1.5


def _area(pts):
    return 0.5 * abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))))


def room_labels(page):
    """[(name, stated_w_mm, stated_h_mm, (x, y) label centre)] from lines like '3700 x 3300' with a name line just above."""
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            t = "".join(sp["text"] for sp in ln["spans"]).strip()
            if t:
                lines.append((t, fitz.Rect(ln["bbox"])))
    out = []
    for t, r in lines:
        m = SIZE_RE.match(t)
        if not m:
            continue
        cx = (r.x0 + r.x1) / 2
        cand = [(r.y0 - rr.y1, tt.strip()) for tt, rr in lines
                if -2 <= r.y0 - rr.y1 <= 28 and abs((rr.x0 + rr.x1) / 2 - cx) <= 45 and re.search(r"[A-Za-z]{2}", tt) and not SIZE_RE.match(tt)]
        caps = [c for c in cand if c[1] == c[1].upper()]                        # room names are capitals: "MASTER BED"; annotations are not: "vanity"
        name = min(caps)[1] if caps else ""
        stated = int(m.group(1)) * int(m.group(2)) / 1e6
        if not name and stated < 5:
            continue                                                            # a cupboard or fitting size, not a room
        out.append((name, int(m.group(1)), int(m.group(2)), (cx, (r.y0 + r.y1) / 2)))
    return out


def _seeds(label_pt, name_given):
    x, y = label_pt
    return [(x, y - 9), (x, y - 20 if name_given else y - 14), (x, y), (x - 20, y - 9), (x + 20, y - 9), (x, y + 10)]


def detect_rooms(pdf, index, ratio):
    """Rooms the plan labels, each with an outline when the walls close around it. ratio is the sheet's scale (100 for 1:100)."""
    doc = fitz.open(pdf)
    try:
        page = doc[index]
        labels = room_labels(page)
        rot = page.rotation_matrix
        labels = [(n, w, h, tuple(fitz.Point(p) * rot)) for n, w, h, p in labels]
    finally:
        doc.close()
    if not labels:
        return [], np.zeros((1, 1), bool), 0, 0
    walls, w, h, n = G._wall_mask(pdf, index, ZOOM)
    r = max(1, int(round(5.0 * ZOOM)))
    free = ~G._grow(walls, r)
    k = 0.3528 * ratio / 1000                       # metres per point
    rooms, taken = [], []
    for name, sw, sh, pt in labels:
        stated = sw * sh / 1e6
        poly = None
        for sx, sy in _seeds(pt, bool(name)):
            ix, iy = int(round(sx * ZOOM)), int(round(sy * ZOOM))
            if not (0 <= iy < free.shape[0] and 0 <= ix < free.shape[1]):
                continue
            region = G._fill(free, iy, ix)
            if region is None:
                continue
            if region.sum() > 0.4 * region.size or region[0].any() or region[-1].any() or region[:, 0].any() or region[:, -1].any():
                break                                # leaked out through an opening: not a closed room
            for _ in range(r):
                region = (region | np.roll(region, 1, 0) | np.roll(region, -1, 0) | np.roll(region, 1, 1) | np.roll(region, -1, 1)) & ~walls
            pts = G._simplify(G._outline(region), 3.0 * ZOOM)
            poly = [[round(float(px) / ZOOM, 1), round(float(py) / ZOOM, 1)] for px, py in pts]
            break
        if not poly or len(poly) < 3:
            rooms.append({"name": name, "w": sw, "h": sh, "stated": round(stated, 2), "pts": None, "area": None, "status": "open", "label": [round(pt[0], 1), round(pt[1], 1)]})
            continue
        area = _area(poly) * k * k
        ratio_ok = area / stated if stated else 0
        key = (round(sum(p[0] for p in poly) / len(poly)), round(sum(p[1] for p in poly) / len(poly)))
        if any(abs(key[0] - t[0]) < 12 and abs(key[1] - t[1]) < 12 for t in taken):
            continue                                 # two labels in one outline: keep the first
        taken.append(key)
        status = "match" if 0.85 <= ratio_ok <= 1.15 else "check" if 0.6 <= ratio_ok <= 1.5 else "open"
        rooms.append({"name": name, "w": sw, "h": sh, "stated": round(stated, 2), "pts": poly if status != "open" else None,
                      "area": round(area, 2) if status != "open" else None, "status": status, "label": [round(pt[0], 1), round(pt[1], 1)]})
    return rooms, walls, w, h


def _shrink(m, steps):
    for _ in range(steps):
        m = m & np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1)
    return m


def _heavy_mask(pdf, index):
    """Only the heavy, long strokes (external walls): the dashed roof lines, dimension ticks and symbols are lighter or shorter."""
    w, h, segs = G.page_segments(pdf, index)
    blank = fitz.open()
    try:
        pg = blank.new_page(width=w, height=h)
        sh = pg.new_shape()
        for x0, y0, x1, y1, wt in segs:
            if wt >= 0.7 and abs(x1 - x0) + abs(y1 - y0) >= 12:
                sh.draw_line((x0, y0), (x1, y1))
        sh.finish(color=(0, 0, 0), width=1.0)
        sh.commit()
        pix = pg.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csGRAY, alpha=False)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w) < 160
    finally:
        blank.close()


def detect_outline(pdf, index, seed_pts, ratio, gap=15.0):
    """The building's outer outline: what the outside cannot reach through the heavy walls (a closing bridges door openings up to
    about 2 x gap points without growing the shape), taken as the component that holds the rooms."""
    walls = _heavy_mask(pdf, index)
    r = max(1, int(round(gap * ZOOM)))
    closed = _shrink(G._grow(walls, r), r)
    free = ~closed
    outside = G._fill(free, 2, 2)
    if outside is None:
        return None
    inner = ~outside
    comp = None
    for sx, sy in seed_pts:
        ix, iy = int(round(sx * ZOOM)), int(round(sy * ZOOM))
        if 0 <= iy < inner.shape[0] and 0 <= ix < inner.shape[1] and inner[iy, ix]:
            comp = G._fill(inner, iy, ix)
            break
    if comp is None:
        return None
    pts = G._simplify(G._outline(comp), 3.0 * ZOOM)
    poly = [[round(float(px) / ZOOM, 1), round(float(py) / ZOOM, 1)] for px, py in pts]
    if len(poly) < 3:
        return None
    k = 0.3528 * ratio / 1000
    per = sum(math.hypot(poly[(i + 1) % len(poly)][0] - poly[i][0], poly[(i + 1) % len(poly)][1] - poly[i][1]) for i in range(len(poly))) * k
    return {"pts": poly, "area": round(_area(poly) * k * k, 2), "perimeter": round(per, 2)}


def detect_doors(pdf, index, ratio):
    """Door swings: quarter-circle arcs whose radius is a door leaf (600 to 1100 mm). Returns [{x, y, width_mm}] with the width rounded to 10 mm."""
    doc = fitz.open(pdf)
    try:
        page = doc[index]
        rot = page.rotation_matrix
        found = []
        for d in page.get_drawings():
            for it in d["items"]:
                if it[0] != "c":
                    continue
                p0, p3 = fitz.Point(it[1]) * rot, fitz.Point(it[4]) * rot
                r = math.hypot(p3.x - p0.x, p3.y - p0.y) / math.sqrt(2)
                mm = r * 0.3528 * ratio
                if DOOR_MM[0] <= mm <= DOOR_MM[1]:
                    found.append({"x": round((p0.x + p3.x) / 2, 1), "y": round((p0.y + p3.y) / 2, 1), "width_mm": int(round(mm / 10.0) * 10)})
    finally:
        doc.close()
    out = []
    for f in sorted(found, key=lambda f: (f["x"], f["y"])):                # one swing is drawn as one arc, but a repeated drawing would double it
        if not any(abs(f["x"] - o["x"]) < 6 and abs(f["y"] - o["y"]) < 6 for o in out):
            out.append(f)
    return out


def detect_page(pdf, index, ratio):
    rooms, walls, w, h = detect_rooms(pdf, index, ratio)
    seeds = [((r["label"][0]), r["label"][1] - 9) for r in rooms if r["status"] in ("match", "check")] or [(r["label"][0], r["label"][1]) for r in rooms]
    outline = detect_outline(pdf, index, seeds, ratio) if rooms and walls.any() else None
    doors = detect_doors(pdf, index, ratio)
    return {"rooms": rooms, "outline": outline, "doors": doors}
