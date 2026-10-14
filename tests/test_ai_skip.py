"""The AI check spends tokens, so it skips sheets with nothing to read and re-reads only where the legend was found. No API calls."""
import io, os, tempfile
os.environ["QD_DATA"] = tempfile.mkdtemp(prefix="qd-skip-", dir="/Volumes/Jojo SSD")
import fitz
import server
from core import logic_reader, reader

c = server.app.test_client()
fails = 0
def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg); fails += (not cond)

# has_markup: plain and near-plain pages are not markup, a coloured shape is
d = fitz.open(); path = os.path.join(os.environ["QD_DATA"], "m.pdf")
d.new_page(width=1191, height=842)                                            # 1: blank
pg = d.new_page(width=1191, height=842); pg.insert_text((50, 50), "NOTES", fontsize=12)    # 2: black text
pg = d.new_page(width=1191, height=842); pg.draw_rect(fitz.Rect(100, 100, 400, 300), color=(1, 0, 0), fill=(1, 0, 0))   # 3: a red shape (~6%)
pg = d.new_page(width=1191, height=842); pg.draw_rect(fitz.Rect(100, 100, 130, 130), color=(1, 0, 0), fill=(1, 0, 0))   # 4: a speck (~0.1%)
d.save(path)
check([logic_reader.has_markup(path, i) for i in range(4)] == [False, False, True, False], "blank, text-only and speck pages are not markup; a coloured shape is")

def upload(pdf_bytes):
    return c.post("/api/projects", data={"files": (io.BytesIO(pdf_bytes), "p.pdf")}, content_type="multipart/form-data").json["id"]
pid = upload(open(path, "rb").read())

calls = []
def fake(pdf, idx, box=None):
    calls.append((idx, box))
    return {"sheet": "S", "entries": [{"label": "Slab U1", "quantity": 10.0, "unit": "SQ M"}], "verify": [], "legend_box": box or [0.6, 0.0, 1.0, 0.3]}
reader.read_page_entries = fake

r = c.post(f"/api/project/{pid}/ai", json={"page": 1}).json
check(r.get("skipped") is True and r["found"] == 0 and calls == [], "a blank sheet is skipped without calling the AI")
r = c.post(f"/api/project/{pid}/ai", json={"page": 2}).json
check(r.get("skipped") is True and calls == [], "a text-only sheet with no legend rows is skipped")
r = c.post(f"/api/project/{pid}/ai", json={"page": 3}).json
check(r["skipped"] is False and r["found"] == 1 and calls == [(2, None)], "a sheet with coloured markup is read, the whole sheet the first time")
r = c.post(f"/api/project/{pid}/ai", json={"page": 3}).json
check(calls[-1] == (2, [0.6, 0.0, 1.0, 0.3]) and r["area_only"] is True, "the second read looks only where the legend was found")
r = c.post(f"/api/project/{pid}/ai", json={"page": 3, "fresh": True}).json
check(calls[-1] == (2, None) and r["area_only"] is False, "fresh starts over with the whole sheet")
r = c.post(f"/api/project/{pid}/ai", json={"page": 3, "box": [0.1, 0.1, 0.5, 0.4]}).json
check(calls[-1] == (2, [0.1, 0.1, 0.5, 0.4]), "a box given by the caller is used")
n = len(calls)
r = c.post(f"/api/project/{pid}/ai", json={"page": 1, "force": True}).json
check(r["skipped"] is False and len(calls) == n + 1, "force reads a sheet that would be skipped")

# a remembered box only reaches readers that accept it, and a failing area does not lose the read
def plain(pdf, idx):
    calls.append(("plain", idx))
    return {"sheet": "S", "entries": [{"label": "A", "quantity": 1.0, "unit": "M"}], "verify": []}
reader.read_page_entries = plain
pid2 = upload(open(path, "rb").read())
c.post(f"/api/project/{pid2}/ai", json={"page": 3})
r = c.post(f"/api/project/{pid2}/ai", json={"page": 3}).json
check(r["found"] == 1 and r["area_only"] is False, "a reader that returns no box is called plainly every time")

print("ALL PASSED" if not fails else f"{fails} FAILED")
import shutil; shutil.rmtree(os.environ["QD_DATA"], ignore_errors=True)
raise SystemExit(1 if fails else 0)
