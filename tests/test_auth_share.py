"""Team login, CSRF, throttling, and client download links. Runs against a throwaway data folder."""
import json, os, re, tempfile, time

TMP = tempfile.mkdtemp(prefix="qd-test-")
os.environ["QD_DATA"] = TMP
os.environ.pop("QD_REQUIRE_AUTH", None)

from core import auth, shares            # noqa: E402  (after QD_DATA is set)
from core.project import sample_project  # noqa: E402
import server                            # noqa: E402

app = server.app
ok = lambda cond, msg: (print(("PASS " if cond else "FAIL ") + msg), cond)[1]
results = []
lines = sample_project("boxshall")["lines"]

# 1. no users yet -> open (local use)
c = app.test_client()
results += [ok(c.get("/").status_code == 200 and c.get("/api/projects").status_code == 200, "open while no user exists (local use)")]

# 2. once a user exists, everything except the public bits is behind the login
auth.add_user("anna", "correct horse battery")
c = app.test_client()
r = c.get("/")
results += [ok(r.status_code == 302 and "/login" in r.headers["Location"], "page redirects to /login")]
results += [ok(c.get("/api/projects").status_code == 401, "API answers 401 without a session")]
results += [ok(c.get("/api/project/boxshall").status_code == 401, "project data needs a session")]
results += [ok(c.get("/tile/boxshall/1?x=0&y=0&w=100&h=100&z=2").status_code == 401, "plan tiles need a session")]
results += [ok(c.get("/fonts/fonts.css").status_code == 200 and c.get("/healthz").status_code == 200, "fonts and health check stay public")]
results += [ok(c.get("/d/not-a-real-token").status_code == 404, "unknown download token is 404")]

# 3. login: wrong, right, redirect safety, cookie flags
page = c.get("/login").get_data(as_text=True)
csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
c.get("/favicon.ico"); c.get("/login?next=/favicon.ico")      # what a browser does right after the page loads
results += [ok(c.get("/favicon.ico").status_code == 200 and c.get("/brand/logo-full.png").status_code == 200 and c.get("/brand/..%2fusers.json").status_code == 404, "icon and logo load without signing in, and nothing else under /brand")]
results += [ok(re.search(r'name="csrf" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1) == csrf, "reloading the sign-in page keeps the same form token")]
bad = c.post("/login", data={"username": "anna", "password": "wrong password!", "csrf": csrf, "next": "/"})
results += [ok(bad.status_code == 401 and "not right" in bad.get_data(as_text=True), "wrong password refused")]
good = c.post("/login", data={"username": "Anna", "password": "correct horse battery", "csrf": csrf, "next": "//evil.example"})
cookie = good.headers.get("Set-Cookie", "")
results += [ok(good.status_code == 302 and good.headers["Location"] == "/", "login works and refuses an off-site redirect")]
results += [ok("HttpOnly" in cookie and "SameSite=Lax" in cookie, "session cookie is HttpOnly and SameSite=Lax")]
idx = c.get("/")
tok = re.search(r'name="csrf-token" content="([^"]+)"', idx.get_data(as_text=True)).group(1)
results += [ok(idx.status_code == 200 and 'name="qd-user" content="anna"' in idx.get_data(as_text=True), "signed-in user gets the app")]
results += [ok("Content-Security-Policy" in idx.headers and "frame-ancestors 'none'" in idx.headers["Content-Security-Policy"], "security headers present")]
results += [ok(c.get("/api/projects").status_code == 200, "API works with the session")]

# 4. CSRF on changes
results += [ok(c.put("/api/project/boxshall/state", json={"v": 2}).status_code == 403, "change without CSRF token refused")]
H = {"X-CSRF-Token": tok}
results += [ok(c.put("/api/project/boxshall/state", json={"v": 2}, headers=H).status_code == 200, "change with CSRF token accepted")]
c.delete("/api/project/boxshall/state", headers=H)

# 5. client download links
body = {"lines": lines, "hours": 24, "max_downloads": 1, "note": "test link"}
r = c.post("/api/project/boxshall/share", json=body, headers=H)
j = r.json
token = j["url"].rsplit("/", 1)[1]
results += [ok(r.status_code == 200 and "/d/" in j["url"], "share link created")]
_root = os.path.join(TMP, "blobs", "shares"), os.path.join(TMP, "docs", "share")
stored = "".join(open(os.path.join(d, f), "rb").read().decode("latin-1") for d in _root for f in os.listdir(d))
results += [ok(token not in stored, "the link token is not stored in plain text")]
client = app.test_client()                      # a client: no login at all
land = client.get(f"/d/{token}")
results += [ok(land.status_code == 200 and "takeoff.xlsx" in land.get_data(as_text=True), "landing page opens without a login")]
results += [ok(client.get(f"/d/{token}").status_code == 200 and shares.get_by_token(token)["count"] == 0, "opening the landing page does not use a download")]
dl = client.get(f"/d/{token}/file")
results += [ok(dl.status_code == 200 and dl.data[:2] == b"PK" and "attachment" in dl.headers["Content-Disposition"], "client downloads a real .xlsx")]
results += [ok(client.get(f"/d/{token}/file").status_code == 410, "one-download limit enforced")]
results += [ok(client.get("/api/projects").status_code == 401, "a link gives access to nothing else")]
lst = c.get("/api/project/boxshall/shares").json
results += [ok(lst and lst[0]["state"] == "used up" and lst[0]["count"] == 1, "team sees the link used up")]

# expiry and revocation
r2 = c.post("/api/project/boxshall/share", json={"lines": lines, "hours": 24}, headers=H).json
t2 = r2["url"].rsplit("/", 1)[1]
m = shares.get_by_token(t2); m["expires"] = int(time.time()) - 5; shares._save(m)
results += [ok(client.get(f"/d/{t2}/file").status_code == 410, "expired link refused")]
r3 = c.post("/api/project/boxshall/share", json={"lines": lines, "hours": 24}, headers=H).json
t3 = r3["url"].rsplit("/", 1)[1]
results += [ok(client.get(f"/d/{t3}/file").status_code == 200, "fresh link works")]
c.delete(f"/api/share/{r3['id']}", headers=H)
results += [ok(client.get(f"/d/{t3}/file").status_code == 410, "revoked link refused")]
results += [ok(c.post("/api/project/boxshall/share", json={"source": "template", "template_id": "abcabcabcabc", "lines": lines}, headers=H).status_code == 404, "template share needs a real template")]
results += [ok(c.post("/api/project/boxshall/share", json={"lines": []}, headers=H).status_code == 400, "nothing to share is a clear error")]
results += [ok("no-store" in client.get(f"/d/{t3}").headers.get("Cache-Control", ""), "links are never cached")]

# 6. logout, then guessing protection
c.post("/logout", headers=H)
results += [ok(c.get("/api/projects").status_code == 401, "signed out")]
g = app.test_client()
page = g.get("/login").get_data(as_text=True)
csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
codes = [g.post("/login", data={"username": "anna", "password": f"wrong guess {i}", "csrf": csrf}).status_code for i in range(6)]
right = g.post("/login", data={"username": "anna", "password": "correct horse battery", "csrf": csrf})
results += [ok(codes[:5] == [401] * 5 and codes[5] == 429 and right.status_code == 429, "repeated wrong passwords lock the account for a while")]
try:
    auth.add_user("bob", "short"); results += [ok(False, "short passwords refused")]
except ValueError:
    results += [ok(True, "short passwords refused")]
results += [ok(oct(os.stat(auth.USERS_FILE).st_mode)[-3:] == "600", "users file is private (600)")]

print(f"\n{sum(results)}/{len(results)} checks passed")
assert all(results)
