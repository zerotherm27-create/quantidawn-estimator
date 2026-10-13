"""A small in-memory stand-in for the parts of Supabase the app uses (Storage, the docs table, password sign-in).
It lets the Supabase code path be tested with no network and no account. Not a full Supabase."""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

USERS = {"anna@example.com": "correct horse battery", "unconfirmed@example.com": "correct horse battery"}


class State:
    def __init__(self):
        self.blobs, self.docs, self.tokens = {}, {}, {}   # blobs[(bucket,key)] = (bytes, ctype); docs[(coll,id)] = (data, ts)
        self.requests = []


def make_server():
    st = State()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="application/json"):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def _json(self):
            b = self._body()
            return json.loads(b) if b else {}

        def _route(self):
            u = urlparse(self.path)
            p, q = unquote(u.path), parse_qs(u.query, keep_blank_values=True)
            st.requests.append((self.command, p))
            m = self.command
            token_urls = ("/storage/v1/object/sign/", "/storage/v1/object/upload/sign/")   # work without a key, like the real ones
            if not (self.headers.get("apikey") or self.headers.get("Authorization")) and not (
                    p.startswith(token_urls) and m in ("GET", "PUT")):
                return self._send(401, {"message": "no key"})
            # ---- storage
            r = re.match(r"^/storage/v1/object/authenticated/([^/]+)/(.+)$", p)
            if r and m in ("GET", "HEAD"):
                v = st.blobs.get((r[1], r[2]))
                return self._send(200, v[0], v[1]) if v else self._send(400, {"error": "not_found"})
            r = re.match(r"^/storage/v1/object/upload/sign/([^/]+)/(.+)$", p)
            if r and m == "POST":
                t = f"up-{len(st.tokens)}"
                st.tokens[t] = (r[1], r[2])
                return self._send(200, {"url": f"/object/upload/sign/{r[1]}/{r[2]}?token={t}", "token": t})
            if r and m == "PUT":
                if st.tokens.get(q.get("token", [""])[0]) != (r[1], r[2]):
                    return self._send(401, {"error": "bad token"})
                st.blobs[(r[1], r[2])] = (self._body(), self.headers.get("Content-Type", ""))
                return self._send(200, {"Key": f"{r[1]}/{r[2]}"})
            r = re.match(r"^/storage/v1/object/sign/([^/]+)/(.+)$", p)
            if r and m == "POST":
                if (r[1], r[2]) not in st.blobs:
                    return self._send(400, {"error": "not_found"})
                return self._send(200, {"signedURL": f"/object/sign/{r[1]}/{r[2]}?token=dl"})
            if r and m == "GET":
                v = st.blobs.get((r[1], r[2]))
                return self._send(200, v[0], v[1]) if v and q.get("token") == ["dl"] else self._send(400, {})
            r = re.match(r"^/storage/v1/object/list/([^/]+)$", p)
            if r and m == "POST":
                pre = self._json().get("prefix", "").rstrip("/") + "/"
                seen, out = set(), []
                for (b, k) in st.blobs:
                    if b == r[1] and k.startswith(pre):
                        head = k[len(pre):].split("/")[0]
                        if head in seen:
                            continue
                        seen.add(head)
                        out.append({"name": head, "id": None if "/" in k[len(pre):] else "obj"})
                return self._send(200, out)
            r = re.match(r"^/storage/v1/object/([^/]+)$", p)
            if r and m == "DELETE":
                for k in self._json().get("prefixes", []):
                    st.blobs.pop((r[1], k), None)
                return self._send(200, [])
            r = re.match(r"^/storage/v1/object/([^/]+)/(.+)$", p)
            if r and m == "POST":
                if self.headers.get("x-upsert") != "true" and (r[1], r[2]) in st.blobs:
                    return self._send(400, {"error": "Duplicate"})
                st.blobs[(r[1], r[2])] = (self._body(), self.headers.get("Content-Type", ""))
                return self._send(200, {"Key": f"{r[1]}/{r[2]}"})
            # ---- docs table
            if p == "/rest/v1/qd_docs":
                eq = lambda name: q.get(name, [None])[0][3:] if q.get(name) else None
                if m == "POST":
                    d = self._json()
                    st.docs[(d["coll"], d["id"])] = (d["data"], time.time())
                    return self._send(201)
                coll, did = eq("coll"), eq("id")
                rows = [(k, v) for k, v in st.docs.items() if k[0] == coll and (did is None or k[1] == did)]
                for key in q:
                    if key.startswith("data->>"):
                        f = key[7:]
                        rows = [(k, v) for k, v in rows if str(v[0].get(f)) == q[key][0][3:]]
                if m == "DELETE":
                    for k, _ in rows:
                        st.docs.pop(k)
                    return self._send(204)
                rows.sort(key=lambda kv: kv[1][1], reverse=True)
                return self._send(200, [{"id": k[1], "data": v[0], "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(v[1]))} for k, v in rows])
            if p == "/rest/v1/rpc/qd_bump" and m == "POST":
                d = self._json()
                cur = st.docs.get((d["p_coll"], d["p_id"]))
                if not cur:
                    return self._send(200, None)
                n = int(cur[0].get(d["p_field"], 0))
                if d["p_limit"] > 0 and n >= d["p_limit"]:
                    return self._send(200, None)
                cur[0][d["p_field"]] = n + 1
                return self._send(200, n + 1)
            # ---- auth
            if p == "/auth/v1/token" and m == "POST":
                d = self._json()
                if USERS.get(d.get("email")) == d.get("password"):
                    return self._send(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "user": {"email": d["email"], "id": "u1", "email_confirmed_at": None if d["email"].startswith("unconfirmed") else "2026-01-01T00:00:00Z"}})
                return self._send(400, {"error": "invalid_grant", "error_description": "Invalid login credentials"})
            if p == "/auth/v1/user" and m == "GET":     # Google sign-in tokens: gtok-<email>
                t = (self.headers.get("Authorization") or "")[7:]
                if t.startswith("gtok-"):
                    return self._send(200, {"email": t[5:], "id": "00000000-0000-0000-0000-" + ("%012d" % (len(t[5:]))), "email_confirmed_at": "2026-01-01T00:00:00Z"})
                return self._send(401, {"msg": "bad jwt"})
            if p == "/auth/v1/recover" and m == "POST":
                st.recover = getattr(st, "recover", []) + [self._json().get("email")]
                return self._send(200, {})
            if p == "/auth/v1/user" and m == "PUT":
                t = (self.headers.get("Authorization") or "")[7:]
                st.passwords = getattr(st, "passwords", {}) | {t[5:]: self._json().get("password")}
                return self._send(200, {"email": t[5:]})
            if p == "/rest/v1/admins" and "email=" in self.path:
                em = parse_qs(urlparse(self.path).query).get("email", [""])[0].replace("eq.", "")
                return self._send(200, [{"user_id": "x"}] if em == "boss@example.com" else [])
            if p == "/rest/v1/admins":                  # the website's admins table: one admin whose email is boss@example.com
                uid = (parse_qs(urlparse(self.path).query).get("user_id") or [""])[0].replace("eq.", "")
                return self._send(200, [{"user_id": uid}] if uid.endswith("%012d" % len("boss@example.com")) else [])
            return self._send(404, {"message": f"no route {m} {p}"})

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _route

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, st, f"http://127.0.0.1:{srv.server_address[1]}"
