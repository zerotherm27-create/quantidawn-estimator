"""A colour-measured group can be seen on the plan: its mask covers exactly the pixels it was measured from."""
import io, tempfile
from pathlib import Path
import fitz
import numpy as np
from core.store import LocalStore, set_store
import server

set_store(LocalStore(Path(tempfile.mkdtemp())))
c = server.app.test_client()
A3 = fitz.paper_rect("a3-l")


def flat_pdf():
    d = fitz.open()
    p = d.new_page(width=A3.width, height=A3.height)
    p.insert_text((60, 700), "GROUND FLOOR PLAN   Scale 1:100", fontsize=12)
    p.draw_rect(fitz.Rect(100, 100, 300, 220), color=None, fill=(0.9, 0.1, 0.1))                 # red filled area
    p.draw_line((400, 150), (800, 150), color=(0.1, 0.2, 0.9), width=4)                           # blue stroke
    img = p.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png")
    d = fitz.open(); q = d.new_page(width=A3.width, height=A3.height); q.insert_image(q.rect, stream=img)
    return d.tobytes()


r = c.post("/api/projects", data={"files": (io.BytesIO(flat_pdf()), "plan.pdf")}, content_type="multipart/form-data")
assert r.status_code == 200, r.data
pid = r.json["id"]

r = c.post(f"/api/project/{pid}/read", json={"page": 1, "ratio": 100}).json
assert r["measured"] >= 2, r
groups = c.get(f"/api/project/{pid}").json["lines"]
marks = [l for l in groups if l.get("lk")]
assert len(marks) >= 2, marks
print("PASS read gave", len(marks), "colour lines")

masks = {}
for l in marks:
    key = l["lk"].lstrip("0123456789")
    resp = c.get(f"/api/project/{pid}/markup-mask/1/{key}.png")
    assert resp.status_code == 200 and resp.mimetype == "image/png", (key, resp.status_code)
    pm = fitz.Pixmap(resp.data)
    px = np.frombuffer(pm.samples, np.uint8).reshape(pm.h, pm.w, pm.n)
    a = (px[..., 0] == 255) & (px[..., 1] == 0) & (px[..., 2] == 200)          # the magenta group pixels; the rest is a white veil
    assert (px[..., 3] > 0).all() and not a.all()
    assert a.any(), key
    ys, xs = np.nonzero(a)
    masks[l["mk"]] = (a, xs.mean() / pm.w * A3.width, ys.mean() / pm.h * A3.height)
print("PASS every group has a mask")

# the masks are different groups: no pixel in both, and each sits on its own shape
names = list(masks)
for i in range(len(names)):
    for j in range(i + 1, len(names)):
        assert not (masks[names[i]][0] & masks[names[j]][0]).any(), (names[i], names[j])
centres = sorted((round(v[1]), round(v[2])) for v in masks.values())
assert any(abs(x - 200) < 15 and abs(y - 160) < 15 for x, y in centres), centres      # the red box
assert any(abs(x - 600) < 15 and abs(y - 150) < 15 for x, y in centres), centres      # the blue line
print("PASS masks sit on their own shapes:", centres)

assert c.get(f"/api/project/{pid}/markup-mask/1/c999999.png").status_code == 404
assert c.get(f"/api/project/{pid}/markup-mask/1/evil.png").status_code == 400
assert c.get(f"/api/project/{pid}/markup-mask/9/c1.png").status_code == 404
print("PASS bad keys and pages refused")
