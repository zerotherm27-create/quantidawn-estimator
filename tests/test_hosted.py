"""Hosted mode end to end: the app on the Supabase backend (local stub), with Supabase Auth plus the team list."""
import os, re, tempfile
import requests

os.environ["QD_DATA"] = tempfile.mkdtemp(prefix="qd-hosted-")
from tests.fake_supabase import make_server
srv, stub, url = make_server()
os.environ.update(SUPABASE_URL=url, SUPABASE_SERVICE_KEY="service-key", SUPABASE_ANON_KEY="anon-key", QD_SECRET_KEY="t" * 32)

import fitz
from core import auth
from core.store import get_store
import server

fails = 0
def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg); fails += (not cond)

assert get_store().kind == "supabase"
c = server.app.test_client()
check(c.get("/api/projects").status_code == 401, "hosted mode is gated without any local users")
csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
def login(email, pw):
    return c.post("/login", data={"username": email, "password": pw, "csrf": csrf, "next": "/"})
check(login("anna@example.com", "correct horse battery").status_code == 401, "valid Supabase account but not on the team list: refused")
auth.team_add("Anna@Example.com")
check(auth.team_list() == ["anna@example.com"], "team list stores the email")
check(login("anna@example.com", "wrong password!!").status_code == 401, "wrong password refused")
check(login("anna@example.com", "correct horse battery").status_code == 302, "team member with the right password signs in")
tok = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").get_data(as_text=True)).group(1)
H = {"X-CSRF-Token": tok}
csp = c.get("/").headers["Content-Security-Policy"]
check(url in csp, "CSP allows the storage host for signed image and upload links")

# Google sign-in and website admins
c2 = server.app.test_client()
r = c2.get("/login/google")
check(r.status_code == 302 and "provider=google" in r.headers["Location"] and "auth%2Fcallback" in r.headers["Location"], "Google button sends the browser to Supabase")
post = lambda t, tok=None: c2.post("/auth/session", json={"access_token": t}, headers={"X-Login-Token": tok if tok is not None else c2.get("/auth/callback") and __import__("re").search(r"'X-Login-Token':'([^']+)'", c2.get("/auth/callback").get_data(as_text=True)).group(1)})
check(post("gtok-stranger@gmail.com").status_code == 403, "Google account that is not a member is refused")
check(c2.post("/auth/session", json={"access_token": "gtok-boss@example.com"}, headers={"X-Login-Token": "wrong"}).status_code == 403, "Google sign-in without the form token is refused")
check(post("gtok-boss@example.com").status_code == 200 and c2.get("/api/projects").status_code == 200, "website admin signs in with Google")
c3 = server.app.test_client(); c3.get("/auth/callback")
auth.team_add("friend@gmail.com")
tk = __import__("re").search(r"'X-Login-Token':'([^']+)'", c3.get("/auth/callback").get_data(as_text=True)).group(1)
check(c3.post("/auth/session", json={"access_token": "gtok-friend@gmail.com"}, headers={"X-Login-Token": tk}).status_code == 200, "team-list member signs in with Google")

# direct upload: init -> PUT to the signed link -> finalize
doc = fitz.open(); doc.new_page(width=842, height=595).insert_text((50, 50), "SCALE 1:100 @A3"); doc.new_page(); pdf = doc.tobytes()
init = c.post("/api/uploads/init", json={"names": ["Plans One.pdf"]}, headers=H).json
check(init["direct"] and init["files"][0]["url"].startswith(url), "init returns a signed upload link")
check(requests.put(init["files"][0]["url"], data=pdf, headers={"Content-Type": "application/pdf"}).status_code in (200, 201), "browser PUT goes straight to storage")
r = c.post("/api/uploads/finalize", json={"id": init["id"], "names": ["Plans One.pdf"]}, headers=H)
check(r.status_code == 200, "finalize reads the stored PDF")
pid = init["id"]
p = c.get(f"/api/project/{pid}").json
check(len(p["pages"]) == 2 and p["name"] == "Plans One", "project saved as a document with 2 pages")
check(c.get("/api/projects").json[-1]["id"] == pid or any(x["id"] == pid for x in c.get("/api/projects").json), "project listed")
check(c.post("/api/uploads/finalize", json={"id": pid, "names": ["Plans One.pdf"]}, headers=H).status_code == 409, "cannot finalize twice")

# lazy page image and tile come back as redirects to signed links, and are then stored
check(not get_store().exists(f"pages/{pid}/p1.png"), "pages are not drawn at upload time")
r = c.get(f"/pages/{pid}/p1.png")
check(r.status_code == 302 and r.headers["Location"].startswith(url) and get_store().exists(f"pages/{pid}/p1.png"), "page drawn on first view, served by signed link")
img = requests.get(r.headers["Location"]).content
check(img[:4] == b"\x89PNG", "the signed link returns the PNG")
r = c.get(f"/tile/{pid}/1?x=0&y=0&w=200&h=150&z=3")
check(r.status_code == 302 and any(k[1].startswith(f"tiles/{pid}/") for k in stub.blobs), "tile rendered once, stored, then redirected")

# state, templates and shares live in the store
check(c.put(f"/api/project/{pid}/state", json={"v": 1}, headers=H).status_code == 200 and c.get(f"/api/project/{pid}").json["state"]["v"] == 1, "saved state round-trips")
from core import shares
tk, meta = shares.create(pid, "Plans One", b"xlsx-bytes", "t.xlsx", 24, 1, "", "anna")
check(c.get(f"/d/{tk}/file").status_code == 200, "client link downloads without a login")
check(c.get(f"/d/{tk}/file").status_code == 410, "download limit enforced atomically")
check(shares.list_for_project(pid)[0]["state"] == "used up", "share listing reads from the database")
print("ALL PASSED" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
