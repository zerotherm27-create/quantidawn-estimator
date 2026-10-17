"""The marked-up PDF looks like an estimator's markup (legend column, red values, big discs), and takeoff items pick their own colour."""
import io, tempfile
from pathlib import Path
import fitz
from core import markup_pdf
from core.store import LocalStore, set_store
import server

set_store(LocalStore(Path(tempfile.mkdtemp())))
c = server.app.test_client()
A3 = fitz.paper_rect("a3-l")


def plain_pdf(rotate=0):
    d = fitz.open()
    p = d.new_page(width=A3.width, height=A3.height)
    p.insert_text((60, 800), "FLOOR PLAN  Scale 1:100", fontsize=12)
    p.set_rotation(rotate)
    return d.tobytes()


# 1. the legend input is validated: unknown kinds and non-dicts are dropped, long text is cut
lg = markup_pdf.clean_legend([{"label": "A", "name": "x" * 200, "color": "#112233", "kind": "len", "qty": "1.000 m"}, {"kind": "nope"}, "junk", None])
assert len(lg) == 1 and len(lg[0]["name"]) == 80, lg
assert markup_pdf.clean_legend("not a list") == []

# 2. build(): the legend lists only the items drawn on that sheet, in their colours, with their quantities; values are red and bold
shapes = markup_pdf.clean_shapes([
    {"kind": "count", "page": 0, "pts": [[200, 200]], "label": "DOOR", "color": "#7b2cbf"},
    {"kind": "len", "page": 0, "pts": [[200, 300], [500, 300]], "label": "WALL", "color": "#e4572e", "text": "20.000 M"},
    {"kind": "area", "page": 0, "pts": [[300, 400], [500, 400], [500, 500]], "label": "SLAB", "color": "#17bebb", "text": "10.000 M2"}])
legend = markup_pdf.clean_legend([
    {"label": "DOOR", "name": "Door 870 mm", "color": "#7b2cbf", "kind": "count", "qty": "16 each"},
    {"label": "WALL", "name": "External wall", "color": "#e4572e", "kind": "len", "qty": "20.000 m"},
    {"label": "SLAB", "name": "Slab", "color": "#17bebb", "kind": "area", "qty": "10.000 m2"},
    {"label": "NOT ON THIS SHEET", "name": "Roof battens", "color": "#000000", "kind": "len", "qty": "5.000 m"}])
for rot in (0, 90):
    src = Path(tempfile.mkdtemp()) / "p.pdf"
    src.write_bytes(plain_pdf(rot))
    out = fitz.open(stream=markup_pdf.build([(src, 0)], shapes, "note", legend), filetype="pdf")
    text = out[0].get_text()
    for must in ("Door 870 mm", "External wall", "Slab", "16 each", "20.000 m", "20.000 M", "10.000 M2"):
        assert must in text, (rot, must)
    assert "Roof battens" not in text, rot
    spans = [s for b in out[0].get_text("dict")["blocks"] if b["type"] == 0 for ln in b["lines"] for s in ln["spans"]]
    red = [s for s in spans if s["text"] == "20.000 M"]
    col = red[0]["color"] if red else 0
    assert red and (col >> 16) > 190 and (col & 0xFFFF) == 0 and "Bold" in red[0]["font"], red     # the measured value is red and bold
print("legend and values:", "ok on pages rotated 0 and 90")

# 3. the endpoint takes the legend and still refuses an empty request
r = c.post("/api/projects", data={"files": (io.BytesIO(plain_pdf()), "plan.pdf")}, content_type="multipart/form-data")
assert r.status_code == 200, r.data
pid = r.json["id"]
r = c.post(f"/api/project/{pid}/markup-pdf", json={"shapes": [], "legend": legend})
assert r.status_code == 400
r = c.post(f"/api/project/{pid}/markup-pdf", json={"shapes": [{"kind": "count", "page": 0, "pts": [[100, 100]], "label": "DOOR", "color": "#7b2cbf"}], "legend": legend})
assert r.status_code == 200, r.data
got = c.get(r.json["url"])
assert got.status_code == 200 and "Door 870 mm" in fitz.open(stream=got.data, filetype="pdf")[0].get_text()
print("endpoint: ok")

# 4. an item made without a colour gets the first palette colour nobody uses; the colours are all different
cols = []
for name in ("A", "B", "C", "D"):
    r = c.put(f"/api/project/{pid}/items", json={"add": {"name": name, "kind": "len"}})
    assert r.status_code == 200, r.data
cols = [l["hex"] for l in c.get(f"/api/project/{pid}").json["lines"] if l.get("it")]
assert len(cols) == 4 and len(set(cols)) == 4 and cols[0] == server.ITEM_PALETTE[0], cols
r = c.put(f"/api/project/{pid}/items", json={"add": {"name": "E", "kind": "len", "color": "#123456"}})        # a colour given is kept
assert [l["hex"] for l in c.get(f"/api/project/{pid}").json["lines"] if l.get("it")][-1] == "#123456"
assert c.put(f"/api/project/{pid}/items", json={"add": {"name": "F", "kind": "len", "color": "not-a-colour"}}).status_code == 400
print("item colours: ok", cols)
