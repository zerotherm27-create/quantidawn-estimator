"""The takeoff item library: validation, loading a template into a project in one request, the team's own templates, and the bundled seed."""
import io, json, subprocess, sys, tempfile
from pathlib import Path
import fitz
from core import item_library as lib
from core.store import LocalStore, set_store
import server

set_store(LocalStore(Path(tempfile.mkdtemp())))
c = server.app.test_client()
ROOT = Path(__file__).resolve().parent.parent


def fails(fn, *a):
    try:
        fn(*a)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


# 1. one item: names are cleaned, kinds and colours checked, walls and volumes need a size
assert lib.clean_item({"name": "  Slab <b>edge</b>  form ", "kind": "len"}) == {"name": "Slab bedge/b form", "kind": "len"}
assert lib.clean_item({"name": "Wall", "kind": "wall", "size": "2.55", "color": "#ABCDEF", "tag": "U1"}) == {"name": "Wall", "kind": "wall", "size": 2.55, "color": "#abcdef", "tag": "U1"}
assert "name" in fails(lib.clean_item, {"kind": "len"})
assert "length, area" in fails(lib.clean_item, {"name": "x", "kind": "nope"})
assert "colour" in fails(lib.clean_item, {"name": "x", "kind": "len", "color": "red"})
assert "height" in fails(lib.clean_item, {"name": "x", "kind": "wall"})
assert "between" in fails(lib.clean_item, {"name": "x", "kind": "vol", "size": 500})
assert len(lib.clean_item({"name": "n" * 500, "kind": "count"})["name"]) == lib.NAME_MAX
print("clean_item: ok")

# 2. a list: repeats dropped, empty and oversized refused
assert len(lib.clean_items([{"name": "A", "kind": "len"}, {"name": "a", "kind": "len"}, {"name": "A", "kind": "area"}])) == 2
assert fails(lib.clean_items, []) and fails(lib.clean_items, "nope")
assert "at most" in fails(lib.clean_items, [{"name": str(i), "kind": "len"} for i in range(5)], 4)
print("clean_items: ok")

# 3. merging into a project: skip what is there, keep a colour the template gives, give the rest the next free colour, respect the cap
have = [{"id": 7, "name": "Slab", "kind": "area", "color": "#e6194b"}]
items, added, skipped, cut = lib.merge(have, [{"name": "slab", "kind": "area"}, {"name": "Doors", "kind": "count"},
                                              {"name": "Own colour", "kind": "len", "color": "#123456"}, {"name": "Wall", "kind": "wall", "size": 2.4, "tag": "U2"}])
assert (added, skipped, cut) == (3, 1, 0) and [i["id"] for i in items] == [7, 8, 9, 10], (added, skipped, items)
assert items[1]["color"] == "#3cb44b" and items[2]["color"] == "#123456" and items[3]["size"] == 2.4 and items[3]["tag"] == "U2", items
_, a2, s2, c2 = lib.merge([{"id": 1, "name": "x", "kind": "len", "color": "#000000"}], [{"name": "a", "kind": "len"}, {"name": "b", "kind": "len"}], limit=2)
assert (a2, s2, c2) == (1, 0, 1), (a2, s2, c2)
print("merge: ok")

# 4. the bundled seed: every item valid, the long estimator names kept, walls carry their height
seed = lib.builtin_templates()["residential-house"]
assert len(seed["items"]) >= 150 and seed["builtin"] and seed["name"] == "Residential house", len(seed["items"])
assert any(len(i["name"]) > 60 for i in seed["items"]) and any(i["kind"] == "wall" and i["size"] for i in seed["items"])
assert {i["kind"] for i in seed["items"]} <= set(lib.ITEM_KINDS)
print("bundled seed:", len(seed["items"]), "items")

# 5. the seed can be rebuilt from a legend by the generator, and rows in an unknown unit are listed, not guessed
tmp = Path(tempfile.mkdtemp())
(tmp / "legend.json").write_text(json.dumps([{"entries": [
    {"label": "Skirtings - U1", "quantity": 1, "unit": "M"}, {"label": "Door 870", "quantity": 3, "unit": "EA"},
    {"label": "Brick 2550mm H - U2", "quantity": 9, "unit": "M"}, {"label": "Floor", "quantity": 2, "unit": "SQ M"},
    {"label": "Concrete", "quantity": 2, "unit": "CU M"}, {"label": "Door 870", "quantity": 3, "unit": "EA"}]}]))
r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_item_template.py"), "--id", "t", "--name", "T", "--out", str(tmp / "t.json"), str(tmp / "legend.json")],
                   capture_output=True, text=True, cwd=ROOT)
assert r.returncode == 0, r.stderr
made = json.loads((tmp / "t.json").read_text())["items"]
assert [(i["name"], i["kind"]) for i in made] == [("Skirtings - U1", "len"), ("Door 870", "count"), ("Brick 2550mm H - U2", "wall"), ("Floor", "area")], made
assert made[2]["size"] == 2.55 and made[0]["tag"] == "U1" and "skipped 1 rows" in r.stdout, r.stdout
print("generator: ok")


# 6. the API: a project, the library list, loading the seed in one request
def plain_pdf():
    d = fitz.open()
    d.new_page(width=842, height=595).insert_text((60, 500), "PLAN  Scale 1:100", fontsize=12)
    return d.tobytes()


r = c.post("/api/projects", data={"files": (io.BytesIO(plain_pdf()), "plan.pdf")}, content_type="multipart/form-data")
assert r.status_code == 200, r.data
pid = r.json["id"]
listing = c.get("/api/library").json
ids = {t["id"]: t for t in listing}
assert ids["residential-house"]["builtin"] and ids["residential-house"]["count"] == len(seed["items"]), listing
assert c.get("/api/library/residential-house").json["items"][0]["name"] == seed["items"][0]["name"]
assert c.get("/api/library/no-such").status_code == 404 and c.get("/api/library/BAD ID").status_code == 404

r = c.post(f"/api/project/{pid}/items/template", json={"template": "residential-house"})
assert r.status_code == 200 and r.json["added"] == len(seed["items"]) and r.json["skipped"] == 0, r.data[:200]
lines = [l for l in c.get(f"/api/project/{pid}").json["lines"] if l.get("it")]
assert len(lines) == len(seed["items"]), len(lines)
assert any(len(l["ds"]) > 60 for l in lines), "long estimator names must survive"
assert all(len(l["hex"]) == 7 for l in lines) and len({l["hex"] for l in lines}) >= 10
again = c.post(f"/api/project/{pid}/items/template", json={"template": "residential-house"}).json
assert again["added"] == 0 and again["skipped"] == len(seed["items"]), again                  # loading twice adds nothing
print("load seed:", len(lines), "items, second load adds", again["added"])

# 7. the items endpoint takes the longer names and the bigger cap
name100 = "N" * 100
r = c.put(f"/api/project/{pid}/items", json={"add": {"name": name100, "kind": "len"}})
assert r.status_code == 200 and r.json["items"][-1]["name"] == name100
r = c.put(f"/api/project/{pid}/items", json={"rename": {"id": r.json["items"][-1]["id"], "name": "R" * 110}})
assert r.status_code == 200 and r.json["items"][-1]["name"] == "R" * 110

# 8. all-or-nothing: one bad item refuses the whole request and nothing is added
before = len(c.get(f"/api/project/{pid}").json["lines"])
r = c.post(f"/api/project/{pid}/items/template", json={"items": [{"name": "Good", "kind": "len"}, {"name": "Bad", "kind": "nope"}]})
assert r.status_code == 400 and "length, area" in r.json["error"], r.data
assert len(c.get(f"/api/project/{pid}").json["lines"]) == before
assert c.post(f"/api/project/{pid}/items/template", json={}).status_code == 400
assert c.post("/api/project/boxshall/items/template", json={"template": "residential-house"}).status_code == 400       # a sample project
assert c.post("/api/project/p-nope/items/template", json={"template": "residential-house"}).status_code == 404
print("all-or-nothing and refusals: ok")

# 9. the team's own templates: create, list, load, rename, change items, delete; the bundled ones are protected
r = c.post("/api/library", json={"name": "Garage <slab>", "items": [{"name": "Slab", "kind": "area", "color": "#112233"}, {"name": "Edge", "kind": "len"}]})
assert r.status_code == 200 and r.json["count"] == 2, r.data
tid = r.json["id"]
assert tid.startswith("u-garage-slab-")
mine = {t["id"]: t for t in c.get("/api/library").json}[tid]
assert mine["name"] == "Garage slab" and not mine["builtin"] and mine["count"] == 2
assert c.put(f"/api/library/{tid}", json={"name": "Garage and shed", "items": [{"name": "Slab", "kind": "area"}]}).status_code == 200
got = c.get(f"/api/library/{tid}").json
assert got["name"] == "Garage and shed" and len(got["items"]) == 1
assert c.put("/api/library/residential-house", json={"name": "x"}).status_code == 400
assert c.delete("/api/library/residential-house").status_code == 400
assert c.post("/api/library", json={"name": "", "items": [{"name": "a", "kind": "len"}]}).status_code == 400
assert c.post("/api/library", json={"name": "Empty", "items": []}).status_code == 400
assert c.put(f"/api/library/{tid}", json={"items": [{"name": "a", "kind": "bad"}]}).status_code == 400
assert c.get(f"/api/library/{tid}").json["items"][0]["name"] == "Slab"                  # a refused change leaves it as it was
assert c.delete(f"/api/library/{tid}").status_code == 200 and c.get(f"/api/library/{tid}").status_code == 404
print("team templates: ok")

# 10. save a project's items as a template, and load it into a fresh project
r2 = c.post("/api/projects", data={"files": (io.BytesIO(plain_pdf()), "plan2.pdf")}, content_type="multipart/form-data")
pid2 = r2.json["id"]
assert c.post(f"/api/project/{pid2}/items/save-template", json={"name": "Nothing yet"}).status_code == 400
c.put(f"/api/project/{pid2}/items", json={"add": {"name": "Walls", "kind": "wall", "size": 2.7, "color": "#e4572e"}})
c.put(f"/api/project/{pid2}/items", json={"add": {"name": "Power points", "kind": "count"}})
assert c.post(f"/api/project/{pid2}/items/save-template", json={"name": ""}).status_code == 400
r = c.post(f"/api/project/{pid2}/items/save-template", json={"name": "My house"})
assert r.status_code == 200 and r.json["count"] == 2, r.data
saved = c.get(f"/api/library/{r.json['id']}").json
assert [(i["name"], i["kind"]) for i in saved["items"]] == [("Walls", "wall"), ("Power points", "count")] and saved["items"][0]["size"] == 2.7 and saved["items"][0]["color"] == "#e4572e"
r3 = c.post("/api/projects", data={"files": (io.BytesIO(plain_pdf()), "plan3.pdf")}, content_type="multipart/form-data")
loaded = c.post(f"/api/project/{r3.json['id']}/items/template", json={"template": r.json["id"]}).json
assert loaded["added"] == 2 and loaded["items"][0]["size"] == 2.7, loaded
print("save and reuse: ok")
print("ALL PASSED")
