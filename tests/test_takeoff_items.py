"""Takeoff items with colours, the sheet's own line work for snapping, and click-inside-a-room."""
import io, tempfile
from pathlib import Path
import fitz
import numpy as np
from core.store import LocalStore, set_store
import server

set_store(LocalStore(Path(tempfile.mkdtemp())))
c = server.app.test_client()
A3 = fitz.paper_rect("a3-l")


def vector_pdf():
    d = fitz.open()
    p = d.new_page(width=A3.width, height=A3.height)
    p.insert_text((60, 800), "FLOOR PLAN  Scale 1:100", fontsize=12)

    def wall(a, b, w=1.0):
        p.draw_line(a, b, color=(0, 0, 0), width=w)
    for a, b in (((250, 150), (700, 150)), ((700, 150), (700, 450)), ((700, 450), (250, 450)), ((250, 450), (250, 150))):
        wall(a, b)                                                   # the building: a closed outer wall
    for a, b in (((300, 200), (500, 200)), ((500, 200), (500, 350)), ((300, 350), (300, 200))):
        wall(a, b)                                                   # a room: three walls...
    wall((300, 350), (390, 350)); wall((398, 350), (500, 350))      # ...and a bottom wall with an 8 pt door gap
    wall((350, 250), (450, 250), 0.1)                                # hairline detail: not a wall, not snapped to
    return d.tobytes()


r = c.post("/api/projects", data={"files": (io.BytesIO(vector_pdf()), "plan.pdf")}, content_type="multipart/form-data")
assert r.status_code == 200, r.data
pid = r.json["id"]


def items():
    return [l for l in c.get(f"/api/project/{pid}").json["lines"] if l.get("it")]


# 1. items: made by name, kind and colour; each is a line in the table
for name, kind, col in (("External wall", "len", "#E4572E"), ("Slab", "area", "#17BEBB"), ("Power points", "count", "#7B2CBF")):
    r = c.put(f"/api/project/{pid}/items", json={"add": {"name": name, "kind": kind, "color": col}})
    assert r.status_code == 200, r.data
ls = items()
assert [l["ds"] for l in ls] == ["External wall", "Slab", "Power points"], ls
assert [l["u"] for l in ls] == ["m", "m2", "each"] and [l["mt"] for l in ls] == ["len", "area", "count"]
assert [l["hex"] for l in ls] == ["#e4572e", "#17bebb", "#7b2cbf"] and all(l["q"] == 0 and l["g"] == "MY TAKEOFF" for l in ls)
assert len({l["el"] for l in ls}) == 3
print("PASS items become coloured table lines:", [(l["ds"], l["u"], l["hex"]) for l in ls])

for bad in ({"add": {"name": "", "kind": "len", "color": "#ffffff"}}, {"add": {"name": "x", "kind": "volume", "color": "#ffffff"}},
            {"add": {"name": "x", "kind": "len", "color": "red"}}, {"add": "x"}, {"recolor": {"id": 1, "color": "nope"}}, {}):
    assert c.put(f"/api/project/{pid}/items", json=bad).status_code == 400, bad
print("PASS bad items refused")

wid = ls[0]["it"]
c.put(f"/api/project/{pid}/items", json={"recolor": {"id": wid, "color": "#112233"}})
assert next(l for l in items() if l["it"] == wid)["hex"] == "#112233"
c.put(f"/api/project/{pid}/items", json={"delete": ls[2]["it"]})
assert [l["ds"] for l in items()] == ["External wall", "Slab"]
if "westgarth" in server.SAMPLES:
    assert c.put("/api/project/westgarth/items", json={"add": {"name": "x", "kind": "len", "color": "#ffffff"}}).status_code == 400
print("PASS recolour and delete; sample projects have no items")

# 2. the sheet's line work, for snapping
g = c.get(f"/api/project/{pid}/geometry/1").json
segs = g["segs"]
assert abs(g["w"] - 1191) < 2 and len(segs) == 9, (g["w"], len(segs))     # the nine wall strokes; the hairline is left out
assert not any(s[4] < 0.3 for s in segs), "hairlines are left out"
top = next(s for s in segs if abs(s[1] - 150) < 0.5 and abs(s[3] - 150) < 0.5 and abs(abs(s[2] - s[0]) - 450) < 1)
print("PASS geometry gives the walls:", len(segs), "segments; building top wall", top)
assert c.get(f"/api/project/{pid}/geometry/9").status_code == 404

# 3. click inside a room: its outline, even with a door gap
k = 0.3528 * 100 / 1000


def area(pts):
    a = np.array(pts)
    return 0.5 * abs(np.dot(a[:, 0], np.roll(a[:, 1], 1)) - np.dot(a[:, 1], np.roll(a[:, 0], 1)))


r = c.post(f"/api/project/{pid}/room", json={"page": 1, "x": 400, "y": 300})
assert r.status_code == 200, r.data
room = area(r.json["pts"])
assert 0.9 * 200 * 150 < room < 1.1 * 200 * 150, room                # 200 x 150 pt room, door gap bridged
print(f"PASS click inside a room with a door gap: {room:.0f} pt2 vs {200 * 150} (about {room * k * k:.1f} m2 at 1:100)")

r = c.post(f"/api/project/{pid}/room", json={"page": 1, "x": 600, "y": 400})      # the building's open interior, outside the room
assert r.status_code == 200 and area(r.json["pts"]) > 0.7 * (450 * 300 - 200 * 150), r.data
r = c.post(f"/api/project/{pid}/room", json={"page": 1, "x": 900, "y": 400})      # outside the building: not closed
assert r.status_code == 422 and "closed room" in r.json["error"], r.data
r = c.post(f"/api/project/{pid}/room", json={"page": 1, "x": 250, "y": 300})      # on a wall
assert r.status_code == 422, r.data
for bad in ({"page": 1, "x": "a", "y": 1}, {"page": 1, "x": 1}, {"page": "z", "x": 1, "y": 1}):
    assert c.post(f"/api/project/{pid}/room", json=bad).status_code == 400, bad
print("PASS open interior ok; outside the building and on a wall refused; bad input refused")

# 4. Read markups on a clean vector drawing says so, instead of inventing "markup" from the architect's colours
d = fitz.open()
p = d.new_page(width=A3.width, height=A3.height)
for i in range(400):
    p.draw_line((100 + i, 100), (100 + i, 300), color=(0, 0, 0), width=0.5)
p.draw_line((300, 500), (700, 500), color=(0.9, 0.1, 0.1), width=1.0)                 # an architect's red annotation line
r = c.post("/api/projects", data={"files": (io.BytesIO(d.tobytes()), "clean.pdf")}, content_type="multipart/form-data")
cid = r.json["id"]
r = c.post(f"/api/project/{cid}/read", json={"page": 1, "ratio": 100}).json
assert r["measured"] == 0 and r["found"] == 0 and "no estimator markup" in r["note"] and "New item" in r["note"], r
assert not [l for l in c.get(f"/api/project/{cid}").json["lines"] if l.get("lk")]
print("PASS clean vector sheet: no invented markup, and it says what to do")

# 5. the marked-up PDF uses an item's own colour, like the screen
from core import markup_pdf
assert markup_pdf.colour_of("X", "#e4572e") == (0xe4 / 255, 0x57 / 255, 0x2e / 255)
assert markup_pdf.colour_of("X", "red") == markup_pdf.colour_of("X") == markup_pdf.colour_of("X", None)
sh = markup_pdf.clean_shapes([{"kind": "len", "page": 0, "pts": [[0, 0], [5, 5]], "label": "W", "color": "#112233"}])
assert sh[0]["color"] == "#112233"
print("PASS marked-up PDF takes the item's colour")

# 6. wall and volume items: height or depth is required and sets the formula
for body in ({"name": "Wall", "kind": "wall", "color": "#112233"}, {"name": "Wall", "kind": "wall", "color": "#112233", "size": 0},
             {"name": "Wall", "kind": "wall", "color": "#112233", "size": "x"}, {"name": "Slab", "kind": "vol", "color": "#112233", "size": 1000}):
    assert c.put(f"/api/project/{pid}/items", json={"add": body}).status_code == 400, body
c.put(f"/api/project/{pid}/items", json={"add": {"name": "Brick wall", "kind": "wall", "color": "#aa5500", "size": 2.7}})
c.put(f"/api/project/{pid}/items", json={"add": {"name": "Slab", "kind": "vol", "color": "#0055aa", "size": 0.1}})
by = {l["ds"]: l for l in items()}
w, v = by["Brick wall"], by["Slab"]
assert (w["u"], w["mt"], w["calc"], w["n"]["H"], w["n"]["O"]) == ("m2", "len", "wall", 2.7, 0), w
assert (v["u"], v["mt"], v["calc"], v["n"]["H"]) == ("m3", "area", "prism", 0.1), v
print("PASS wall (length x height - openings, m2) and volume (area x depth, m3) items")

# the numbers behind them: the same formulas the estimator's own lines use
from core.calc import qty
assert abs(qty("wall", {"L": 30, "H": 2.7, "O": 5.34, "R": None}) - (30 * 2.7 - 5.34)) < 1e-9
assert abs(qty("prism", {"A": 80, "H": 0.1}) - 8.0) < 1e-9
print("PASS 30 m x 2.7 m - 5.34 m2 openings = 75.66 m2; 80 m2 x 0.1 m = 8 m3")
