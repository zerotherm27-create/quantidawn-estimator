"""Reading a marked-up upload: the app's own logic first (embedded text, annotations, colour measuring), AI only as a cross-check."""
import io, json, os, tempfile
os.environ["QD_DATA"] = tempfile.mkdtemp(prefix="qd-read-", dir="/Volumes/Jojo SSD")
import fitz
import server
from core import reader
from core.project import ROOT
from core.takeoff_builder import PROFILES, build_takeoff

c = server.app.test_client()
fails = 0
def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg); fails += (not cond)

def upload(pdf_bytes, name="plan.pdf"):
    return c.post("/api/projects", data={"files": (io.BytesIO(pdf_bytes), name)}, content_type="multipart/form-data").json["id"]

def read_all(pid):
    n = len(c.get(f"/api/project/{pid}").json["pages"])
    return [c.post(f"/api/project/{pid}/read", json={"page": i}).json for i in range(1, n + 1)]

sample = ROOT / "samples" / "boxshall-brighton"
entries = json.load(open(sample / "entries_manual.json"))[0]["entries"]
EXP = {"SQ M": "SQ M", "M": "M", "EA": "EA", "M3": "M3"}

# 1. a PDF with a live text legend: rows are read exactly and go through the same rules as the samples
d = fitz.open(); pg = d.new_page(width=1191, height=842)
for i, e in enumerate(entries):
    pg.insert_text((40, 40 + 14 * i), f"{e['label']}  {e['quantity']} {e['unit']}", fontsize=8)
pid = upload(d.tobytes())
res = read_all(pid)[0]
check(res["source"] == "text" and res["found"] >= len(entries) - 2, f"text legend read exactly ({res['found']} of {len(entries)} rows)")
q = c.get(f"/api/project/{pid}").json
ref, _ = build_takeoff([{"label": e["label"], "quantity": e["quantity"], "unit": e["unit"]} for e in entries], PROFILES["boxshall"])
check(len(q["lines"]) > 0 and abs(len(q["lines"]) - len(ref)) <= 3, f"lines built by the rules ({len(q['lines'])} vs {len(ref)} direct)")
check(q["ai"] == "not run" and all(l["ag"] == "unrun" for l in q["lines"]), "no AI involved: lines wait for a person")

# 2. AI as a cross-check only: one changed quantity is disputed, the rest agree, logic numbers untouched
orig_q = [l["q"] for l in q["lines"]]
def fake_ai(pdf, idx):
    es = [dict(e) for e in entries]
    es[3]["quantity"] = es[3]["quantity"] * 1.2
    return {"sheet": "S1", "entries": reader.clean_entries(es)}
reader.read_page_entries = fake_ai
check(c.post(f"/api/project/{pid}/ai", json={"page": 1}).json["found"] > 0, "AI reading stored")
q2 = c.get(f"/api/project/{pid}").json
check([l["q"] for l in q2["lines"][:len(orig_q)]] == orig_q, "AI never overwrites the logic quantities")
ags = [l["ag"] for l in q2["lines"]]
check("disputed" in ags and ags.count("agreed") > len(ags) // 2, f"AI disagreement flagged, most lines agreed ({ags.count('agreed')} agreed, {ags.count('disputed')} disputed)")
os.environ.pop("ANTHROPIC_API_KEY", None)
import importlib; importlib.reload(reader)
r = c.post(f"/api/project/{pid}/ai", json={"page": 1})
check(r.status_code == 502 and "ANTHROPIC_API_KEY" in r.json["error"], "AI without a key gives a clear message")

# 3. live annotations: measurement markups are summed per subject, free-text rows become legend rows
d = fitz.open(); pg = d.new_page(width=842, height=595)
for pts, txt in (([(50, 50), (150, 50), (150, 120), (50, 120)], "12.3 m2"), ([(200, 50), (300, 50), (300, 120), (200, 120)], "7.7 m2")):
    a = pg.add_polygon_annot(pts); a.set_info(content=txt, subject="Slab A"); a.set_colors(stroke=(1, 0, 0)); a.update()
pid3 = upload(d.tobytes())
r3 = read_all(pid3)[0]
check(r3["source"] == "annotations" and r3["found"] == 1, "annotation measurements read")

# 4. a flattened page: coloured markups are measured from the scale, and a person names them
with open(sample / "marked_up.pdf", "rb") as f:
    pid4 = upload(f.read(), "marked_up.pdf")
r0 = c.post(f"/api/project/{pid4}/read", json={"page": 1}).json
check(r0.get("measured") == 0 and "scale" in r0["note"], "flattened sheet without a scale asks for the scale first")
r4 = c.post(f"/api/project/{pid4}/read", json={"page": 1, "ratio": 100}).json
check(c.post(f"/api/project/{pid4}/read", json={"page": 1, "ratio": "nan"}).json["measured"] == 0, "nonsense scale ignored")
c.post(f"/api/project/{pid4}/read", json={"page": 1, "ratio": 100})
check(r4["source"] == "colour" and r4["measured"] > 3 and r4["found"] == 0, f"flattened page measured by colour ({r4['measured']} groups)")
p4 = c.get(f"/api/project/{pid4}").json
ml = [l for l in p4["lines"] if l.get("lk")]
check(ml and all(l["g"] == "MARKUPS TO IDENTIFY" and l["q"] > 0 for l in ml), "colour groups become lines to identify")
lk = ml[0]["lk"]
check(c.put(f"/api/project/{pid4}/label", json={"key": lk, "label": "Strip footing SF1 <b>"}).status_code == 200, "naming a markup accepted")
named = [l for l in c.get(f"/api/project/{pid4}").json["lines"] if l.get("lk") == lk][0]
check("Strip footing SF1" in named["ds"] and "<" not in named["ds"], "name shown on the line, markup stripped")
check(c.put(f"/api/project/{pid4}/label", json={"key": "../x", "label": "x"}).status_code == 400, "bad label key refused")
ex = c.post(f"/api/project/{pid4}/export", json={"lines": p4["lines"]})
check(ex.status_code == 200 and ex.data[:2] == b"PK", "export works with colour-measured lines")
check(c.post(f"/api/project/{pid4}/read", json={"page": 99}).status_code == 404 and c.post("/api/project/boxshall/read", json={"page": 1}).status_code == 400, "bad page and samples refused")
c.delete(f"/api/project/{pid4}/read")
check(c.get(f"/api/project/{pid4}").json["lines"] == [], "clearing returns to no lines")
check(reader.clean_entries([{"label": "x", "quantity": float("nan"), "unit": "M"}, {"label": "", "quantity": 1}, "junk", {"label": "ok", "quantity": 3, "unit": "M"}]) == [{"label": "ok", "quantity": 3.0, "unit": "M"}], "bad AI entries are dropped")
print("ALL PASSED" if not fails else f"{fails} FAILED")
import shutil; shutil.rmtree(os.environ["QD_DATA"], ignore_errors=True)
raise SystemExit(1 if fails else 0)
