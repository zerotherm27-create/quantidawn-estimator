"""Colour reading leaves out a logo by itself, a person can draw a clean-up box, and colour lines can be combined and split."""
import io, tempfile
from pathlib import Path
import fitz
import numpy as np
from core import logic_reader
from core.store import LocalStore, set_store
import server

set_store(LocalStore(Path(tempfile.mkdtemp())))
c = server.app.test_client()
A3 = fitz.paper_rect("a3-l")


def flat_pdf():
    d = fitz.open()
    p = d.new_page(width=A3.width, height=A3.height)
    p.draw_rect(fitz.Rect(100, 100, 700, 440), color=None, fill=(0.9, 0.1, 0.1))                  # red slab: the drawing
    p.draw_line((150, 480), (650, 480), color=(0.1, 0.2, 0.9), width=1.5)                          # blue stroke below it
    p.draw_line((150, 520), (600, 520), color=(0.95, 0.55, 0.05), width=1.5)                       # orange stroke below that
    p.draw_circle((400, 270), 14, color=None, fill=(0.1, 0.7, 0.2))                                # small green marker inside the slab
    p.draw_rect(fitz.Rect(1090, 770, 1130, 810), color=None, fill=(0.6, 0.1, 0.8))                 # purple logo in the title-block corner
    img = p.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    d = fitz.open(); q = d.new_page(width=A3.width, height=A3.height); q.insert_image(q.rect, stream=img)
    return d.tobytes()


def hues(pid):
    return sorted(l["ds"] for l in c.get(f"/api/project/{pid}").json["lines"] if l.get("lk"))


raw = flat_pdf()
r = c.post("/api/projects", data={"files": (io.BytesIO(raw), "plan.pdf")}, content_type="multipart/form-data")
pid = r.json["id"]

# 1. the logo is left out by itself, the marker inside the drawing stays
c.post(f"/api/project/{pid}/read", json={"page": 1, "ratio": 100})
names = hues(pid)
assert len(names) == 4, names                              # red, blue, orange, green: no purple
assert not any("Purple" in n or "Violet" in n or "Magenta" in n for n in names), names
assert any("Green" in n for n in names), names
print("PASS logo left out, marker inside the drawing kept:", names)

# 2. a clean-up box removes the blue line (x 150-650pt, y 480pt of 1191 x 842)
r = c.put(f"/api/project/{pid}/ignore", json={"page": 1, "box": [0.10, 0.54, 0.60, 0.60]})
assert r.status_code == 200 and len(r.json["boxes"]) == 1, r.data
c.post(f"/api/project/{pid}/read", json={"page": 1, "ratio": 100})
names2 = hues(pid)
assert len(names2) == 3 and not any("Blue" in n for n in names2), names2
assert c.get(f"/api/project/{pid}").json["ignore"]["1"], "the box is sent to the page"
print("PASS clean-up box removes what is inside it:", names2)

assert c.put(f"/api/project/{pid}/ignore", json={"page": 1, "box": [0.5, 0.5, 0.5, 0.5]}).status_code == 400
assert c.put(f"/api/project/{pid}/ignore", json={"page": 1, "box": [0, 0, 2, 1]}).status_code == 400
assert c.put(f"/api/project/{pid}/ignore", json={"page": 1, "box": "x"}).status_code == 400
r = c.put(f"/api/project/{pid}/ignore", json={"page": 1, "clear": True})
c.post(f"/api/project/{pid}/read", json={"page": 1, "ratio": 100})
assert len(hues(pid)) == 4 and r.json["boxes"] == []
print("PASS clearing the boxes brings it back; bad boxes refused")

# 3. combine two length lines: quantities add, one line, the highlight covers both
lines = c.get(f"/api/project/{pid}").json["lines"]
length = [l for l in lines if l.get("lk") and l["u"] == "m"]
area = [l for l in lines if l.get("lk") and l["u"] == "m2"]
assert len(length) == 2, [l["ds"] for l in lines]
want = round(sum(l["q"] for l in length), 2)
r = c.put(f"/api/project/{pid}/merge", json={"keys": [l["lk"] for l in length]})
assert r.status_code == 200, r.data
lines = c.get(f"/api/project/{pid}").json["lines"]
mk = [l for l in lines if l.get("lk")]
assert len(mk) == 3, [l["ds"] for l in mk]                 # combined + red area + green marker
comb = next(l for l in mk if l.get("mem"))
assert abs(comb["q"] - want) < 0.02 and len(comb["mem"]) == 2, (comb["q"], want)
keys = "-".join(k.lstrip("0123456789") for k in comb["mem"])
resp = c.get(f"/api/project/{pid}/markup-mask/1/{keys}.png")
assert resp.status_code == 200
pm = fitz.Pixmap(resp.data)
px = np.frombuffer(pm.samples, np.uint8).reshape(pm.h, pm.w, pm.n)
m = (px[..., 0] == 255) & (px[..., 1] == 0) & (px[..., 2] == 200)
ys, xs = np.nonzero(m)
assert ys.min() / pm.h * A3.height < 500 and ys.max() / pm.h * A3.height > 505, "both lines are painted"
print("PASS combined line adds quantities:", comb["q"], "and highlights both lines")

# the browser revalidates the mask instead of showing a stale one
assert resp.headers.get("ETag") and c.get(f"/api/project/{pid}/markup-mask/1/{keys}.png", headers={"If-None-Match": resp.headers["ETag"]}).status_code == 304
print("PASS mask is revalidated by ETag")

# 4. split puts them back; area + length cannot be combined
c.put(f"/api/project/{pid}/merge", json={"split": comb["lk"]})
lines = c.get(f"/api/project/{pid}").json["lines"]
assert len([l for l in lines if l.get("lk")]) == 4 and not any(l.get("mem") for l in lines)
r = c.put(f"/api/project/{pid}/merge", json={"keys": [length[0]["lk"], area[0]["lk"]]})
assert r.status_code == 400 and "same unit" in r.json["error"], r.data
assert c.put(f"/api/project/{pid}/merge", json={"keys": ["1c1"]}).status_code == 400
assert c.put(f"/api/project/{pid}/merge", json={"keys": ["1c1", "2c1"]}).status_code == 400
print("PASS split restores the lines; mixed units and bad keys refused")

# 5. re-reading drops the combination (the colour groups can change)
c.put(f"/api/project/{pid}/merge", json={"keys": [l["lk"] for l in length]})
c.delete(f"/api/project/{pid}/read")
c.post(f"/api/project/{pid}/read", json={"page": 1, "ratio": 100})
assert not any(l.get("mem") for l in c.get(f"/api/project/{pid}").json["lines"])
print("PASS a fresh read clears combinations")

# 6. reading a sheet again with no scale does not wipe the lines already read
c.post(f"/api/project/{pid}/read", json={"page": 1, "ratio": 100})
before = len(hues(pid))
r = c.post(f"/api/project/{pid}/read", json={"page": 1}).json
assert "set its scale" in r["note"] and len(hues(pid)) == before > 0, (r, hues(pid))
print("PASS no scale: existing colour lines are kept")
