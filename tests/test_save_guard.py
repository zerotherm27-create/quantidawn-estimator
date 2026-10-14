"""Two browsers saving one project: the second save is refused unless it is based on the latest one."""
from server import app

c = app.test_client()
pid = "boxshall"
c.delete(f"/api/project/{pid}/state")

a = c.put(f"/api/project/{pid}/state", json={"v": 2, "x": "A1", "base": 0}); assert a.status_code == 200, a.json
base_a = a.json["saved"]
print("PASS first save with nothing stored")

# browser B opened before A saved (base 0) and now saves: refused, A's work kept
b = c.put(f"/api/project/{pid}/state", json={"v": 2, "x": "B1", "base": 0})
assert b.status_code == 409 and b.json["error"] == "conflict" and b.json["saved"] == base_a, b.json
assert c.get(f"/api/project/{pid}").json["state"]["x"] == "A1"
print("PASS stale save refused, other work kept")

# A saves again on its own latest stamp: fine
a2 = c.put(f"/api/project/{pid}/state", json={"v": 2, "x": "A2", "base": base_a}); assert a2.status_code == 200
print("PASS save on the latest stamp accepted")

# B chooses to overwrite
f = c.put(f"/api/project/{pid}/state", json={"v": 2, "x": "B2", "base": 0, "force": True}); assert f.status_code == 200
assert c.get(f"/api/project/{pid}").json["state"]["x"] == "B2"
print("PASS forced overwrite accepted")

# older clients send no base: still accepted
o = c.put(f"/api/project/{pid}/state", json={"v": 2, "x": "old"}); assert o.status_code == 200
print("PASS save without a base still works")

c.delete(f"/api/project/{pid}/state")
print("ALL PASSED")
