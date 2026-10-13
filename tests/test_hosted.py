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

# forgot password / reset
c4 = server.app.test_client()
page_ = c4.get("/forgot").get_data(as_text=True)
fc = re.search(r'name="csrf" value="([^"]+)"', page_).group(1)
check(c4.get("/reset").status_code == 200 and c4.get("/login").get_data(as_text=True).count("/forgot") == 1, "forgot and reset pages load, login links to forgot")
r = c4.post("/forgot", data={"email": "stranger@gmail.com", "csrf": fc})
check("reset link is on its way" in r.get_data(as_text=True) and not getattr(stub, "recover", []), "non-member gets the same message but no email is sent")
r = c4.post("/forgot", data={"email": "boss@example.com", "csrf": fc})
check("reset link is on its way" in r.get_data(as_text=True) and stub.recover == ["boss@example.com"], "website admin gets a reset email")
c4.post("/forgot", data={"email": "friend@gmail.com", "csrf": fc})
check(stub.recover[-1] == "friend@gmail.com", "team-list member gets a reset email")
check("/auth/callback" in c4.get("/auth/callback").get_data(as_text=True) and "/reset" in c4.get("/auth/callback").get_data(as_text=True), "callback sends recovery links on to /reset")
tk4 = re.search(r'window|"X-Login-Token":"([^"]+)"', c4.get("/reset").get_data(as_text=True)).group(1)
sub = lambda tok, pw, h=None: c4.post("/reset/submit", json={"access_token": tok, "password": pw}, headers={"X-Login-Token": h or tk4})
check(sub("gtok-boss@example.com", "short").status_code == 400, "short password refused")
check(sub("gtok-stranger@gmail.com", "a-long-new-password").status_code == 400, "recovery token of a non-member refused")
check(sub("gtok-boss@example.com", "a-long-new-password", "wrong").status_code == 403, "reset without the page token refused")
check(sub("gtok-boss@example.com", "a-long-new-password").status_code == 200 and stub.passwords["boss@example.com"] == "a-long-new-password", "member sets a new password")

# --- audit regressions
auth.team_add("unconfirmed@example.com")
_c6 = server.app.test_client(); _t6 = re.search(r'name="csrf" value="([^"]+)"', _c6.get("/login").get_data(as_text=True)).group(1)
_r = _c6.post("/login", data={"username": "unconfirmed@example.com", "password": "correct horse battery", "csrf": _t6, "next": "/"})
check(_r.status_code == 401, "unconfirmed Supabase account cannot claim a team member's email")
c5 = server.app.test_client()                        # no cookie at all: a made-up form token must not pass
check(c5.post("/login", data={"username": "anna@example.com", "password": "correct horse battery", "csrf": "x"}).status_code == 302 and c5.get("/api/projects").status_code == 401, "login form token 'x' without a session does not pass")
check(c5.post("/reset/submit", json={"access_token": "gtok-boss@example.com", "password": "a-long-new-password"}, headers={"X-Login-Token": "x"}).status_code == 403, "reset with a made-up page token refused")
check(c5.post("/auth/session", json={"access_token": "gtok-boss@example.com"}, headers={"X-Login-Token": "x"}).status_code == 403, "google sign-in with a made-up page token refused")
check(c.get(f"/tile/p-nonexist1/1?x=nan&y=0&w=1&h=1&z=2").status_code in (400, 404), "tile with NaN is a clean 4xx")

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
check(r.status_code == 200 and r.mimetype == "image/png" and "max-age" in r.headers["Cache-Control"] and not any(k[1].startswith(f"tiles/{pid}/") for k in stub.blobs), "small tiles come straight back, browser-cached, and are not stored")

# state, templates and shares live in the store
for q in ("x=nan&y=0&w=100&h=100&z=2", "x=0&y=0&w=inf&h=100&z=2", "x=0&y=0&w=1e308&h=1e308&z=1e-300", "x=0&y=0&w=100&h=100&z=1e308"):
    check(c.get(f"/tile/{pid}/1?{q}").status_code == 400, f"tile {q} refused with 400")
check(c.post(f"/api/project/{pid}/export", json={"lines": [{}]}, headers=H).status_code == 400, "malformed lines give 400, not 500")
check(c.put(f"/api/project/{pid}/state", data="{" + '"a":"' + "x" * 2_100_000 + '"}', content_type="application/json", headers=H).status_code == 413, "oversized saved state refused")
r = c.post("/api/uploads/init", json={"names": ["<img src=x onerror=alert(1)>.pdf"]}, headers=H).json
requests.put(r["files"][0]["url"], data=pdf, headers={"Content-Type": "application/pdf"})
c.post("/api/uploads/finalize", json={"id": r["id"], "names": ["<img src=x onerror=alert(1)>.pdf"]}, headers=H)
pj = c.get(f"/api/project/{r['id']}").json
check("<" not in pj["name"] and "<" not in pj["pages"][0]["n"] and ">" not in pj["name"], "markup in a file name is stripped from project and sheet names")
check(c.post("/api/uploads/init", json={"names": ["a" * 300 + ".pdf"]}, headers=H).status_code == 400, "over-long file name refused")
check(c.post("/api/project/%s/share" % pid, json={"lines": [], "max_downloads": -5}, headers=H).status_code == 400, "share with no lines refused")
check(c.put(f"/api/project/{pid}/state", json={"v": 1}, headers=H).status_code == 200 and c.get(f"/api/project/{pid}").json["state"]["v"] == 1, "saved state round-trips")
from core import shares
tk, meta = shares.create(pid, "Plans One", b"xlsx-bytes", "t.xlsx", 24, 1, "", "anna")
check(c.get(f"/d/{tk}/file").status_code == 200, "client link downloads without a login")
check(c.get(f"/d/{tk}/file").status_code == 410, "download limit enforced atomically")
check(shares.list_for_project(pid)[0]["state"] == "used up", "share listing reads from the database")
print("ALL PASSED" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
