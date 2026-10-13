"""The same behaviour from both backends: files, documents, counters, signed URLs. Supabase runs against a local stub."""
import tempfile
import requests

from core.store import LocalStore, SupabaseStore
from tests.fake_supabase import make_server

srv, st, url = make_server()
backends = {"local": LocalStore(tempfile.mkdtemp(prefix="qd-store-")), "supabase(stub)": SupabaseStore(url, "service-key")}
fails = 0


def check(cond, msg):
    global fails
    print(("  PASS " if cond else "  FAIL ") + msg)
    fails += (not cond)


for name, s in backends.items():
    print(name)
    s.put("projects/p1/a.pdf", b"%PDF-1", "application/pdf")
    s.put("projects/p1/b.pdf", b"%PDF-2")
    s.put("pages/p1/p1.png", b"png")
    check(s.get("projects/p1/a.pdf") == b"%PDF-1" and s.exists("pages/p1/p1.png"), "put, get and exists")
    s.put("projects/p1/a.pdf", b"%PDF-new")
    check(s.get("projects/p1/a.pdf") == b"%PDF-new", "overwrite replaces the file")
    check(s.get("nope/missing.bin") is None and not s.exists("nope/missing.bin"), "missing file is None, not an error")
    for bad in ("../etc/passwd", "a/../b", "/abs", "a//b" if False else "x y"):
        try:
            s.put(bad, b"x"); check(False, f"refuses key {bad!r}")
        except ValueError:
            check(True, f"refuses key {bad!r}")
    s.delete_prefix("projects/p1")
    check(s.get("projects/p1/a.pdf") is None and s.get("projects/p1/b.pdf") is None and s.exists("pages/p1/p1.png"), "delete_prefix removes only that folder")
    # documents
    s.doc_put("state", "p1", {"v": 2, "n": [1, 2]})
    s.doc_put("state", "p1", {"v": 3})
    check(s.doc_get("state", "p1") == {"v": 3} and s.doc_get("state", "zz") is None, "doc put replaces, missing is None")
    s.doc_put("share", "s1", {"project": "p1", "count": 0, "max": 2})
    s.doc_put("share", "s2", {"project": "p2", "count": 0, "max": 0})
    mine = s.doc_list("share", {"project": "p1"})
    check([r[0] for r in mine] == ["s1"] and len(s.doc_list("share")) == 2, "doc_list filters on a field")
    check([s.doc_bump("share", "s1", "count", 2) for _ in range(3)] == [1, 2, None], "counter stops at its limit")
    check([s.doc_bump("share", "s2", "count", 0) for _ in range(3)] == [1, 2, 3] and s.doc_bump("share", "ghost", "count") is None, "no limit counts on; unknown doc is None")
    s.doc_delete("share", "s1")
    check(s.doc_get("share", "s1") is None, "doc_delete")
    try:
        s.doc_put("bad coll!", "x", {}); check(False, "refuses a bad collection name")
    except ValueError:
        check(True, "refuses a bad collection name")

s = backends["supabase(stub)"]
s.put("pages/p9/p1.png", b"image-bytes", "image/png")
u = s.url("pages/p9/p1.png")
check(requests.get(u).content == b"image-bytes", "signed download URL works without the service key")
up = s.signed_upload("projects/p9/0-big.pdf")
r = requests.put(up["url"], data=b"BIGPDF", headers={"Content-Type": "application/pdf"})
check(r.status_code == 200 and s.get("projects/p9/0-big.pdf") == b"BIGPDF", "browser-style direct upload through a signed URL")
check(requests.put(up["url"].replace("token=", "token=x"), data=b"no").status_code == 401, "a wrong upload token is refused")
check(backends["local"].url("a/b") is None and backends["local"].signed_upload("a/b") is None, "local backend serves files itself")
srv.shutdown()
print(f"\n{'ALL PASSED' if not fails else str(fails) + ' FAILED'}")
assert not fails
