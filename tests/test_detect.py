"""Auto-detect on a vector plan: rooms from their printed labels, the building outline, door swings."""
import io, tempfile
from pathlib import Path
import fitz
from core.store import LocalStore, set_store
import server

set_store(LocalStore(Path(tempfile.mkdtemp())))
c = server.app.test_client()
A3 = fitz.paper_rect("a3-l")
K = 0.3528 * 100 / 1000             # metres per point at 1:100


def plan_pdf():
    d = fitz.open()
    p = d.new_page(width=A3.width, height=A3.height)

    def wall(a, b):
        p.draw_line(a, b, color=(0, 0, 0), width=1.0)
    for a, b in (((250, 150), (700, 150)), ((700, 150), (700, 450)), ((700, 450), (250, 450)), ((250, 450), (250, 150))):
        wall(a, b)                                                        # the building: 450 x 300 pt
    for a, b in (((300, 200), (500, 200)), ((500, 200), (500, 350)), ((300, 350), (300, 200)), ((300, 350), (390, 350)), ((398, 350), (500, 350))):
        wall(a, b)                                                        # a room 200 x 150 pt with an 8 pt door gap
    p.insert_text((365, 262), "BED 1", fontsize=9)
    p.insert_text((355, 275), "7056 x 5292", fontsize=8)                  # 200 x 150 pt at 1:100
    p.insert_text((330, 215), "vanity", fontsize=6)
    p.insert_text((330, 225), "900 x 900", fontsize=6)                    # a fitting, not a room
    sh = p.new_shape()
    sh.draw_sector(fitz.Point(600, 400), fitz.Point(600 + 24.7, 400), -90, fullSector=False)    # a door swing, leaf 24.7 pt = 871 mm
    sh.finish(color=(0, 0, 0), width=0.5)
    sh.commit()
    for i in range(300):                                                  # enough line work to count as a vector drawing
        p.draw_line((720 + i * 0.5, 100), (720 + i * 0.5, 110), color=(0, 0, 0), width=0.5)
    return d.tobytes()


r = c.post("/api/projects", data={"files": (io.BytesIO(plan_pdf()), "plan.pdf")}, content_type="multipart/form-data")
pid = r.json["id"]
res = c.post(f"/api/project/{pid}/detect", json={"page": 1, "ratio": 100})
assert res.status_code == 200, res.data
j = res.json
named = [r for r in j["rooms"] if r["name"] == "BED 1"]
assert len(named) == 1 and named[0]["status"] == "match", j["rooms"]
bed = named[0]
assert abs(bed["area"] - 7.056 * 5.292) / (7.056 * 5.292) < 0.12, bed["area"]
assert not [r for r in j["rooms"] if r["name"].lower() == "vanity"], "fittings are not rooms"
print(f"PASS room found from its label: {bed['name']} {bed['area']} m2 vs printed {bed['stated']} m2")
o = j["outline"]
assert o and abs(o["area"] - 450 * 300 * K * K) / (450 * 300 * K * K) < 0.06, o
assert abs(o["perimeter"] - 2 * (450 + 300) * K) / (2 * (450 + 300) * K) < 0.06, o
print(f"PASS building outline: {o['area']} m2 (drawn {450 * 300 * K * K:.1f}), perimeter {o['perimeter']} m")
assert len(j["doors"]) == 1 and j["doors"][0]["width_mm"] == 870, j["doors"]
print("PASS door swing found, width read from its radius: 870 mm")

assert c.post(f"/api/project/{pid}/detect", json={"page": 1, "ratio": "x"}).status_code == 400
assert c.post(f"/api/project/{pid}/detect", json={"page": 1, "ratio": 0}).status_code == 400
assert c.post(f"/api/project/{pid}/detect", json={"page": 9, "ratio": 100}).status_code == 404
d2 = fitz.open(); q = d2.new_page(width=A3.width, height=A3.height); q.insert_text((60, 60), "just text", fontsize=12)
r2 = c.post("/api/projects", data={"files": (io.BytesIO(d2.tobytes()), "blank.pdf")}, content_type="multipart/form-data")
r3 = c.post(f"/api/project/{r2.json['id']}/detect", json={"page": 1, "ratio": 100})
assert r3.status_code == 422 and "not a vector drawing" in r3.json["error"], r3.data
print("PASS bad input refused; a picture sheet says there is nothing to detect")

# the real Clinton plan, scored against what its own Area Table and the estimator's BOQ say
real = Path(__file__).resolve().parent.parent.parent / "samples" / "22l524sp-clinton" / "clean_unmarked.pdf"
if real.exists():
    rr = c.post("/api/projects", data={"files": (io.BytesIO(real.read_bytes()), "clinton.pdf")}, content_type="multipart/form-data")
    jj = c.post(f"/api/project/{rr.json['id']}/detect", json={"page": 5, "ratio": 100}).json
    matches = [r for r in jj["rooms"] if r["status"] == "match"]
    assert len(matches) >= 6 and all(0.85 <= r["area"] / r["stated"] <= 1.15 for r in matches), [(r["name"], r["area"], r["stated"]) for r in matches]
    assert abs(jj["outline"]["area"] - 384.31) / 384.31 < 0.03, jj["outline"]["area"]
    widths = sorted({d["width_mm"] for d in jj["doors"]})
    assert widths == [820, 870] and 13 <= len(jj["doors"]) <= 18, (widths, len(jj["doors"]))
    print(f"PASS Clinton: {len(matches)} rooms within 15% of their printed size, outline {jj['outline']['area']} m2 vs Area Table 384.31, "
          f"{len(jj['doors'])} door swings (widths {widths}; the BOQ lists about 13 hinged doors)")
else:
    print("SKIP the Clinton sample is not present")
