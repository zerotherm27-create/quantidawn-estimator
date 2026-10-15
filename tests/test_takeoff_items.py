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
