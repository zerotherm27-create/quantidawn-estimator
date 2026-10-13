"""Everything the app saves goes through here: files ("blobs") and small JSON documents ("docs").

Two backends with the same behaviour:
  LocalStore     plain folders under QD_DATA (default ./data)       - local use and tests
  SupabaseStore  Supabase Storage (one private bucket) + a `qd_docs` table - hosting on Vercel, where there is no disk

The backend is chosen by environment: SUPABASE_URL and SUPABASE_SERVICE_KEY select Supabase.
The service key is a server-side secret: it must never be sent to the browser.
"""
import calendar
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from core.project import DATA

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,300}$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _check_key(key):
    if not KEY_RE.match(key) or ".." in key or key.endswith("/"):
        raise ValueError(f"bad storage key: {key!r}")
    return key


def _check_doc(coll, doc_id):
    if not ID_RE.match(coll) or not ID_RE.match(doc_id):
        raise ValueError("bad document name")


class LocalStore:
    kind = "local"
    _lock = threading.RLock()          # re-entrant: doc_bump calls doc_put

    def __init__(self, root):
        self.root = Path(root)

    # ---- files
    def _blob(self, key):
        return self.root / "blobs" / _check_key(key)

    def put(self, key, data, content_type=None):
        p = self._blob(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key):
        p = self._blob(key)
        return p.read_bytes() if p.exists() else None

    def exists(self, key):
        return self._blob(key).exists()

    def local_path(self, key):
        p = self._blob(key)
        return p if p.exists() else None

    def delete_prefix(self, prefix):
        base = self._blob(prefix.rstrip("/"))
        if base.is_dir():
            for f in sorted(base.rglob("*"), reverse=True):
                f.unlink() if f.is_file() else f.rmdir()
            base.rmdir()
        elif base.exists():
            base.unlink()

    def url(self, key, expires=3600):
        return None                       # the app serves local files itself

    def signed_upload(self, key):
        return None                       # no direct-to-storage upload locally

    # ---- documents
    def _doc(self, coll, doc_id):
        _check_doc(coll, doc_id)
        return self.root / "docs" / coll / f"{doc_id}.json"

    def doc_get(self, coll, doc_id):
        p = self._doc(coll, doc_id)
        return json.loads(p.read_text())["data"] if p.exists() else None

    def doc_put(self, coll, doc_id, data):
        p = self._doc(coll, doc_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps({"data": data, "updated": time.time()}))
            tmp.replace(p)

    def doc_delete(self, coll, doc_id):
        p = self._doc(coll, doc_id)
        if p.exists():
            p.unlink()

    def doc_list(self, coll, where=None):
        """[(id, data, updated_epoch)] newest first; `where` is {field: value} on top-level fields."""
        _check_doc(coll, "x")
        out = []
        base = self.root / "docs" / coll
        if base.exists():
            for f in base.glob("*.json"):
                raw = json.loads(f.read_text())
                if all(raw["data"].get(k) == v for k, v in (where or {}).items()):
                    out.append((f.stem, raw["data"], raw["updated"]))
        return sorted(out, key=lambda r: r[2], reverse=True)

    def doc_bump(self, coll, doc_id, field, limit=0):
        """Add 1 to a counter and return the new value, or None if `limit` (> 0) is already reached. Atomic."""
        with self._lock:
            d = self.doc_get(coll, doc_id)
            if d is None:
                return None
            n = int(d.get(field, 0))
            if limit and n >= limit:
                return None
            d[field] = n + 1
            self.doc_put(coll, doc_id, d)
            return n + 1


class SupabaseStore:
    kind = "supabase"

    def __init__(self, url, service_key, bucket="qd-estimator"):
        import requests
        self.base = url.rstrip("/")
        self.bucket = bucket
        self.s = requests.Session()
        self.s.headers.update({"apikey": service_key, "Authorization": f"Bearer {service_key}"})
        self.timeout = 60

    def _r(self, method, path, ok=(200, 201, 204), **kw):
        r = self.s.request(method, self.base + path, timeout=self.timeout, **kw)
        if r.status_code not in ok:
            raise RuntimeError(f"Supabase {method} {path.split('?')[0]} -> {r.status_code}: {r.text[:200]}")
        return r

    # ---- files (Supabase Storage)
    def _obj(self, key):
        return f"/storage/v1/object/{self.bucket}/{_check_key(key)}"

    def put(self, key, data, content_type=None):
        self._r("POST", self._obj(key), data=data,
                headers={"Content-Type": content_type or "application/octet-stream", "x-upsert": "true"})

    def get(self, key):
        r = self.s.get(self.base + f"/storage/v1/object/authenticated/{self.bucket}/{_check_key(key)}", timeout=self.timeout)
        if r.status_code in (400, 404):
            return None
        if r.status_code != 200:
            raise RuntimeError(f"Supabase GET {key} -> {r.status_code}")
        return r.content

    def exists(self, key):
        r = self.s.head(self.base + f"/storage/v1/object/authenticated/{self.bucket}/{_check_key(key)}", timeout=self.timeout)
        return r.status_code == 200

    def local_path(self, key):
        return None

    def delete_prefix(self, prefix):
        prefix = prefix.rstrip("/")
        names = []
        stack = [prefix]
        while stack:
            p = stack.pop()
            r = self._r("POST", f"/storage/v1/object/list/{self.bucket}", json={"prefix": p, "limit": 1000})
            for it in r.json():
                full = f"{p}/{it['name']}"
                (names if it.get("id") else stack).append(full)
        if self.exists(prefix):
            names.append(prefix)
        for i in range(0, len(names), 100):
            self._r("DELETE", f"/storage/v1/object/{self.bucket}", json={"prefixes": names[i:i + 100]})

    def url(self, key, expires=3600):
        r = self._r("POST", f"/storage/v1/object/sign/{self.bucket}/{_check_key(key)}", json={"expiresIn": expires})
        return self.base + "/storage/v1" + r.json()["signedURL"]

    def signed_upload(self, key):
        """A one-time URL the browser can PUT a file to directly, so big PDFs never pass through the server."""
        r = self._r("POST", f"/storage/v1/object/upload/sign/{self.bucket}/{_check_key(key)}", json={})
        j = r.json()
        rel = j["url"] if j["url"].startswith("/") else "/" + j["url"]
        return {"url": self.base + "/storage/v1" + rel, "method": "PUT", "token": j.get("token")}

    # ---- documents (Postgres table public.qd_docs, reached through PostgREST)
    def doc_get(self, coll, doc_id):
        _check_doc(coll, doc_id)
        r = self._r("GET", f"/rest/v1/qd_docs?coll=eq.{coll}&id=eq.{doc_id}&select=data")
        rows = r.json()
        return rows[0]["data"] if rows else None

    def doc_put(self, coll, doc_id, data):
        _check_doc(coll, doc_id)
        self._r("POST", "/rest/v1/qd_docs?on_conflict=coll,id", json={"coll": coll, "id": doc_id, "data": data,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"})

    def doc_delete(self, coll, doc_id):
        _check_doc(coll, doc_id)
        self._r("DELETE", f"/rest/v1/qd_docs?coll=eq.{coll}&id=eq.{doc_id}")

    def doc_list(self, coll, where=None):
        _check_doc(coll, "x")
        q = f"/rest/v1/qd_docs?coll=eq.{coll}&select=id,data,updated_at&order=updated_at.desc&limit=500"
        for k, v in (where or {}).items():
            if not ID_RE.match(k):
                raise ValueError("bad filter")
            q += f"&data->>{k}=eq.{v}"
        out = []
        for row in self._r("GET", q).json():
            ts = calendar.timegm(time.strptime(row["updated_at"][:19], "%Y-%m-%dT%H:%M:%S"))
            out.append((row["id"], row["data"], ts))
        return out

    def doc_bump(self, coll, doc_id, field, limit=0):
        _check_doc(coll, doc_id)
        r = self._r("POST", "/rest/v1/rpc/qd_bump", json={"p_coll": coll, "p_id": doc_id, "p_field": field, "p_limit": int(limit)})
        v = r.json()
        return v if isinstance(v, int) else None


_store = None


def get_store():
    global _store
    if _store is None:
        url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
        _store = SupabaseStore(url, key, os.environ.get("QD_BUCKET", "qd-estimator")) if url and key else LocalStore(DATA)
    return _store


def set_store(store):
    """For tests: swap the backend."""
    global _store
    _store = store


def blob_file(key):
    """A real file path holding this blob, for libraries that need one (PyMuPDF, openpyxl).
    Local: the stored file itself. Supabase: a cached copy in the temp folder, fetched once per server instance."""
    st = get_store()
    p = st.local_path(key)
    if p:
        return p
    cache = Path(tempfile.gettempdir()) / "qd-cache" / key.replace("/", "__")
    if not cache.exists():
        data = st.get(key)
        if data is None:
            return None
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(cache)
    return cache
