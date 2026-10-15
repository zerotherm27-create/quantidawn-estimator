"""The drawing's own line work. A vector PDF holds every wall as a stroked line, so a click can snap to it exactly, and a click
inside a closed set of walls can take the room's outline without guessing from pixels.

Coordinates are in points on the page as it is shown (rotation applied), the same space the app draws shapes in."""
import numpy as np
import fitz

MIN_W = 0.3         # thinner strokes are hatching and fine detail: not worth snapping to
WALL_W = 0.4        # strokes at least this heavy are treated as walls when finding rooms
WALL_LEN = 6.0      # ...and at least this long in points: drawings draw dashed roof and hidden lines as many tiny solid strokes
MAX_SEGS = 60000


def page_segments(pdf, index):
    """(width, height, segments) with each segment [x0, y0, x1, y1, stroke width], heaviest first, rounded to 0.1 pt."""
    doc = fitz.open(pdf)
    try:
        page = doc[index]
        rot = page.rotation_matrix
        segs = []

        def add(a, b, w):
            a, b = fitz.Point(a) * rot, fitz.Point(b) * rot
            if abs(a.x - b.x) + abs(a.y - b.y) >= 0.2:
                segs.append([round(a.x, 1), round(a.y, 1), round(b.x, 1), round(b.y, 1), round(w, 2)])

        for d in page.get_drawings():
            w = d.get("width")
            if d.get("type") not in ("s", "fs") or not w or w < MIN_W:
                continue
            for it in d["items"]:
                if it[0] == "l":
                    add(it[1], it[2], w)
                elif it[0] in ("re", "qu"):
                    q = fitz.Quad(it[1]) if it[0] == "qu" else fitz.Rect(it[1]).quad
                    pts = [q.ul, q.ur, q.lr, q.ll]
                    for i in range(4):
                        add(pts[i], pts[(i + 1) % 4], w)
                elif it[0] == "c":                              # an arc (a door swing, a curved wall): its two ends
                    add(it[1], it[4], w)
        segs.sort(key=lambda s: -s[4])
        r = page.rect * rot if rot else page.rect
        return r.width, r.height, segs[:MAX_SEGS]
    finally:
        doc.close()


def _wall_mask(pdf, index, zoom):
    """Heavy strokes drawn onto a blank page: True where there is wall."""
    w, h, segs = page_segments(pdf, index)
    blank = fitz.open()
    try:
        pg = blank.new_page(width=w, height=h)
        sh = pg.new_shape()
        n = 0
        for x0, y0, x1, y1, wt in segs:
            if wt >= WALL_W and abs(x1 - x0) + abs(y1 - y0) >= WALL_LEN:
                sh.draw_line((x0, y0), (x1, y1))
                n += 1
        sh.finish(color=(0, 0, 0), width=1.0)
        sh.commit()
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w)
        return img < 160, w, h, n
    finally:
        blank.close()


def _grow(m, steps):
    for _ in range(steps):
        m = m | np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)
    return m


def _fill(free, sy, sx):
    """Flood fill by runs: the connected True region of `free` around (sy, sx), or None if the seed is not free."""
    h, w = free.shape
    if not free[sy, sx]:
        return None
    out = np.zeros_like(free)
    stack = [(sy, sx)]
    while stack:
        y, x = stack.pop()
        if out[y, x] or not free[y, x]:
            continue
        xl = x
        while xl > 0 and free[y, xl - 1] and not out[y, xl - 1]:
            xl -= 1
        xr = x
        while xr < w - 1 and free[y, xr + 1] and not out[y, xr + 1]:
            xr += 1
        out[y, xl:xr + 1] = True
        for yy in (y - 1, y + 1):
            if 0 <= yy < h:
                idx = np.nonzero(free[yy, xl:xr + 1] & ~out[yy, xl:xr + 1])[0]
                if idx.size:
                    for s in idx[np.r_[True, np.diff(idx) > 1]]:
                        stack.append((yy, xl + int(s)))
    return out


_N8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def _outline(m):
    """Outer boundary of the region as pixel (x, y) points, by Moore neighbour tracing."""
    ys, xs = np.nonzero(m)
    start = (int(ys.min()), int(xs[ys == ys.min()].min()))
    h, w = m.shape
    pad = np.zeros((h + 2, w + 2), bool)
    pad[1:-1, 1:-1] = m
    cur, prev_dir, pts = (start[0] + 1, start[1] + 1), 6, []        # the pixel left of the start is background: we came from there
    first = cur
    for _ in range(4 * (h + w) * 4):
        pts.append((cur[1] - 1, cur[0] - 1))
        for k in range(8):
            d = (prev_dir + 1 + k) % 8                              # clockwise from just after where we came from
            ny, nx = cur[0] + _N8[d][0], cur[1] + _N8[d][1]
            if pad[ny, nx]:
                prev_dir = (d + 4 + 1) % 8
                cur = (ny, nx)
                break
        else:
            break                                                    # a single pixel
        if cur == first and len(pts) > 2:
            break
    return pts


def _simplify(pts, tol):
    """Douglas-Peucker on a closed outline."""
    a = np.array(pts, float)
    if len(a) < 4:
        return a

    def dp(p):
        if len(p) < 3:
            return p
        d = p[-1] - p[0]
        n = np.hypot(*d)
        dist = np.abs(np.cross(d, p[1:-1] - p[0])) / n if n else np.hypot(*(p[1:-1] - p[0]).T)
        i = int(np.argmax(dist))
        if dist[i] > tol:
            return np.vstack([dp(p[:i + 2])[:-1], dp(p[i + 1:])])
        return np.vstack([p[0], p[-1]])

    far = int(np.argmax(np.hypot(*(a - a[0]).T)))
    return np.vstack([dp(a[:far + 1])[:-1], dp(np.vstack([a[far:], a[:1]]))[:-1]])


def room_polygon(pdf, index, x, y, zoom=1.5, gap=5.0, max_share=0.4):
    """The outline of the closed room around page point (x, y), as [[x, y], ...] in points, or None if the click is on a wall or
    the area is not closed in (so the fill escaped). Door openings up to about `gap` points on each side are bridged."""
    walls, w, h, n = _wall_mask(pdf, index, zoom)
    if not n:
        return None
    sx, sy = int(round(x * zoom)), int(round(y * zoom))
    if not (0 <= sy < walls.shape[0] and 0 <= sx < walls.shape[1]):
        return None
    r = max(1, int(round(gap * zoom)))
    free = ~_grow(walls, r)
    region = _fill(free, sy, sx)
    if region is None:
        return None
    if region.sum() > max_share * region.size or region[0].any() or region[-1].any() or region[:, 0].any() or region[:, -1].any():
        return None                                                  # leaked out through an opening: not a closed room
    for _ in range(r):                                               # take back the bridging margin, but never into a wall
        region = (region | np.roll(region, 1, 0) | np.roll(region, -1, 0) | np.roll(region, 1, 1) | np.roll(region, -1, 1)) & ~walls
    pts = _simplify(_outline(region), 3.0 * zoom)                  # about 3 pt: drops wall-thickness jogs and door jambs
    return [[round(float(px) / zoom, 1), round(float(py) / zoom, 1)] for px, py in pts]
